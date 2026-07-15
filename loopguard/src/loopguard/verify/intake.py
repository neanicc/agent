from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, TypeAdapter

from .models import Baseline, CheckPhase, CheckStatus, ProofContract


class IntakeMode(StrEnum):
    MANAGED = "managed"
    ATTACHED_WARN = "attached_warn"
    ATTACHED_BLOCK = "attached_block"


class BaselineCapture(Protocol):
    async def capture(
        self,
        contract: ProofContract,
        *,
        repository: Path,
        owning_session_id: str,
    ) -> Baseline: ...


class _IntakeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    contract: ProofContract
    repository: Path
    baseline: Baseline | None = None
    first_mutation_id: str | None = None
    late_attach: bool = False
    status: str = "intake"


@dataclass(frozen=True, slots=True)
class MutationGate:
    allow: bool
    status: str
    reason: str
    baseline: Baseline | None = None


class ProofIntakeService:
    def __init__(
        self,
        baseline_service: BaselineCapture,
        *,
        journal_path: Path | None = None,
    ) -> None:
        self.baseline_service = baseline_service
        self.journal_path = journal_path
        self._lock = asyncio.Lock()
        self._captures: dict[str, asyncio.Task[Baseline]] = {}
        self._records = self._load_journal(journal_path)

    async def submit_prompt(
        self,
        session_id: str,
        contract: ProofContract,
        *,
        repository: Path,
        raw_prompt: str | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session ID must not be empty")
        if raw_prompt is not None:
            observed_hash = hashlib.sha256(raw_prompt.encode()).hexdigest()
            if observed_hash != contract.source_prompt_hash:
                raise ValueError("proof contract does not match the source prompt")
        repo = repository.expanduser().resolve(strict=True)
        async with self._lock:
            existing = self._captures.pop(session_id, None)
            if existing is not None:
                existing.cancel()
            self._records[session_id] = _IntakeRecord(
                session_id=session_id,
                contract=contract,
                repository=repo,
            )
            self._persist_journal()

    async def record_late_attach(
        self,
        session_id: str,
        contract: ProofContract,
        *,
        repository: Path,
        observed_mutation_id: str,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session ID must not be empty")
        if not observed_mutation_id.strip():
            raise ValueError("observed mutation ID must not be empty")
        repo = repository.expanduser().resolve(strict=True)
        async with self._lock:
            self._records[session_id] = _IntakeRecord(
                session_id=session_id,
                contract=contract,
                repository=repo,
                first_mutation_id=observed_mutation_id,
                late_attach=True,
                status="inconclusive",
            )
            self._persist_journal()

    async def before_mutation(
        self,
        session_id: str,
        mutation_id: str,
        *,
        mode: IntakeMode,
    ) -> MutationGate:
        mode = IntakeMode(mode)
        if not mutation_id.strip():
            raise ValueError("mutation ID must not be empty")
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return self._unproven_gate(mode, "missing_prompt_intake")
            if record.late_attach:
                return self._unproven_gate(mode, "attached_after_first_mutation")
            if record.baseline is not None:
                if record.first_mutation_id is None:
                    record.first_mutation_id = mutation_id
                    record.status = "active"
                    self._persist_journal()
                return MutationGate(True, "active", "trusted_baseline_captured", record.baseline)
            capture = self._captures.get(session_id)
            if capture is None:
                record.status = "baselining"
                self._persist_journal()
                capture = asyncio.create_task(
                    self.baseline_service.capture(
                        record.contract,
                        repository=record.repository,
                        owning_session_id=session_id,
                    )
                )
                self._captures[session_id] = capture
            contract_hash = _contract_identity(record.contract)
        try:
            baseline = await asyncio.shield(capture)
        except asyncio.CancelledError:
            async with self._lock:
                current = self._records.get(session_id)
                if current is not None and _contract_identity(current.contract) != contract_hash:
                    return self._unproven_gate(mode, "prompt_replaced_during_baseline")
            raise
        except Exception:
            async with self._lock:
                if self._captures.get(session_id) is capture:
                    self._captures.pop(session_id, None)
                current = self._records.get(session_id)
                if current is not None:
                    current.status = "inconclusive"
                    self._persist_journal()
            return self._unproven_gate(mode, "baseline_capture_failed")
        async with self._lock:
            current = self._records.get(session_id)
            if current is None or _contract_identity(current.contract) != contract_hash:
                return self._unproven_gate(mode, "prompt_replaced_during_baseline")
            if self._captures.get(session_id) is capture:
                self._captures.pop(session_id, None)
            if (
                baseline.source_prompt_event_id != current.contract.source_event_id
                or not baseline.captured_before_first_mutation
            ):
                current.status = "inconclusive"
                self._persist_journal()
                return self._unproven_gate(mode, "baseline_ordering_unproven")
            if not _baseline_results_are_complete(current.contract, baseline):
                current.status = "inconclusive"
                self._persist_journal()
                return self._unproven_gate(mode, "baseline_checks_incomplete")
            current.baseline = baseline
            if current.first_mutation_id is None:
                current.first_mutation_id = mutation_id
            current.status = "active"
            self._persist_journal()
            return MutationGate(True, "active", "trusted_baseline_captured", baseline)

    def record(self, session_id: str) -> dict[str, object] | None:
        record = self._records.get(session_id)
        return record.model_dump(mode="json") if record is not None else None

    def _unproven_gate(self, mode: IntakeMode, reason: str) -> MutationGate:
        if mode is IntakeMode.MANAGED or mode is IntakeMode.ATTACHED_BLOCK:
            return MutationGate(False, "baseline_failed", reason)
        return MutationGate(True, "inconclusive", reason)

    def _load_journal(self, path: Path | None) -> dict[str, _IntakeRecord]:
        if path is None:
            return {}
        if path.is_symlink():
            raise ValueError("intake journal cannot be a symlink")
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text())
            records = TypeAdapter(list[_IntakeRecord]).validate_python(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("intake journal is invalid") from exc
        if len({record.session_id for record in records}) != len(records):
            raise ValueError("intake journal contains duplicate sessions")
        if any(not _record_is_consistent(record) for record in records):
            raise ValueError("intake journal contains inconsistent proof state")
        return {record.session_id: record for record in records}

    def _persist_journal(self) -> None:
        if self.journal_path is None:
            return
        path = self.journal_path
        if path.exists() and path.is_symlink():
            raise ValueError("intake journal cannot be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = [record.model_dump(mode="json") for record in self._records.values()]
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.tmp-",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def is_mutating_tool(tool_name: str, tool_input: dict[str, object]) -> bool:
    normalized = tool_name.strip().lower()
    if normalized in {"read", "glob", "grep", "websearch", "webfetch", "view_image"}:
        return False
    if normalized in {"apply_patch", "write", "edit", "notebookedit", "multiedit"}:
        return True
    if normalized not in {"bash", "shell", "exec", "command"}:
        return bool(normalized)
    command = tool_input.get("command", tool_input.get("cmd"))
    # Shell-capable tools are mutating unless the integration can prove otherwise.
    # Missing or malformed input must fail closed at the first-mutation gate.
    return not isinstance(command, str) or bool(command.strip())


def _contract_identity(contract: ProofContract) -> str:
    return hashlib.sha256(contract.model_dump_json().encode()).hexdigest()


def _baseline_results_are_complete(contract: ProofContract, baseline: Baseline) -> bool:
    required = {
        check.id
        for check in contract.checks
        if check.required
        and check.phase in {CheckPhase.BASELINE, CheckPhase.COMPLETION, CheckPhase.PR}
    }
    results = {result.check_id: result for result in baseline.results}
    if not required.issubset(results):
        return False
    return all(
        results[check_id].status in {CheckStatus.PASSED, CheckStatus.FAILED}
        for check_id in required
    )


def _record_is_consistent(record: _IntakeRecord) -> bool:
    baseline = record.baseline
    if baseline is None:
        return record.status != "active"
    return (
        baseline.source_prompt_event_id == record.contract.source_event_id
        and baseline.captured_before_first_mutation
        and _baseline_results_are_complete(record.contract, baseline)
        and record.status == "active"
        and record.first_mutation_id is not None
    )
