from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from loopguard.control.redaction import redact

from .capability import ContextCapability, ContextTool
from .digest import DigestBuilder
from .handoff import Handoff, HandoffStore
from .journal import ChangeJournal
from .leases import LeaseManager, LeaseScope


_MAX_TOOL_OUTPUT_BYTES = 512 * 1024
_MAX_PATCH_BYTES = 4 * 1024


@dataclass(frozen=True, slots=True)
class ContextServices:
    journal: ChangeJournal
    leases: LeaseManager
    handoffs: HandoffStore
    digest: DigestBuilder
    verification: object | None = None
    symbol_index: object | None = None
    watcher: object | None = None
    worktree_manager: object | None = None
    mcp_launcher: object | None = None


class ContextMCPServer:
    def __init__(self, services: ContextServices, capability: ContextCapability) -> None:
        self.services = services
        self.capability = ContextCapability.model_validate(capability)
        self.fastmcp = FastMCP(
            "LoopGuard Context",
            instructions=(
                "Repository-scoped, cursor-based context. Identities come from a local "
                "capability and cannot be supplied by callers."
            ),
            log_level="ERROR",
        )
        self._handlers: dict[str, Any] = {}
        self._register_tools()

    def invoke_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self._authorize(name)
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError("unknown context tool")
        arguments = arguments or {}
        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError as exc:
            raise ValueError("context tool arguments are invalid") from exc
        return _bounded_output(handler(**arguments))

    def run(self) -> None:
        self.fastmcp.run(transport="stdio")

    def _authorize(self, name: str) -> None:
        if self.capability.expires_at <= datetime.now(timezone.utc):
            raise PermissionError("context capability has expired")
        if name not in self.capability.allowed_tools:
            raise PermissionError("context capability does not allow this tool")

    def _register(self, name: ContextTool, handler: Any) -> None:
        self._handlers[name] = handler
        if name in self.capability.allowed_tools:
            self.fastmcp.tool(name=name, structured_output=True)(handler)

    def _register_tools(self) -> None:
        def get_repo_state() -> dict[str, Any]:
            self._authorize("get_repo_state")
            repo_id = self.capability.repo_id
            checkpoint = self.services.journal.latest_checkpoint(repo_id)
            active = self.services.leases.active(repo_id)
            return _bounded_output(
                {
                    "repo_seq": self.services.journal.latest_cursor(repo_id),
                    "checkpoint": (
                        checkpoint.model_dump(mode="json") if checkpoint is not None else None
                    ),
                    "active_leases": [
                        {
                            "owner_session_id": lease.owner_session_id,
                            "scope": lease.scope.model_dump(mode="json", by_alias=True),
                            "expires_at": lease.expires_at.isoformat(),
                            "enforcement_mode": lease.enforcement_mode,
                        }
                        for lease in active[:100]
                    ],
                    "leases_omitted": max(0, len(active) - 100),
                }
            )

        def get_changes_since(repo_seq: int, limit: int = 50) -> dict[str, Any]:
            self._authorize("get_changes_since")
            if repo_seq < 0 or not 1 <= limit <= 50:
                raise ValueError("change query cursor or limit is invalid")
            records = self.services.journal.since(
                self.capability.repo_id,
                repo_seq=repo_seq,
                limit=limit,
            )
            next_cursor = max([repo_seq, *(record.repo_seq for record in records)])
            latest = self.services.journal.latest_cursor(self.capability.repo_id)
            return _bounded_output(
                {
                    "changes": [_change_payload(record) for record in records],
                    "next_repo_seq": next_cursor,
                    "has_more": latest > next_cursor,
                }
            )

        def get_verification_status(
            verification_id: str | None = None,
        ) -> dict[str, Any]:
            self._authorize("get_verification_status")
            if verification_id is not None:
                return _bounded_output(self._verification_status(verification_id))
            cursor = self.services.journal.latest_cursor(self.capability.repo_id)
            records = self.services.journal.since(
                self.capability.repo_id,
                repo_seq=max(0, cursor - 1_000),
                limit=1_000,
            )
            identifiers = sorted(
                {
                    verification
                    for record in records
                    for verification in record.verification_ids
                }
            )
            return _bounded_output(
                {
                    "verifications": [
                        self._verification_status(identifier) for identifier in identifiers[:50]
                    ],
                    "omitted": max(0, len(identifiers) - 50),
                }
            )

        def claim_work(scopes: list[LeaseScope], ttl_seconds: int = 600) -> dict[str, Any]:
            self._authorize("claim_work")
            if not 1 <= len(scopes) <= 50 or not 1 <= ttl_seconds <= 86_400:
                raise ValueError("work claim size or TTL is invalid")
            parsed = [LeaseScope.model_validate(scope) for scope in scopes]
            results = [
                self.services.leases.acquire(
                    self.capability.repo_id,
                    self.capability.session_id,
                    scope,
                    ttl=timedelta(seconds=ttl_seconds),
                )
                for scope in parsed
            ]
            return _bounded_output(
                {"results": [result.model_dump(mode="json") for result in results]}
            )

        def release_work(scopes: list[LeaseScope] | None = None) -> dict[str, Any]:
            self._authorize("release_work")
            if scopes is not None and len(scopes) > 50:
                raise ValueError("work release exceeds the scope limit")
            parsed = (
                [LeaseScope.model_validate(scope) for scope in scopes]
                if scopes is not None
                else None
            )
            released = self.services.leases.release(
                self.capability.repo_id,
                self.capability.session_id,
                scopes=parsed,
            )
            return {"released": released}

        def create_handoff() -> dict[str, Any]:
            self._authorize("create_handoff")
            repo_id = self.capability.repo_id
            cursor = self.services.journal.latest_cursor(repo_id)
            records = self.services.journal.since(
                repo_id,
                repo_seq=max(0, cursor - 1_000),
                limit=1_000,
            )
            checkpoint = self.services.journal.latest_checkpoint(repo_id)
            changed_paths = sorted({record.path for record in records})
            verification_ids = sorted(
                {
                    verification_id
                    for record in records
                    for verification_id in record.verification_ids
                }
            )
            unresolved = []
            if len(changed_paths) > 200 or len(verification_ids) > 200:
                unresolved.append("Additional exact context remains available through MCP cursors.")
            handoff = Handoff(
                goal="Continue the repository work from the persisted LoopGuard context.",
                accepted_decisions=[],
                changed_paths=changed_paths[:200],
                verification_ids=verification_ids[:200],
                unresolved=unresolved,
                risks=[],
                repo_seq=cursor,
                commit_sha=checkpoint.commit_sha if checkpoint is not None else None,
            )
            artifact = self.services.handoffs.create(
                repo_id,
                self.capability.session_id,
                handoff,
            )
            return _bounded_output(artifact.model_dump(mode="json"))

        def read_handoff(handoff_id: str) -> dict[str, Any]:
            self._authorize("read_handoff")
            artifact = self.services.handoffs.read(self.capability.repo_id, handoff_id)
            return _bounded_output(artifact.model_dump(mode="json"))

        self._register("get_repo_state", get_repo_state)
        self._register("get_changes_since", get_changes_since)
        self._register("get_verification_status", get_verification_status)
        self._register("claim_work", claim_work)
        self._register("release_work", release_work)
        self._register("create_handoff", create_handoff)
        self._register("read_handoff", read_handoff)

    def _verification_status(self, verification_id: str) -> dict[str, Any]:
        if not verification_id.strip() or len(verification_id) > 512 or "\x00" in verification_id:
            raise ValueError("verification ID is invalid")
        verification = self.services.verification
        if verification is None:
            return {"verification_id": verification_id, "status": "unavailable"}
        try:
            metadata = verification.metadata(verification_id)
            if metadata.repository_id != self.capability.repo_id:
                raise KeyError("verification not found")
            run = verification.read(verification_id)
        except KeyError:
            raise
        except Exception as exc:
            raise KeyError("verification not found") from exc
        verdict = getattr(run, "verdict", None)
        return {
            "verification_id": verification_id,
            "status": _enum_value(run.status),
            "verdict": _enum_value(verdict.status) if verdict is not None else None,
            "repo_seq": run.repo_seq,
            "updated_at": run.updated_at.isoformat(),
            "evidence_ids": list(getattr(verdict, "evidence_ids", []))[:100],
        }


def build_context_server(
    services: ContextServices,
    *,
    capability: ContextCapability,
) -> ContextMCPServer:
    return ContextMCPServer(services, capability)


def _change_payload(record: Any) -> dict[str, Any]:
    patch, truncated = _bounded_patch(record.patch)
    return {
        "repo_seq": record.repo_seq,
        "worktree_id": record.worktree_id,
        "path": record.path,
        "actor": record.actor,
        "before_hash": record.before_hash,
        "after_hash": record.after_hash,
        "symbols": sorted(record.symbols)[:100],
        "symbols_omitted": max(0, len(record.symbols) - 100),
        "verification_ids": sorted(record.verification_ids)[:100],
        "verifications_omitted": max(0, len(record.verification_ids) - 100),
        "patch": patch,
        "patch_truncated": truncated,
    }


def _bounded_patch(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    redacted = str(redact(value))
    encoded = redacted.encode("utf-8")
    if len(encoded) <= _MAX_PATCH_BYTES:
        return redacted, False
    return encoded[:_MAX_PATCH_BYTES].decode("utf-8", errors="ignore"), True


def _bounded_output(value: dict[str, Any]) -> dict[str, Any]:
    cleaned = redact(value)
    encoded = json.dumps(cleaned, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_TOOL_OUTPUT_BYTES:
        raise ValueError("context tool output exceeds the safe response limit")
    return cleaned


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
