from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from loopguard.control.crypto import (
    KeyStore,
    MissingKeyError,
    PlatformKeyStore,
    generate_data_key,
)

from .artifacts import (
    ArtifactIntegrityError,
    ArtifactKeyError,
    ArtifactStore,
    ArtifactUnavailableError,
)
from .manifest import ArtifactContext, ArtifactManifest
from .models import (
    Baseline,
    CheckResult,
    EvidenceArtifact,
    ProofContract,
    RunStatus,
    VerificationVerdict,
    VerificationRun,
    VerdictStatus,
)
from .store import VerificationMetadata, VerificationStore
from .verdict import derive_verdict

if TYPE_CHECKING:
    from loopguard.preferences.models import PreferenceVerdict
    from loopguard.preferences.service import PreferenceService

    from .runner import CheckExecution


_TERMINAL = {
    RunStatus.COMPLETED,
    RunStatus.CANCELLED,
    RunStatus.FAILED,
    RunStatus.ORPHANED,
}


class VerificationService:
    def __init__(
        self,
        store: VerificationStore,
        artifacts: ArtifactStore,
        *,
        preferences: PreferenceService | None = None,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.preferences = preferences
        self._lock = threading.RLock()
        self.artifacts.prune_unreferenced(self.store.artifact_ids())
        for artifact_id in self.store.artifact_ids():
            if self.store.live_manifest_count(artifact_id) == 0:
                self.artifacts.shred(artifact_id, authorized=True)

    @classmethod
    def open(
        cls,
        *,
        home: Path,
        key_store: KeyStore | None = None,
        preferences: PreferenceService | None = None,
    ) -> VerificationService:
        resolved_home = home.expanduser().absolute()
        if resolved_home.is_symlink():
            raise ValueError("verification home cannot be a symlink")
        resolved_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved_home.chmod(0o700)
        database = resolved_home / "verification.db"
        artifact_root = resolved_home / "verification.artifacts"
        keys = key_store or PlatformKeyStore(
            service_name="dev.loopguard.verification-proof-store"
        )
        credential_id = hashlib.sha256(
            b"loopguard-verification-credential-v1\0" + str(resolved_home).encode()
        ).hexdigest()
        existing_state = database.exists() or artifact_root.exists()
        try:
            key = keys.get(credential_id)
        except MissingKeyError:
            if existing_state:
                raise
            key = generate_data_key()
            keys.put(credential_id, key)
        artifacts = ArtifactStore(artifact_root, key_material=key)
        return cls(
            VerificationStore(database, integrity_key=key),
            artifacts,
            preferences=preferences,
        )

    @classmethod
    def for_path(
        cls,
        path: Path,
        *,
        key: bytes = b"loopguard-verification-test-key",
        preferences: PreferenceService | None = None,
    ) -> VerificationService:
        """Open an isolated test store with explicitly injected deterministic key material."""
        database = path.expanduser().absolute()
        artifacts = ArtifactStore.for_test(
            database.parent / f"{database.stem}.artifacts",
            key=key,
        )
        return cls(
            VerificationStore(database, integrity_key=key),
            artifacts,
            preferences=preferences,
        )

    def start(
        self,
        contract: ProofContract,
        *,
        repository_id: str,
        repo_seq: int = 0,
        session_seq: int = 0,
        preference_profile_id: str | None = None,
    ) -> str:
        if not repository_id.strip():
            raise ValueError("repository ID must not be empty")
        if preference_profile_id is not None:
            if self.preferences is None:
                raise ValueError("preference service is required for a preference profile")
            self.preferences.profile(preference_profile_id)
        now = datetime.now(timezone.utc)
        run_id = f"verify-{uuid.uuid4().hex}"
        run = VerificationRun(
            run_id=run_id,
            contract=contract,
            status=RunStatus.CREATED,
            created_at=now,
            updated_at=now,
            repo_seq=repo_seq,
            session_seq=session_seq,
            preference_profile_id=preference_profile_id,
        )
        metadata = VerificationMetadata(
            run_id=run_id,
            contract_hash=_hash_model(contract.model_dump(mode="json")),
            repository_id=repository_id.strip(),
            repository_sha="0" * 40,
            worktree_hash="0" * 64,
            command_hash=_command_hash(contract),
            repo_seq=repo_seq,
            session_seq=session_seq,
            revision=0,
        )
        self.store.create(run, metadata)
        return run_id

    def begin_baselining(self, run_id: str) -> VerificationRun:
        return self._transition(run_id, {RunStatus.CREATED}, RunStatus.BASELINING)

    def record_baseline(self, run_id: str, baseline: Baseline) -> VerificationRun:
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            _require_status(run, {RunStatus.BASELINING})
            check_ids = {check.id for check in run.contract.checks}
            if any(result.check_id not in check_ids for result in baseline.results):
                raise ValueError("baseline contains a check outside the proof contract")
            if any(
                result.worktree_hash != baseline.worktree_hash
                for result in baseline.results
            ):
                raise ValueError("baseline result worktree does not match the snapshot")
            if any(
                manifest.command_id in {result.check_id for result in baseline.results}
                and manifest.worktree_hash != baseline.worktree_hash
                for manifest in self.store.manifests_for_run(run_id)
            ):
                raise ValueError("baseline artifact worktree does not match the snapshot")
            updated = run.model_copy(
                update={
                    "baseline": baseline,
                    "status": RunStatus.ACTIVE,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self.store.replace(
                updated,
                metadata.model_copy(
                    update={
                        "repository_sha": baseline.repository_sha,
                        "worktree_hash": baseline.worktree_hash,
                    }
                ),
            )
            return updated

    def activate_without_baseline(self, run_id: str) -> VerificationRun:
        """Continue an attached run while preserving that baseline ownership is absent."""
        return self._transition(run_id, {RunStatus.BASELINING}, RunStatus.ACTIVE)

    def begin_completion(self, run_id: str) -> VerificationRun:
        return self._transition(run_id, {RunStatus.ACTIVE}, RunStatus.COMPLETING)

    def add_result(self, run_id: str, result: CheckResult) -> VerificationRun:
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            _require_status(run, {RunStatus.ACTIVE, RunStatus.COMPLETING})
            if result.check_id not in {check.id for check in run.contract.checks}:
                raise ValueError("result check is not part of the proof contract")
            if len(run.results) >= 4096:
                raise ValueError("verification result limit exceeded")
            if metadata.inflight_command_id is not None and (
                metadata.inflight_command_id != result.check_id
                or metadata.worktree_hash != result.worktree_hash
            ):
                raise ValueError("result does not match the in-flight command snapshot")
            updated = run.model_copy(
                update={
                    "results": [*run.results, result],
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self.store.replace(updated, metadata)
            return updated

    def add_preference_verdict(
        self,
        run_id: str,
        verdict: PreferenceVerdict,
    ) -> VerificationRun:
        from loopguard.preferences.models import PreferenceVerdict

        candidate = PreferenceVerdict.model_validate(verdict)
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            _require_status(run, {RunStatus.ACTIVE, RunStatus.COMPLETING})
            if self.preferences is None or run.preference_profile_id is None:
                raise ValueError("verification run has no preference policy snapshot")
            if candidate.artifact_id not in {
                evidence.artifact_id for evidence in run.evidence
            }:
                raise ValueError("preference verdict artifact is not evidence for this run")
            if candidate.verdict_id in run.preference_verdict_ids:
                existing = self.preferences.verdict(candidate.verdict_id)
                if existing != candidate:
                    raise ValueError("preference verdict replay has conflicting semantics")
                return run
            if len(run.preference_verdict_ids) >= 1_024:
                raise ValueError("verification preference verdict limit exceeded")
            self.preferences.record_verdict(run.preference_profile_id, candidate)
            updated = run.model_copy(
                update={
                    "preference_verdict_ids": [
                        *run.preference_verdict_ids,
                        candidate.verdict_id,
                    ],
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self.store.replace(updated, metadata)
            return updated

    def record_execution(
        self,
        run_id: str,
        execution: CheckExecution,
        *,
        now: datetime | None = None,
    ) -> CheckResult:
        persisted_result = self._persist_execution(run_id, execution, now=now)
        self.add_result(run_id, persisted_result)
        return persisted_result

    def record_baseline_execution(
        self,
        run_id: str,
        execution: CheckExecution,
        *,
        now: datetime | None = None,
    ) -> CheckResult:
        run = self.read(run_id)
        _require_status(run, {RunStatus.BASELINING})
        return self._persist_execution(run_id, execution, now=now)

    def _persist_execution(
        self,
        run_id: str,
        execution: CheckExecution,
        *,
        now: datetime | None,
    ) -> CheckResult:
        metadata = self.store.metadata(run_id)
        payload = (
            b"LoopGuard command stdout\n"
            + execution.stdout
            + b"\nLoopGuard command stderr\n"
            + execution.stderr
        )
        result_id = hashlib.sha256(
            b"loopguard-verification-result-v1\0"
            + execution.result.check_id.encode()
            + b"\0"
            + execution.result.started_at.isoformat().encode()
        ).hexdigest()
        manifest = self.add_artifact(
            run_id,
            payload,
            ArtifactContext(
                repository_id=metadata.repository_id,
                worktree_hash=execution.result.worktree_hash,
                verification_id=run_id,
                command_id=execution.result.check_id,
                result_id=result_id,
                media_type="text/plain",
                retention_class="raw_logs",
            ),
            now=now,
        )
        persisted_result = execution.result.model_copy(
            update={"artifact_ids": [manifest.artifact_id]}
        )
        return persisted_result

    def add_artifact(
        self,
        run_id: str,
        raw: bytes,
        context: ArtifactContext,
        *,
        now: datetime | None = None,
    ) -> ArtifactManifest:
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            if context.verification_id != run.run_id:
                raise ValueError("artifact verification ID does not match the run")
            if context.repository_id != metadata.repository_id:
                raise ValueError("artifact repository ID does not match the run")
            stored = self.artifacts.put(
                raw,
                context,
                now=now or datetime.now(timezone.utc),
            )
            self.store.add_manifest(run_id, stored.manifest)
            if stored.manifest.artifact_id not in {
                evidence.artifact_id for evidence in run.evidence
            }:
                evidence = EvidenceArtifact(
                    artifact_id=stored.manifest.artifact_id,
                    sha256=stored.manifest.sha256,
                    size_bytes=stored.manifest.size_bytes,
                    media_type=stored.manifest.media_type,
                    retention_class=stored.manifest.retention_class,
                    created_at=stored.manifest.created_at,
                )
                updated = run.model_copy(
                    update={
                        "evidence": [*run.evidence, evidence],
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                self.store.replace(updated, metadata)
            return stored.manifest

    def complete(
        self,
        run_id: str,
        *,
        acceptance_evidence_ids: Sequence[str] = (),
    ) -> VerificationRun:
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            _require_status(run, {RunStatus.COMPLETING})
            manifests = self._available_manifests(run_id)
            current = [
                _with_available_artifacts(result, manifests) for result in run.results
            ]
            if any(result.worktree_hash != metadata.worktree_hash for result in current):
                current = [
                    result.model_copy(
                        update={"parser_error": "verification_worktree_metadata_mismatch"}
                    )
                    for result in current
                ]
            baseline = run.baseline
            if baseline is not None:
                baseline = baseline.model_copy(
                    update={
                        "results": [
                            _with_available_artifacts(result, manifests)
                            for result in baseline.results
                        ]
                    }
                )
            verdict = derive_verdict(
                run.contract,
                baseline,
                current,
                acceptance_evidence_ids=[
                    artifact_id
                    for artifact_id in acceptance_evidence_ids
                    if any(manifest.artifact_id == artifact_id for manifest in manifests)
                ],
            )
            verdict = self._with_preference_policy(run, metadata, verdict)
            completed = run.model_copy(
                update={
                    "status": RunStatus.COMPLETED,
                    "verdict": verdict,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self.store.replace(completed, metadata)
            return completed

    def _with_preference_policy(
        self,
        run: VerificationRun,
        metadata: VerificationMetadata,
        verdict: VerificationVerdict,
    ) -> VerificationVerdict:
        if run.preference_profile_id is None and not run.preference_verdict_ids:
            return verdict
        evidence = {
            "preference_profile_id": run.preference_profile_id,
            "preference_verdict_ids": list(run.preference_verdict_ids),
        }
        if self.preferences is None or run.preference_profile_id is None:
            return verdict.model_copy(
                update={
                    **evidence,
                    "status": _policy_unavailable_status(verdict.status),
                    "missing_required_checks": [
                        *verdict.missing_required_checks,
                        "preference:service-unavailable",
                    ],
                }
            )
        try:
            self.preferences.profile(run.preference_profile_id)
            effective = self.preferences.effective_verdicts(
                run.preference_verdict_ids,
                run_id=run.run_id,
                repository_id=metadata.repository_id,
            )
        except Exception:
            return verdict.model_copy(
                update={
                    **evidence,
                    "status": _policy_unavailable_status(verdict.status),
                    "missing_required_checks": [
                        *verdict.missing_required_checks,
                        "preference:policy-unavailable",
                    ],
                }
            )

        warning_ids: list[str] = []
        override_ids: list[str] = []
        blocking_rules: list[str] = []
        for preference_verdict, override in effective:
            if override is not None:
                override_ids.append(override.override_id)
                continue
            if preference_verdict.status.value != "violation":
                continue
            if preference_verdict.severity.value == "block":
                blocking_rules.append(preference_verdict.rule_id)
            else:
                warning_ids.append(preference_verdict.verdict_id)

        missing = list(verdict.missing_required_checks)
        for rule_id in sorted(set(blocking_rules)):
            check_id = f"preference:{rule_id}"
            if check_id not in missing:
                missing.append(check_id)
        status = verdict.status
        if blocking_rules and status in {
            VerdictStatus.VERIFIED,
            VerdictStatus.VERIFIED_WITH_PREEXISTING_FAILURES,
            VerdictStatus.CHECKS_PASSED_UNBASELINED,
        }:
            status = VerdictStatus.INCOMPLETE
        return verdict.model_copy(
            update={
                **evidence,
                "status": status,
                "missing_required_checks": missing,
                "preference_warning_ids": warning_ids,
                "preference_override_ids": override_ids,
            }
        )

    def mark_command_started(
        self,
        run_id: str,
        check_id: str,
        *,
        worktree_hash: str | None = None,
    ) -> None:
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            _require_status(
                run,
                {RunStatus.BASELINING, RunStatus.ACTIVE, RunStatus.COMPLETING},
            )
            if metadata.inflight_command_id is not None:
                raise ValueError("a verification command is already in flight")
            check = next((item for item in run.contract.checks if item.id == check_id), None)
            if check is None:
                raise ValueError("command is not part of the proof contract")
            observed_worktree = worktree_hash or metadata.worktree_hash
            if len(observed_worktree) != 64 or any(
                character not in "0123456789abcdef"
                for character in observed_worktree
            ):
                raise ValueError("command worktree hash must be a SHA-256 digest")
            self.store.replace(
                run.model_copy(update={"updated_at": datetime.now(timezone.utc)}),
                metadata.model_copy(
                    update={
                        "inflight_command_id": check.id,
                        "inflight_command_hash": _hash_model(check.model_dump(mode="json")),
                        "worktree_hash": observed_worktree,
                    }
                ),
            )

    def mark_command_finished(self, run_id: str, check_id: str) -> None:
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            if metadata.inflight_command_id != check_id:
                raise ValueError("finished command does not match the in-flight command")
            self.store.replace(
                run.model_copy(update={"updated_at": datetime.now(timezone.utc)}),
                metadata.model_copy(
                    update={"inflight_command_id": None, "inflight_command_hash": None}
                ),
            )

    def resume_recovery(
        self,
        run_id: str,
        *,
        contract_hash: str,
        worktree_hash: str,
        command_hash: str,
    ) -> VerificationRun:
        with self._lock:
            run = self.store.read(run_id)
            metadata = self.store.metadata(run_id)
            _require_status(run, {RunStatus.RECOVERING})
            reason = None
            if contract_hash != metadata.contract_hash:
                reason = "contract_hash_mismatch"
            elif worktree_hash != metadata.worktree_hash:
                reason = "worktree_hash_mismatch"
            elif command_hash != metadata.command_hash:
                reason = "command_hash_mismatch"
            elif metadata.inflight_command_id is not None:
                reason = "abandoned_command"
            if reason is not None:
                orphaned = run.model_copy(
                    update={
                        "status": RunStatus.ORPHANED,
                        "verdict": VerificationVerdict(
                            status=VerdictStatus.ORPHANED,
                            missing_required_checks=[
                                check.id
                                for check in run.contract.checks
                                if check.required
                            ],
                        ),
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                self.store.replace(
                    orphaned,
                    metadata.model_copy(update={"recovery_reason": reason}),
                )
                return orphaned
            if metadata.previous_status is None:
                raise ValueError("recovering run has no previous status")
            resumed = run.model_copy(
                update={
                    "status": metadata.previous_status,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self.store.replace(
                resumed,
                metadata.model_copy(
                    update={"previous_status": None, "recovery_reason": None}
                ),
            )
            return resumed

    def delete_artifact(self, manifest_id: str, *, actor: str, authorized: bool) -> None:
        if not authorized:
            raise PermissionError("artifact deletion requires privileged authorization")
        deleted_at = datetime.now(timezone.utc)
        artifact_id = self.store.delete_manifest(
            manifest_id,
            actor=actor,
            deleted_at=deleted_at,
        )
        if self.store.live_manifest_count(artifact_id) == 0:
            self.artifacts.shred(artifact_id, authorized=True)

    def expire_artifacts(
        self,
        *,
        now: datetime,
        actor: str,
        authorized: bool,
    ) -> list[str]:
        if not authorized:
            raise PermissionError("artifact expiry requires privileged authorization")
        expired = self.store.expired_manifest_ids(now)
        for manifest_id in expired:
            self.delete_artifact(manifest_id, actor=actor, authorized=True)
        return expired

    def cancel(self, run_id: str) -> VerificationRun:
        return self._transition(
            run_id,
            {RunStatus.BASELINING, RunStatus.ACTIVE, RunStatus.COMPLETING},
            RunStatus.CANCELLED,
        )

    def fail(self, run_id: str) -> VerificationRun:
        return self._transition(
            run_id,
            {RunStatus.BASELINING, RunStatus.ACTIVE, RunStatus.COMPLETING},
            RunStatus.FAILED,
        )

    def read_artifact(self, manifest_id: str) -> bytes:
        manifest, deleted = self.store.manifest(manifest_id)
        if deleted:
            raise ArtifactUnavailableError("artifact was retention-deleted")
        return self.artifacts.read(manifest)

    def artifact_availability(self, manifest_id: str) -> str:
        manifest, deleted = self.store.manifest(manifest_id)
        if deleted:
            return "deleted"
        try:
            self.artifacts.read(manifest)
        except ArtifactUnavailableError:
            return "missing"
        except ArtifactKeyError:
            return "deleted"
        except ArtifactIntegrityError:
            return "corrupt"
        return "available"

    def deletion_audit(self, manifest_id: str) -> dict[str, str]:
        return self.store.deletion_audit(manifest_id)

    def read(self, run_id: str) -> VerificationRun:
        return self.store.read(run_id)

    def metadata(self, run_id: str) -> VerificationMetadata:
        return self.store.metadata(run_id)

    def close(self) -> None:
        self.store.close()

    def _transition(
        self,
        run_id: str,
        allowed: set[RunStatus],
        target: RunStatus,
    ) -> VerificationRun:
        with self._lock:
            run, metadata = self._load_mutable(run_id)
            _require_status(run, allowed)
            updated = run.model_copy(
                update={"status": target, "updated_at": datetime.now(timezone.utc)}
            )
            self.store.replace(updated, metadata)
            return updated

    def _load_mutable(self, run_id: str) -> tuple[VerificationRun, VerificationMetadata]:
        run = self.store.read(run_id)
        if run.status in _TERMINAL:
            if run.status is RunStatus.COMPLETED:
                raise ValueError("completed verification is immutable")
            raise ValueError("terminal verification is immutable")
        return run, self.store.metadata(run_id)

    def _available_manifests(self, run_id: str) -> list[ArtifactManifest]:
        available: list[ArtifactManifest] = []
        for manifest in self.store.manifests_for_run(run_id):
            if self.artifact_availability(manifest.manifest_id) == "available":
                available.append(manifest)
        return available


def _require_status(run: VerificationRun, allowed: set[RunStatus]) -> None:
    if run.status not in allowed:
        raise ValueError(
            f"invalid verification transition from {run.status.value}"
        )


def _policy_unavailable_status(status: VerdictStatus) -> VerdictStatus:
    if status in {
        VerdictStatus.VERIFIED,
        VerdictStatus.VERIFIED_WITH_PREEXISTING_FAILURES,
        VerdictStatus.CHECKS_PASSED_UNBASELINED,
    }:
        return VerdictStatus.INCONCLUSIVE
    return status


def _hash_model(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _command_hash(contract: ProofContract) -> str:
    return _hash_model(
        {"checks": [check.model_dump(mode="json") for check in contract.checks]}
    )


def _with_available_artifacts(
    result: CheckResult,
    manifests: Sequence[ArtifactManifest],
) -> CheckResult:
    available = {
        manifest.artifact_id
        for manifest in manifests
        if manifest.command_id == result.check_id
        and manifest.worktree_hash == result.worktree_hash
    }
    return result.model_copy(
        update={
            "artifact_ids": [
                artifact_id for artifact_id in result.artifact_ids if artifact_id in available
            ]
        }
    )
