from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from loopguard.context.git_runtime import resolve_git_runtime
from loopguard.control.decisions import ActionTarget, PolicyDecision, TargetKind
from loopguard.control.events import ControlEvent, EventKind

from .baseline import capture_repository_snapshot
from .intake import IntakeMode, MutationGate, ProofIntakeService, is_mutating_tool
from .manifest import ArtifactContext
from .models import (
    CheckPhase,
    ProofContract,
    RetentionClass,
    RunStatus,
    VerificationRun,
    VerificationVerdict,
    VerdictStatus,
)
from .plugins import ChangeRecord
from .runner import CheckExecution, ExecutionAuthorization
from .service import VerificationService


class CompletionEnforcement(StrEnum):
    BLOCK = "block"
    OBSERVE = "observe"
    MANAGED = "managed"


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    allow: bool
    verified: bool
    severity: Literal["success", "warning", "error"]
    status: VerdictStatus
    message: str
    fingerprint: str


class VerificationReader(Protocol):
    def read(self, run_id: str) -> VerificationRun: ...


class VerificationRunner(Protocol):
    async def run(
        self,
        spec,
        *,
        authorization: ExecutionAuthorization,
    ) -> CheckExecution: ...


@dataclass(frozen=True, slots=True)
class _WorkflowSession:
    run_id: str
    repository: Path
    mode: IntakeMode


class _Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    enforcement: CompletionEnforcement


class _LedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: dict[str, _Binding] = Field(default_factory=dict)
    repair_attempts: dict[str, int] = Field(default_factory=dict)
    repair_events: dict[str, int] = Field(default_factory=dict)


class _LedgerEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: _LedgerState
    mac: str


class CompletionLedger:
    """Small private restart-safe ledger for bindings and bounded repair continuations."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        integrity_key: bytes | None = None,
    ) -> None:
        self.path = path.expanduser().absolute() if path is not None else None
        if self.path is not None and self.path.is_symlink():
            raise ValueError("completion ledger cannot be a symlink")
        if self.path is not None and not integrity_key:
            raise ValueError("persistent completion ledger requires an integrity key")
        self._integrity_key = (
            hashlib.sha256(
                b"loopguard-completion-ledger-integrity-v1\0" + integrity_key
            ).digest()
            if integrity_key
            else None
        )
        self._lock = threading.RLock()
        self._state = self._load()

    def bind(
        self,
        session_id: str,
        run_id: str,
        enforcement: CompletionEnforcement,
    ) -> None:
        if not session_id.strip() or not run_id.strip():
            raise ValueError("completion binding IDs must not be empty")
        with self._lock:
            self._state.bindings[session_id] = _Binding(
                run_id=run_id,
                enforcement=CompletionEnforcement(enforcement),
            )
            self._persist()

    def binding(self, session_id: str) -> _Binding | None:
        with self._lock:
            return self._state.bindings.get(session_id)

    def claim_repair_attempt(self, fingerprint: str, event_id: str) -> int:
        """Return the stable one-based attempt for an idempotent completion event."""
        if not fingerprint or not event_id:
            raise ValueError("repair attempt identity must not be empty")
        event_key = hashlib.sha256(
            f"loopguard-repair-event-v1\0{fingerprint}\0{event_id}".encode()
        ).hexdigest()
        with self._lock:
            existing_attempt = self._state.repair_events.get(event_key)
            if existing_attempt is not None:
                return existing_attempt
            attempt = self._state.repair_attempts.get(fingerprint, 0) + 1
            self._state.repair_attempts[fingerprint] = attempt
            self._state.repair_events[event_key] = attempt
            self._prune_events()
            self._persist()
            return attempt

    def _load(self) -> _LedgerState:
        if self.path is None or not self.path.exists():
            return _LedgerState()
        try:
            envelope = _LedgerEnvelope.model_validate_json(self.path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError("completion ledger is invalid") from exc
        expected = self._mac(envelope.state)
        if not hmac.compare_digest(envelope.mac, expected):
            raise ValueError("completion ledger integrity check failed")
        return envelope.state

    def _persist(self) -> None:
        if self.path is None:
            return
        path = self.path
        if path.exists() and path.is_symlink():
            raise ValueError("completion ledger cannot be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.tmp-",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(
                    _LedgerEnvelope(
                        state=self._state,
                        mac=self._mac(self._state),
                    ).model_dump_json().encode()
                )
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _prune_events(self) -> None:
        maximum = 10_000
        excess = len(self._state.repair_events) - maximum
        if excess <= 0:
            return
        for event_key in list(self._state.repair_events)[:excess]:
            self._state.repair_events.pop(event_key, None)

    def _mac(self, state: _LedgerState) -> str:
        if self._integrity_key is None:
            return "memory-only"
        canonical = json.dumps(
            state.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hmac.new(self._integrity_key, canonical, hashlib.sha256).hexdigest()


class CompletionEnforcer:
    def __init__(
        self,
        verification: VerificationReader,
        *,
        ledger: CompletionLedger | None = None,
        max_automatic_repairs: int = 2,
    ) -> None:
        if max_automatic_repairs < 0:
            raise ValueError("automatic repair limit must not be negative")
        self.verification = verification
        self.ledger = ledger or CompletionLedger()
        self.max_automatic_repairs = max_automatic_repairs

    def bind(
        self,
        session_id: str,
        run_id: str,
        *,
        enforcement: CompletionEnforcement = CompletionEnforcement.BLOCK,
    ) -> None:
        self.ledger.bind(session_id, run_id, CompletionEnforcement(enforcement))

    def policy_decision(
        self,
        event: ControlEvent,
        core_decision: PolicyDecision,
    ) -> PolicyDecision:
        if event.kind is not EventKind.TURN_COMPLETED:
            return core_decision
        binding = self.ledger.binding(event.session.session_id)
        if binding is None:
            return core_decision
        run: VerificationRun | None
        try:
            run = self.verification.read(binding.run_id)
            verdict = _run_verdict(run, session_id=event.session.session_id)
            metadata_reader = getattr(self.verification, "metadata", None)
            if callable(metadata_reader):
                metadata = metadata_reader(binding.run_id)
                if getattr(metadata, "repository_id", None) != event.session.repo_id:
                    verdict = _untrusted_binding_verdict(run)
        except Exception:
            run = None
            verdict = VerificationVerdict(status=VerdictStatus.INCONCLUSIVE)
        completion = stop_decision(verdict, enforcement=binding.enforcement)
        action: Literal["allow", "warn", "pause", "inject"]
        outcome: Literal["complete", "warn", "continue", "human_required"]
        repair_attempt = 0
        if completion.verified:
            action = "allow"
            outcome = "complete"
        elif binding.enforcement is CompletionEnforcement.OBSERVE:
            action = "warn"
            outcome = "warn"
        elif binding.enforcement is CompletionEnforcement.MANAGED:
            repair_attempt = self.ledger.claim_repair_attempt(
                completion.fingerprint,
                event.event_id,
            )
            if repair_attempt <= self.max_automatic_repairs:
                action = "inject"
                outcome = "continue"
            else:
                action = "pause"
                outcome = "human_required"
        else:
            action = "pause"
            outcome = "human_required"
        return PolicyDecision(
            decision_id=(
                f"verification:{event.session.session_id}:"
                f"{core_decision.state_version}:{completion.fingerprint[:16]}"
            ),
            action=action,
            reason=completion.message,
            target=ActionTarget(
                kind=TargetKind.SESSION,
                target_id=event.session.session_id,
            ),
            state_version=core_decision.state_version,
            state_hash=core_decision.state_hash,
            metadata={
                **core_decision.metadata,
                "completion": {
                    "run_id": run.run_id if run is not None else binding.run_id,
                    "status": completion.status.value,
                    "verified": completion.verified,
                    "severity": completion.severity,
                    "fingerprint": completion.fingerprint,
                    "outcome": outcome,
                    "repair_attempt": repair_attempt,
                    "max_automatic_repairs": self.max_automatic_repairs,
                    "evidence_ids": list(verdict.evidence_ids),
                },
            },
        )


class VerificationWorkflow:
    """Coordinates prompt ownership, mutation ordering, proof execution, and completion."""

    def __init__(
        self,
        *,
        proof_intake: ProofIntakeService,
        runner: VerificationRunner,
        verification: VerificationService,
        completion: CompletionEnforcer,
        authorization_factory: Callable[[str], ExecutionAuthorization],
    ) -> None:
        self.proof_intake = proof_intake
        self.runner = runner
        self.verification = verification
        self.completion = completion
        self.authorization_factory = authorization_factory
        self._sessions: dict[str, _WorkflowSession] = {}

    async def submit_prompt(
        self,
        event: ControlEvent,
        contract: ProofContract,
        *,
        repository: Path,
        mode: IntakeMode = IntakeMode.ATTACHED_BLOCK,
    ) -> str:
        if event.kind is not EventKind.PROMPT_SUBMITTED:
            raise ValueError("verification intake requires a prompt event")
        prompt = event.payload.get("input_text")
        if not isinstance(prompt, str):
            raise ValueError("prompt event is missing bounded input text")
        _validate_prompt_owner(event, contract, prompt)
        repo = repository.expanduser().resolve(strict=True)
        run_id = self.verification.start(
            contract,
            repository_id=event.session.repo_id,
        )
        self.verification.begin_baselining(run_id)
        try:
            await self.proof_intake.submit_prompt(
                event.session.session_id,
                contract,
                repository=repo,
                raw_prompt=prompt,
            )
        except Exception:
            self.verification.fail(run_id)
            raise
        resolved_mode = IntakeMode(mode)
        self._sessions[event.session.session_id] = _WorkflowSession(
            run_id=run_id,
            repository=repo,
            mode=resolved_mode,
        )
        enforcement = (
            CompletionEnforcement.MANAGED
            if resolved_mode is IntakeMode.MANAGED
            else CompletionEnforcement.BLOCK
        )
        self.completion.bind(
            event.session.session_id,
            run_id,
            enforcement=enforcement,
        )
        return run_id

    async def attach_after_mutation(
        self,
        event: ControlEvent,
        contract: ProofContract,
        *,
        repository: Path,
        observed_mutation_id: str,
        mode: IntakeMode = IntakeMode.ATTACHED_WARN,
    ) -> str:
        if event.kind is not EventKind.PROMPT_SUBMITTED:
            raise ValueError("late attachment requires its owning prompt event")
        prompt = event.payload.get("input_text")
        if not isinstance(prompt, str):
            raise ValueError("prompt event is missing bounded input text")
        _validate_prompt_owner(event, contract, prompt)
        repo = repository.expanduser().resolve(strict=True)
        run_id = self.verification.start(contract, repository_id=event.session.repo_id)
        self.verification.begin_baselining(run_id)
        await self.proof_intake.record_late_attach(
            event.session.session_id,
            contract,
            repository=repo,
            observed_mutation_id=observed_mutation_id,
        )
        self.verification.activate_without_baseline(run_id)
        resolved_mode = IntakeMode(mode)
        self._sessions[event.session.session_id] = _WorkflowSession(
            run_id=run_id,
            repository=repo,
            mode=resolved_mode,
        )
        self.completion.bind(
            event.session.session_id,
            run_id,
            enforcement=CompletionEnforcement.OBSERVE,
        )
        return run_id

    async def before_mutation(self, event: ControlEvent) -> MutationGate:
        if event.kind is not EventKind.TOOL_CALL:
            raise ValueError("mutation gate requires a tool-call event")
        tool_name = event.payload.get("tool_name")
        arguments = event.payload.get("arguments", {})
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            raise ValueError("tool event is missing normalized mutation input")
        if not is_mutating_tool(tool_name, arguments):
            return MutationGate(True, "read_only", "tool_is_non_mutating")
        session = self._session(event.session.session_id)
        gate = await self.proof_intake.before_mutation(
            event.session.session_id,
            event.event_id,
            mode=session.mode,
        )
        if gate.baseline is not None:
            run = self.verification.read(session.run_id)
            if run.status is RunStatus.BASELINING:
                self.verification.record_baseline(session.run_id, gate.baseline)
        elif gate.allow and self.verification.read(session.run_id).status is RunStatus.BASELINING:
            self.verification.activate_without_baseline(session.run_id)
        return gate

    async def verify_completion(
        self,
        session_id: str,
        *,
        acceptance_evidence: Sequence[bytes] = (),
        journal_changes: Sequence[ChangeRecord] | None = None,
    ) -> VerificationRun:
        session = self._session(session_id)
        run = self.verification.read(session.run_id)
        if run.status is not RunStatus.ACTIVE:
            return run
        snapshot = capture_repository_snapshot(session.repository)
        canonical_changes = canonical_repository_changes(session.repository)
        iteration_changes = list(journal_changes) if journal_changes is not None else canonical_changes
        change_payload = json.dumps(
            {
                "canonical_repository_changes": [
                    change.model_dump(mode="json") for change in canonical_changes
                ],
                "iteration_changes": [
                    change.model_dump(mode="json") for change in iteration_changes
                ],
                "iteration_source": (
                    "change_journal" if journal_changes is not None else "git_fallback"
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        change_manifest = self.verification.add_artifact(
            session.run_id,
            change_payload,
            ArtifactContext(
                repository_id=self.verification.metadata(session.run_id).repository_id,
                worktree_hash=snapshot.worktree_hash,
                verification_id=session.run_id,
                command_id="change-set",
                result_id=hashlib.sha256(change_payload).hexdigest(),
                media_type="application/json",
                retention_class=RetentionClass.SUMMARIES,
            ),
        )
        del change_manifest
        acceptance_ids: list[str] = []
        for index, evidence in enumerate(acceptance_evidence):
            if not evidence:
                continue
            manifest = self.verification.add_artifact(
                session.run_id,
                evidence,
                ArtifactContext(
                    repository_id=self.verification.metadata(session.run_id).repository_id,
                    worktree_hash=snapshot.worktree_hash,
                    verification_id=session.run_id,
                    command_id="acceptance",
                    result_id=hashlib.sha256(
                        f"acceptance:{index}:".encode() + evidence
                    ).hexdigest(),
                    media_type="text/plain",
                    retention_class=RetentionClass.SUMMARIES,
                ),
            )
            acceptance_ids.append(manifest.artifact_id)
        self.verification.begin_completion(session.run_id)
        try:
            checks = [
                check
                for check in run.contract.checks
                if check.required and check.phase in {CheckPhase.COMPLETION, CheckPhase.PR}
            ]
            for check in checks:
                self.verification.mark_command_started(
                    session.run_id,
                    check.id,
                    worktree_hash=snapshot.worktree_hash,
                )
                try:
                    execution = await self.runner.run(
                        check,
                        authorization=self.authorization_factory(check.id),
                    )
                    self.verification.record_execution(session.run_id, execution)
                finally:
                    self.verification.mark_command_finished(session.run_id, check.id)
            return self.verification.complete(
                session.run_id,
                acceptance_evidence_ids=acceptance_ids,
            )
        except Exception:
            current = self.verification.read(session.run_id)
            if current.status is RunStatus.COMPLETING:
                self.verification.fail(session.run_id)
            raise

    def is_registered(self, session_id: str) -> bool:
        return session_id in self._sessions

    def _session(self, session_id: str) -> _WorkflowSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError("verification session is not registered") from exc


def canonical_repository_changes(repository: Path) -> list[ChangeRecord]:
    """Derive a bounded, deterministic Git change set without requiring Context."""
    repo = repository.expanduser().resolve(strict=True)
    runtime = resolve_git_runtime()
    if runtime is None or not (repo / ".git").exists():
        raise ValueError("canonical changes require a Git worktree")
    git = runtime.executable
    try:
        result = subprocess.run(
            [git, "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=10,
            env=runtime.environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("canonical Git change discovery failed") from exc
    if result.returncode != 0 or len(result.stdout) > 64 * 1024 * 1024:
        raise ValueError("canonical Git change discovery failed")
    entries = result.stdout.split(b"\0")
    changes: dict[str, ChangeRecord] = {}
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            raise ValueError("Git returned an invalid change record")
        status = entry[:2].decode("ascii", errors="strict")
        raw_path = entry[3:]
        if "R" in status or "C" in status:
            if index >= len(entries) or not entries[index]:
                raise ValueError("Git returned an incomplete rename record")
            index += 1
        path = os.fsdecode(raw_path).replace(os.sep, "/")
        deleted = "D" in status
        change = ChangeRecord(path=path, deleted=deleted)
        after_hash = None
        candidate = repo / Path(change.path)
        if not deleted and candidate.is_file() and not candidate.is_symlink():
            after_hash = _hash_file(candidate)
        changes[change.path] = change.model_copy(update={"after_hash": after_hash})
        if len(changes) > 100_000:
            raise ValueError("canonical Git change set exceeded its limit")
    return [changes[path] for path in sorted(changes)]


def stop_decision(
    verdict: VerificationVerdict,
    *,
    enforcement: CompletionEnforcement = CompletionEnforcement.BLOCK,
) -> CompletionDecision:
    enforcement = CompletionEnforcement(enforcement)
    status = verdict.status
    verified = status in {
        VerdictStatus.VERIFIED,
        VerdictStatus.VERIFIED_WITH_PREEXISTING_FAILURES,
    }
    severity: Literal["success", "warning", "error"]
    if status is VerdictStatus.VERIFIED:
        severity = "success"
        message = f"Verified with {len(verdict.evidence_ids)} signed evidence artifact(s)."
    elif status is VerdictStatus.VERIFIED_WITH_PREEXISTING_FAILURES:
        severity = "success"
        message = (
            "Verified with unchanged pre-existing failures: "
            + _items(verdict.remaining_preexisting_failures)
        )
    elif status is VerdictStatus.REGRESSION:
        severity = "error"
        message = "Verification found new failure(s): " + _items(
            verdict.introduced_failures
        )
    elif status is VerdictStatus.CHECKS_PASSED_UNBASELINED:
        severity = "warning"
        message = (
            "Checks passed, but baseline ownership or pre-mutation ordering is unproven; "
            "completion is inconclusive."
        )
    elif status is VerdictStatus.INCOMPLETE:
        severity = "warning"
        message = "Verification is incomplete; missing required checks: " + _items(
            verdict.missing_required_checks
        )
    elif status is VerdictStatus.ORPHANED:
        severity = "error"
        message = "Verification was orphaned during recovery and cannot prove completion."
    else:
        severity = "warning"
        message = "Verification is inconclusive and cannot prove completion."
    allow = verified or enforcement is CompletionEnforcement.OBSERVE
    if enforcement is CompletionEnforcement.OBSERVE and not verified:
        severity = "warning"
    return CompletionDecision(
        allow=allow,
        verified=verified,
        severity=severity,
        status=status,
        message=message,
        fingerprint=_fingerprint(verdict),
    )


def _run_verdict(
    run: VerificationRun,
    *,
    session_id: str | None = None,
) -> VerificationVerdict:
    if (
        session_id is not None
        and run.baseline is not None
        and run.baseline.owning_session_id != session_id
    ):
        return VerificationVerdict(
            status=VerdictStatus.INCONCLUSIVE,
            missing_required_checks=[
                check.id for check in run.contract.checks if check.required
            ],
            evidence_ids=[artifact.artifact_id for artifact in run.evidence],
        )
    if run.verdict is not None:
        return run.verdict
    status = (
        VerdictStatus.ORPHANED
        if run.status is RunStatus.ORPHANED
        else VerdictStatus.INCOMPLETE
    )
    return VerificationVerdict(
        status=status,
        missing_required_checks=[check.id for check in run.contract.checks if check.required],
        evidence_ids=[artifact.artifact_id for artifact in run.evidence],
    )


def _untrusted_binding_verdict(run: VerificationRun) -> VerificationVerdict:
    return VerificationVerdict(
        status=VerdictStatus.INCONCLUSIVE,
        missing_required_checks=[check.id for check in run.contract.checks if check.required],
        evidence_ids=[artifact.artifact_id for artifact in run.evidence],
    )


def _validate_prompt_owner(
    event: ControlEvent,
    contract: ProofContract,
    prompt: str,
) -> None:
    if contract.source_event_id != event.event_id:
        raise ValueError("proof contract source does not match the prompt event")
    if hashlib.sha256(prompt.encode()).hexdigest() != contract.source_prompt_hash:
        raise ValueError("proof contract does not match the source prompt")


def _fingerprint(verdict: VerificationVerdict) -> str:
    payload = json.dumps(
        verdict.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"loopguard-verification-fingerprint-v1\0{payload}".encode()).hexdigest()


def _items(values: list[str]) -> str:
    if not values:
        return "none reported"
    shown = values[:5]
    suffix = f" (+{len(values) - len(shown)} more)" if len(values) > len(shown) else ""
    return ", ".join(shown) + suffix


def _hash_file(path: Path) -> str:
    if path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("changed file exceeded the hashing limit")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
