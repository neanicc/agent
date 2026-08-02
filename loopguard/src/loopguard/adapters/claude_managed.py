from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import secrets
import sqlite3
import stat
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from loopguard.adapters.base import (
    AdapterCapabilities,
    AdapterFailure,
    Capability,
    LifecycleError,
    LifecycleErrorCode,
    ManagedRunRequest,
    ManagedStartEvidence,
)
from loopguard.adapters.claude_bridge import ClaudeBridgeClient
from loopguard.adapters.jsonrpc import (
    JsonRpcError,
    JsonRpcProcessExited,
    JsonRpcProtocolError,
    JsonRpcTimeout,
)
from loopguard.control.decisions import ActionKind, ActionRequest, ActionTarget, TargetKind
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.paths import loopguard_home
from loopguard.control.redaction import redact


_PROTOCOL_VERSION = 1
_MAX_CONTEXT_BYTES = 64 * 1024
_MAX_QUEUE = 256
_MAX_EVENT_IDS = 2_048
_APPROVAL_TTL = timedelta(minutes=5)
_ACTIVE = frozenset({"active", "interrupting", "requires_action"})
_EVENT_KINDS = {kind.value: kind for kind in EventKind}
_SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    worktree_root TEXT NOT NULL,
    proof_contract_id TEXT NOT NULL,
    baseline_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
    auth_provider TEXT NOT NULL,
    turn_id TEXT,
    state_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
PRAGMA user_version = 1;
"""


class ClaudeAuthentication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["anthropic-api", "bedrock", "vertex", "foundry"]
    source: Literal["environment", "workload-identity"]

    @model_validator(mode="before")
    @classmethod
    def _reject_subscription_credentials(cls, value: Any) -> Any:
        if isinstance(value, dict) and "subscription" in str(value.get("provider", "")).lower():
            raise ValueError("claude.ai subscription authentication is not supported")
        return value


class ClaudeBridge(Protocol):
    def set_message_handler(self, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None: ...

    async def request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any: ...

    async def close(self) -> None: ...


StartAuthorizer = Callable[
    [ManagedRunRequest], ManagedStartEvidence | None | Awaitable[ManagedStartEvidence | None]
]
ClaudeStatus = Literal[
    "active",
    "between_turns",
    "interrupting",
    "requires_action",
    "recovering",
    "orphaned",
    "terminal",
]


@dataclass(slots=True)
class _ClaudeSession:
    ref: SessionRef
    worktree_root: Path
    proof_contract_id: str
    baseline_id: str
    lease_id: str
    auth_provider: str
    status: ClaudeStatus
    state_version: int
    lock: asyncio.Lock
    events: asyncio.Queue[ControlEvent | AdapterFailure]
    actions: asyncio.Queue[ActionRequest]
    event_ids: deque[str]
    event_id_set: set[str]


@dataclass(frozen=True, slots=True)
class _PendingPermission:
    permission_id: str
    action_id: str


class ClaudeManagedAdapter:
    capabilities = AdapterCapabilities(
        surface="claude-agent-sdk",
        supported={
            Capability.OBSERVE,
            Capability.START_MANAGED,
            Capability.INJECT_CONTEXT,
            Capability.INTERRUPT,
            Capability.SELECT_MODEL,
            Capability.SELECT_EFFORT,
            Capability.APPROVE_TOOL,
        },
    )

    def __init__(
        self,
        *,
        bridge: ClaudeBridge | None = None,
        authorize_start: StartAuthorizer | None = None,
        authentication: ClaudeAuthentication | None | Literal["detect"] = "detect",
        state_path: Path | None = None,
        start_timeout: float = 30.0,
        control_timeout: float = 10.0,
    ) -> None:
        if not 1 <= start_timeout <= 300:
            raise ValueError("start timeout must be between 1 and 300 seconds")
        if not 0.1 <= control_timeout <= 120:
            raise ValueError("control timeout must be between 0.1 and 120 seconds")
        self._bridge = bridge
        self._authorize_start = authorize_start
        self.authentication = (
            detect_claude_authentication() if authentication == "detect" else authentication
        )
        self._start_timeout = start_timeout
        self._control_timeout = control_timeout
        self._bridge_lock = asyncio.Lock()
        self._sessions: dict[str, _ClaudeSession] = {}
        self._permissions: dict[str, _PendingPermission] = {}
        self._closed = False
        self._store = _ClaudeStore(state_path or loopguard_home() / "claude-managed.sqlite3")
        for row in self._store.load_all():
            session = self._from_record(row)
            self._sessions[session.ref.session_id] = session
        if self._bridge is not None:
            self._bridge.set_message_handler(self.handle_bridge_message)

    async def start(self, request: ManagedRunRequest) -> SessionRef | AdapterFailure:
        if self.authentication is None:
            return _failure(
                LifecycleErrorCode.STALE_STATE,
                "managed Claude requires an API or documented cloud-provider credential",
            )
        evidence = await self._authorize(request)
        evidence_error = _validate_evidence(request, evidence)
        if evidence_error is not None:
            return _failure(LifecycleErrorCode.STALE_STATE, evidence_error)
        assert evidence is not None
        try:
            bridge = await self._ensure_bridge()
            result = await bridge.request(
                "start",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "cwd": str(request.worktree_root),
                    "model": request.model,
                    "effort": request.effort,
                    "prompt": request.prompt,
                    "permissionMode": _permission_mode(request.permission_policy),
                    "sandboxPolicy": request.sandbox,
                    "maxBudgetUsd": request.max_cost_usd,
                    "maxTokens": request.max_tokens,
                    "authProvider": self.authentication.provider,
                },
                timeout=self._start_timeout,
            )
            session_id, validation_error = _start_result(result, request, self.authentication)
            if validation_error is not None:
                session_id = result.get("sessionId") if isinstance(result, dict) else None
                if isinstance(session_id, str):
                    await bridge.request(
                        "close",
                        {"protocolVersion": _PROTOCOL_VERSION, "sessionId": session_id},
                        timeout=self._control_timeout,
                    )
                return _failure(LifecycleErrorCode.PROTOCOL_VERSION, validation_error)
            assert session_id is not None
        except (JsonRpcError, OSError) as exc:
            return _bridge_failure(exc)
        ref = SessionRef(
            host_id="local",
            repo_id=request.repository_id,
            session_id=session_id,
            worktree_id=request.worktree_id,
        )
        managed = _ClaudeSession(
            ref=ref,
            worktree_root=request.worktree_root,
            proof_contract_id=request.proof_contract_id,
            baseline_id=evidence.baseline_id,
            lease_id=evidence.worktree_lease_id,
            auth_provider=self.authentication.provider,
            status="active",
            state_version=1,
            lock=asyncio.Lock(),
            events=asyncio.Queue(maxsize=_MAX_QUEUE),
            actions=asyncio.Queue(maxsize=_MAX_QUEUE),
            event_ids=deque(),
            event_id_set=set(),
        )
        if session_id in self._sessions:
            return _failure(LifecycleErrorCode.PROTOCOL_VERSION, "bridge reused a session id")
        self._sessions[session_id] = managed
        self._persist(managed)
        await self._emit(managed, EventKind.SESSION_STARTED, {"managed": True})
        return managed.ref

    async def attach(self, session: SessionRef) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        if managed.status != "recovering":
            return None
        managed.status = "orphaned"
        managed.state_version += 1
        self._persist(managed)
        return _failure(
            LifecycleErrorCode.STALE_STATE,
            "in-flight Claude SDK query cannot be safely reattached; no duplicate turn was started",
            session.session_id,
        )

    async def interrupt(self, session: SessionRef) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        async with managed.lock:
            if managed.status not in {"active", "requires_action"}:
                return _stale(session.session_id)
            try:
                bridge = await self._ensure_bridge()
                await bridge.request(
                    "interrupt",
                    {"protocolVersion": _PROTOCOL_VERSION, "sessionId": session.session_id},
                    timeout=self._control_timeout,
                )
            except (JsonRpcError, OSError) as exc:
                return _bridge_failure(exc, session.session_id)
            managed.status = "interrupting"
            managed.state_version += 1
            self._persist(managed)
            return None

    async def inject(self, session: SessionRef, context: str) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        error = _validate_context(context)
        if error is not None:
            return _failure(LifecycleErrorCode.PROTOCOL_VERSION, error, session.session_id)
        async with managed.lock:
            if managed.status != "between_turns":
                return _stale(session.session_id)
            try:
                bridge = await self._ensure_bridge()
                await bridge.request(
                    "inject",
                    {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "sessionId": session.session_id,
                        "text": context,
                    },
                    timeout=self._control_timeout,
                )
            except (JsonRpcError, OSError) as exc:
                return _bridge_failure(exc, session.session_id)
            managed.status = "active"
            managed.state_version += 1
            self._persist(managed)
            return None

    async def resolve_action(
        self, session: SessionRef, action: ActionRequest
    ) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        pending = self._permissions.get(action.action_id)
        if pending is None or action.target.target_id != session.session_id:
            return _stale(session.session_id)
        if action.expires_at <= datetime.now(timezone.utc):
            self._permissions.pop(action.action_id, None)
            return _stale(session.session_id)
        try:
            action.validate_state(
                current_state_version=managed.state_version,
                current_state_hash=_state_hash(managed),
            )
        except ValueError:
            return _stale(session.session_id)
        behavior = (
            "allow" if action.kind in {ActionKind.APPROVE, ActionKind.CONTINUE_ONCE} else "deny"
        )
        try:
            bridge = await self._ensure_bridge()
            await bridge.request(
                "resolve_permission",
                {
                    "sessionId": session.session_id,
                    "permissionId": pending.permission_id,
                    "behavior": behavior,
                },
                timeout=self._control_timeout,
            )
        except (JsonRpcError, OSError) as exc:
            return _bridge_failure(exc, session.session_id)
        self._permissions.pop(action.action_id, None)
        managed.status = "active"
        managed.state_version += 1
        self._persist(managed)
        await self._emit(
            managed,
            EventKind.ACTION_RESOLVED,
            {"action_id": action.action_id, "behavior": behavior},
        )
        return None

    async def next_action(self, session: SessionRef) -> ActionRequest:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            raise LookupError(managed.message)
        return await managed.actions.get()

    async def handle_bridge_message(self, message: dict[str, Any]) -> None:
        if message.get("method") != "event":
            raise JsonRpcProtocolError("Claude bridge emitted an unsupported message")
        params = message.get("params")
        if not isinstance(params, dict) or params.get("protocolVersion") != _PROTOCOL_VERSION:
            raise JsonRpcProtocolError("Claude bridge protocol version is unsupported")
        session_id = params.get("sessionId")
        managed = self._sessions.get(session_id) if isinstance(session_id, str) else None
        if managed is None:
            raise ValueError("Claude bridge event targets an unknown session")
        event = params.get("event")
        if not isinstance(event, dict):
            raise JsonRpcProtocolError("Claude bridge event payload must be an object")
        async with managed.lock:
            if managed.status == "terminal":
                raise ValueError("Claude bridge emitted an event after terminal state")
            event_id = event.get("eventId")
            if not isinstance(event_id, str) or not event_id or len(event_id) > 256:
                raise JsonRpcProtocolError("Claude bridge event id is invalid")
            _remember_event(managed, event_id)
            kind_value = event.get("kind")
            kind = _EVENT_KINDS.get(kind_value) if isinstance(kind_value, str) else None
            if kind is None:
                raise JsonRpcProtocolError("Claude bridge event kind is unsupported")
            turn_id = event.get("turnId")
            if turn_id is not None and (not isinstance(turn_id, str) or len(turn_id) > 256):
                raise JsonRpcProtocolError("Claude bridge turn id is invalid")
            payload = _bounded_payload(event.get("payload", {}))
            if kind is EventKind.ACTION_REQUESTED:
                await self._handle_permission(managed, payload, event_id=event_id)
                return
            if kind is EventKind.TURN_COMPLETED:
                managed.ref = managed.ref.model_copy(update={"turn_id": None})
                managed.status = "between_turns"
                managed.state_version += 1
                self._persist(managed)
            elif kind in {EventKind.SESSION_STOPPED, EventKind.PIPELINE_FAILED}:
                managed.ref = managed.ref.model_copy(update={"turn_id": None})
                managed.status = "terminal"
                managed.state_version += 1
                self._persist(managed)
            elif turn_id is not None:
                managed.ref = managed.ref.model_copy(update={"turn_id": turn_id})
            await self._emit(managed, kind, payload, event_id=event_id)

    async def events(self, session: SessionRef) -> AsyncIterator[ControlEvent | AdapterFailure]:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            yield managed
            return
        while not self._closed:
            yield await managed.events.get()

    def session_status(self, session_id: str) -> str | None:
        managed = self._sessions.get(session_id)
        return managed.status if managed is not None else None

    def phase_switch_safe(self, session_id: str) -> bool:
        managed = self._sessions.get(session_id)
        return bool(
            managed is not None
            and managed.status == "between_turns"
            and managed.ref.turn_id is None
        )

    def mark_route_orphaned(self, session_id: str) -> None:
        managed = self._sessions.get(session_id)
        if managed is None:
            return
        managed.status = "orphaned"
        managed.state_version += 1
        self._persist(managed)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._bridge is not None:
            await self._bridge.close()
        self._store.close()

    async def _authorize(self, request: ManagedRunRequest) -> ManagedStartEvidence | None:
        if self._authorize_start is None:
            return None
        value = self._authorize_start(request)
        return await value if inspect.isawaitable(value) else value

    async def _ensure_bridge(self) -> ClaudeBridge:
        if self._bridge is not None:
            return self._bridge
        async with self._bridge_lock:
            if self._bridge is None:
                self._bridge = await ClaudeBridgeClient.launch(self.handle_bridge_message)
            return self._bridge

    def _owned(self, session: SessionRef) -> _ClaudeSession | LifecycleError:
        managed = self._sessions.get(session.session_id)
        if managed is None or managed.ref.repo_id != session.repo_id:
            return LifecycleError.for_code(
                LifecycleErrorCode.UNKNOWN_SESSION,
                surface=self.capabilities.surface,
                session_id=session.session_id,
            )
        if session.worktree_id != managed.ref.worktree_id:
            return _stale(session.session_id)
        return managed

    async def _handle_permission(
        self,
        managed: _ClaudeSession,
        payload: dict[str, Any],
        *,
        event_id: str,
    ) -> None:
        permission_id = payload.get("permissionId")
        if not isinstance(permission_id, str) or not permission_id or len(permission_id) > 256:
            raise JsonRpcProtocolError("Claude permission id is invalid")
        action_id = (
            "claude:"
            + hashlib.sha256(f"{managed.ref.session_id}\0{permission_id}".encode()).hexdigest()[:32]
        )
        managed.status = "requires_action"
        managed.state_version += 1
        self._persist(managed)
        action = ActionRequest(
            action_id=action_id,
            target=ActionTarget(kind=TargetKind.SESSION, target_id=managed.ref.session_id),
            kind=ActionKind.APPROVE,
            parameters={
                "permission_id": permission_id,
                "tool_name": payload.get("toolName"),
                "input": payload.get("input", {}),
                "reason": payload.get("reason"),
            },
            expected_state_version=managed.state_version,
            expected_state_hash=_state_hash(managed),
            nonce=secrets.token_urlsafe(24),
            expires_at=datetime.now(timezone.utc) + _APPROVAL_TTL,
        )
        self._permissions[action_id] = _PendingPermission(permission_id, action_id)
        await _queue_put(managed.actions, action)
        await self._emit(
            managed,
            EventKind.ACTION_REQUESTED,
            {"action_request": action.model_dump(mode="json")},
            event_id=event_id,
        )

    async def _emit(
        self,
        managed: _ClaudeSession,
        kind: EventKind,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        await _queue_put(
            managed.events,
            ControlEvent(
                event_id=event_id or "claude:" + secrets.token_hex(16),
                kind=kind,
                source=self.capabilities.surface,
                session=managed.ref,
                payload=payload,
            ),
        )

    def _persist(self, managed: _ClaudeSession) -> None:
        self._store.save(managed)

    def _from_record(self, row: sqlite3.Row) -> _ClaudeSession:
        status = str(row["status"])
        if status in _ACTIVE:
            status = "recovering"
            self._store.mark_status(str(row["session_id"]), status)
        return _ClaudeSession(
            ref=SessionRef(
                host_id="local",
                repo_id=str(row["repo_id"]),
                session_id=str(row["session_id"]),
                worktree_id=str(row["worktree_id"]),
                turn_id=str(row["turn_id"]) if row["turn_id"] is not None else None,
            ),
            worktree_root=Path(str(row["worktree_root"])),
            proof_contract_id=str(row["proof_contract_id"]),
            baseline_id=str(row["baseline_id"]),
            lease_id=str(row["lease_id"]),
            auth_provider=str(row["auth_provider"]),
            status=status,  # type: ignore[arg-type]
            state_version=int(row["state_version"]),
            lock=asyncio.Lock(),
            events=asyncio.Queue(maxsize=_MAX_QUEUE),
            actions=asyncio.Queue(maxsize=_MAX_QUEUE),
            event_ids=deque(),
            event_id_set=set(),
        )


class _ClaudeStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError("Claude session state path cannot be a symlink")
        if self.path.parent.resolve(strict=True) != self.path.parent:
            raise ValueError("Claude session state path cannot contain a symlink")
        if os.name == "posix":
            parent = os.lstat(self.path.parent)
            if parent.st_uid != os.getuid():
                raise ValueError("Claude session state parent must be owner controlled")
            os.chmod(self.path.parent, 0o700)
        descriptor = os.open(
            self.path,
            os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if os.name == "posix":
                status = os.fstat(descriptor)
                if status.st_uid != os.getuid() or not stat.S_ISREG(status.st_mode):
                    raise ValueError("Claude session database must be an owner file")
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self._connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self._connection.row_factory = sqlite3.Row
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        objects = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        if version == 0:
            if objects:
                raise ValueError("unversioned Claude session database is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            raise ValueError("Claude session database schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        _secure_sqlite_files(self.path)

    def load_all(self) -> list[sqlite3.Row]:
        return list(self._connection.execute("SELECT * FROM sessions ORDER BY session_id"))

    def save(self, session: _ClaudeSession) -> None:
        self._connection.execute(
            """
            INSERT INTO sessions(
                session_id, repo_id, worktree_id, worktree_root, proof_contract_id,
                baseline_id, lease_id, auth_provider, turn_id, state_version, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                turn_id=excluded.turn_id, state_version=excluded.state_version,
                status=excluded.status, updated_at=excluded.updated_at
            """,
            (
                session.ref.session_id,
                session.ref.repo_id,
                session.ref.worktree_id,
                str(session.worktree_root),
                session.proof_contract_id,
                session.baseline_id,
                session.lease_id,
                session.auth_provider,
                session.ref.turn_id,
                session.state_version,
                session.status,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _secure_sqlite_files(self.path)

    def mark_status(self, session_id: str, status: str) -> None:
        self._connection.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, datetime.now(timezone.utc).isoformat(), session_id),
        )

    def close(self) -> None:
        self._connection.close()


def detect_claude_authentication(
    environment: Mapping[str, str] | None = None,
) -> ClaudeAuthentication | None:
    env = os.environ if environment is None else environment
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return None
    if env.get("ANTHROPIC_API_KEY"):
        return ClaudeAuthentication(provider="anthropic-api", source="environment")
    if _truthy(env.get("CLAUDE_CODE_USE_BEDROCK")):
        return ClaudeAuthentication(provider="bedrock", source="environment")
    if _truthy(env.get("CLAUDE_CODE_USE_VERTEX")):
        return ClaudeAuthentication(provider="vertex", source="environment")
    if _truthy(env.get("CLAUDE_CODE_USE_FOUNDRY")):
        return ClaudeAuthentication(provider="foundry", source="environment")
    return None


def _start_result(
    result: Any, request: ManagedRunRequest, authentication: ClaudeAuthentication
) -> tuple[str | None, str | None]:
    if not isinstance(result, dict) or result.get("protocolVersion") != _PROTOCOL_VERSION:
        return None, "Claude bridge start response has an unsupported protocol version"
    session_id = result.get("sessionId")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        return None, "Claude bridge start response is missing a bounded session id"
    if result.get("authProvider") != authentication.provider:
        return None, "Claude bridge authenticated with a different provider"
    models = result.get("models")
    if not isinstance(models, list):
        return None, "Claude bridge did not report supported models"
    selected = next(
        (
            model
            for model in models
            if isinstance(model, dict)
            and request.model in {model.get("value"), model.get("resolvedModel")}
        ),
        None,
    )
    if selected is None:
        return None, f"requested model {request.model!r} is not advertised by Claude"
    efforts = selected.get("supportedEffortLevels")
    if selected.get("supportsEffort") is not True or not isinstance(efforts, list):
        return None, "selected Claude model does not advertise effort support"
    if request.effort not in efforts:
        return None, f"requested effort {request.effort!r} is not advertised by Claude"
    return session_id, None


def _validate_evidence(
    request: ManagedRunRequest, evidence: ManagedStartEvidence | None
) -> str | None:
    if evidence is None:
        return "managed start requires proof, baseline, and worktree lease evidence"
    if evidence.proof_contract_id != request.proof_contract_id:
        return "managed start proof contract does not match the request"
    if evidence.worktree_id != request.worktree_id:
        return "managed start worktree lease does not match the request"
    if evidence.worktree_root.resolve() != request.worktree_root.resolve():
        return "managed start worktree root does not match the lease"
    if not evidence.captured_before_first_mutation:
        return "managed start baseline ordering is unproven"
    return None


def _permission_mode(policy: str) -> str:
    mapping = {"on-request": "default", "never": "dontAsk", "plan": "plan"}
    try:
        return mapping[policy]
    except KeyError as exc:
        raise JsonRpcProtocolError(f"unsupported Claude permission policy: {policy!r}") from exc


def _bounded_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JsonRpcProtocolError("Claude bridge event payload must be an object")
    sanitized = redact(value)
    assert isinstance(sanitized, dict)
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > _MAX_CONTEXT_BYTES:
        return {"truncated": True, "sha256": hashlib.sha256(encoded).hexdigest()}
    return sanitized


def _validate_context(value: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "injected context must be non-empty"
    if len(value.encode()) > _MAX_CONTEXT_BYTES:
        return "injected context exceeds the 64 KiB limit"
    return None


def _remember_event(session: _ClaudeSession, event_id: str) -> None:
    if event_id in session.event_id_set:
        raise JsonRpcProtocolError("Claude bridge emitted a duplicate event id")
    if len(session.event_ids) >= _MAX_EVENT_IDS:
        oldest = session.event_ids.popleft()
        session.event_id_set.remove(oldest)
    session.event_ids.append(event_id)
    session.event_id_set.add(event_id)


def _state_hash(session: _ClaudeSession) -> str:
    raw = (
        f"{session.ref.session_id}\0{session.ref.turn_id or ''}\0"
        f"{session.status}\0{session.state_version}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def _queue_put(queue: asyncio.Queue[Any], value: Any) -> None:
    try:
        queue.put_nowait(value)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(value)


def _failure(
    code: LifecycleErrorCode, message: str, session_id: str | None = None
) -> LifecycleError:
    return LifecycleError(
        code=code,
        message=message[:512],
        surface="claude-agent-sdk",
        session_id=session_id,
        retryable=code is LifecycleErrorCode.TIMEOUT,
    )


def _stale(session_id: str) -> LifecycleError:
    return LifecycleError.for_code(
        LifecycleErrorCode.STALE_STATE,
        surface="claude-agent-sdk",
        session_id=session_id,
    )


def _bridge_failure(exc: BaseException, session_id: str | None = None) -> LifecycleError:
    if isinstance(exc, JsonRpcTimeout):
        code = LifecycleErrorCode.TIMEOUT
    elif isinstance(exc, (JsonRpcProcessExited, OSError)):
        code = LifecycleErrorCode.PROCESS_EXITED
    else:
        code = LifecycleErrorCode.PROTOCOL_VERSION
    return _failure(code, str(exc), session_id)


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _secure_sqlite_files(path: Path) -> None:
    if os.name != "posix":
        return
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.chmod(0o600)


__all__ = [
    "ClaudeAuthentication",
    "ClaudeManagedAdapter",
    "ManagedStartEvidence",
    "detect_claude_authentication",
]
