from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loopguard.control.crypto import InMemoryKeyStore, MissingKeyError
from loopguard.verify.manifest import ArtifactContext
from loopguard.verify.models import (
    AcceptanceState,
    Baseline,
    CheckResult,
    CheckSpec,
    CheckStatus,
    ContractSource,
    IsolationLevel,
    ProofContract,
    RetentionClass,
    RunStatus,
    VerdictStatus,
)
from loopguard.verify.service import VerificationService
from loopguard.verify.runner import CheckExecution


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _contract() -> ProofContract:
    return ProofContract(
        task_id="task-1",
        source_event_id="prompt-1",
        source_prompt_hash="a" * 64,
        source=ContractSource.CONTRACT_EVENT,
        source_hash="b" * 64,
        acceptance_state=AcceptanceState.CONFIRMED,
        acceptance=["Change works"],
        checks=[CheckSpec(id="unit", command=["pytest", "-q"])],
    )


def _result(artifact_id: str, *, failures: list[str] | None = None) -> CheckResult:
    failure_ids = failures or []
    return CheckResult(
        check_id="unit",
        status=CheckStatus.FAILED if failure_ids else CheckStatus.PASSED,
        started_at=NOW,
        completed_at=NOW,
        exit_code=1 if failure_ids else 0,
        failure_ids=failure_ids,
        artifact_ids=[artifact_id],
        isolation=IsolationLevel.SANDBOXED,
        worktree_hash="c" * 64,
    )


def _baseline(result: CheckResult) -> Baseline:
    return Baseline(
        baseline_id="baseline-1",
        captured_at=NOW,
        repository_sha="d" * 40,
        worktree_hash="c" * 64,
        owning_session_id="session-1",
        source_prompt_event_id="prompt-1",
        captured_before_first_mutation=True,
        results=[result],
    )


def _artifact_context(run_id: str, result_id: str) -> ArtifactContext:
    return ArtifactContext(
        repository_id="repo-1",
        worktree_hash="c" * 64,
        verification_id=run_id,
        command_id="unit",
        result_id=result_id,
        media_type="text/plain",
        retention_class=RetentionClass.RAW_LOGS,
    )


def test_completed_verification_is_immutable_and_keeps_exact_cursors(tmp_path: Path) -> None:
    service = VerificationService.for_path(tmp_path / "verify.db")
    run_id = service.start(_contract(), repository_id="repo-1", repo_seq=7, session_seq=11)
    service.begin_baselining(run_id)
    baseline_artifact = service.add_artifact(
        run_id,
        b"baseline passed",
        _artifact_context(run_id, "baseline-result"),
        now=NOW,
    )
    service.record_baseline(run_id, _baseline(_result(baseline_artifact.artifact_id)))
    service.begin_completion(run_id)
    current_artifact = service.add_artifact(
        run_id,
        b"current passed",
        _artifact_context(run_id, "current-result"),
        now=NOW,
    )
    acceptance = service.add_artifact(
        run_id,
        b"acceptance evidence",
        _artifact_context(run_id, "acceptance"),
        now=NOW,
    )
    service.add_result(run_id, _result(current_artifact.artifact_id))

    completed = service.complete(run_id, acceptance_evidence_ids=[acceptance.artifact_id])

    assert completed.status is RunStatus.COMPLETED
    assert completed.verdict is not None
    assert completed.verdict.status is VerdictStatus.VERIFIED
    assert len(completed.evidence) == 3
    metadata = service.metadata(run_id)
    assert metadata.repo_seq == 7
    assert metadata.session_seq == 11
    assert metadata.contract_hash
    with pytest.raises(ValueError, match="completed verification is immutable"):
        service.add_result(run_id, _result(current_artifact.artifact_id))
    with pytest.raises(ValueError, match="completed verification is immutable"):
        service.begin_completion(run_id)


def test_invalid_state_transition_and_unregistered_artifact_fail_closed(tmp_path: Path) -> None:
    service = VerificationService.for_path(tmp_path / "verify.db")
    run_id = service.start(_contract(), repository_id="repo-1")
    with pytest.raises(ValueError, match="invalid verification transition"):
        service.begin_completion(run_id)
    service.begin_baselining(run_id)
    service.record_baseline(run_id, _baseline(_result("missing-baseline-artifact")))
    service.begin_completion(run_id)
    service.add_result(run_id, _result("missing-current-artifact"))

    completed = service.complete(run_id, acceptance_evidence_ids=["missing-acceptance"])

    assert completed.verdict is not None
    assert completed.verdict.status is VerdictStatus.INCOMPLETE


def test_retention_deletion_is_authorized_audited_and_does_not_mutate_proof(
    tmp_path: Path,
) -> None:
    service = VerificationService.for_path(tmp_path / "verify.db")
    run_id = service.start(_contract(), repository_id="repo-1")
    manifest = service.add_artifact(
        run_id,
        b"sensitive",
        _artifact_context(run_id, "sensitive"),
        now=NOW,
    )
    before = service.read(run_id)
    with pytest.raises(PermissionError):
        service.delete_artifact(manifest.manifest_id, actor="user", authorized=False)

    service.delete_artifact(manifest.manifest_id, actor="admin", authorized=True)

    assert service.read(run_id) == before
    assert service.artifact_availability(manifest.manifest_id) == "deleted"
    assert service.deletion_audit(manifest.manifest_id)["actor"] == "admin"


def test_artifact_context_is_scoped_to_repository_run_check_and_worktree(
    tmp_path: Path,
) -> None:
    service = VerificationService.for_path(tmp_path / "verify.db")
    run_id = service.start(_contract(), repository_id="repo-1")
    wrong_repository = _artifact_context(run_id, "result").model_copy(
        update={"repository_id": "repo-2"}
    )

    with pytest.raises(ValueError, match="repository ID"):
        service.add_artifact(run_id, b"wrong", wrong_repository, now=NOW)


def test_retention_expiry_and_terminal_cancel_are_explicit(tmp_path: Path) -> None:
    service = VerificationService.for_path(tmp_path / "verify.db")
    run_id = service.start(_contract(), repository_id="repo-1")
    service.begin_baselining(run_id)
    manifest = service.add_artifact(
        run_id,
        b"expires",
        _artifact_context(run_id, "expiry").model_copy(
            update={"retention_class": RetentionClass.SENSITIVE}
        ),
        now=NOW,
    )

    expired = service.expire_artifacts(
        now=NOW + timedelta(days=2),
        actor="retention-worker",
        authorized=True,
    )
    cancelled = service.cancel(run_id)

    assert expired == [manifest.manifest_id]
    assert service.artifact_availability(manifest.manifest_id) == "deleted"
    assert cancelled.status is RunStatus.CANCELLED
    with pytest.raises(ValueError, match="terminal verification is immutable"):
        service.add_result(run_id, _result("anything"))


def test_artifact_registered_to_another_run_cannot_satisfy_proof(tmp_path: Path) -> None:
    service = VerificationService.for_path(tmp_path / "verify.db")
    first_id = service.start(_contract(), repository_id="repo-1")
    foreign = service.add_artifact(
        first_id,
        b"foreign",
        _artifact_context(first_id, "foreign"),
        now=NOW,
    )
    second_id = service.start(_contract(), repository_id="repo-1")
    service.begin_baselining(second_id)
    service.record_baseline(second_id, _baseline(_result(foreign.artifact_id)))
    service.begin_completion(second_id)
    service.add_result(second_id, _result(foreign.artifact_id))

    completed = service.complete(
        second_id,
        acceptance_evidence_ids=[foreign.artifact_id],
    )

    assert completed.verdict is not None
    assert completed.verdict.status is VerdictStatus.INCOMPLETE


def test_production_open_persists_master_key_outside_the_database(tmp_path: Path) -> None:
    keys = InMemoryKeyStore()
    first = VerificationService.open(home=tmp_path / "state", key_store=keys)
    run_id = first.start(_contract(), repository_id="repo-1")
    first.close()

    reopened = VerificationService.open(home=tmp_path / "state", key_store=keys)
    assert reopened.read(run_id).status is RunStatus.CREATED
    key_id = keys.key_ids()[0]
    keys.delete(key_id)
    reopened.close()

    with pytest.raises(MissingKeyError):
        VerificationService.open(home=tmp_path / "state", key_store=keys)


def test_runner_output_is_persisted_before_result_receives_artifact_id(
    tmp_path: Path,
) -> None:
    service = VerificationService.for_path(tmp_path / "verify.db")
    run_id = service.start(_contract(), repository_id="repo-1")
    service.begin_baselining(run_id)
    service.record_baseline(run_id, _baseline(_result("baseline-placeholder")))
    service.begin_completion(run_id)
    execution = CheckExecution(
        result=_result("provisional-runner-hash"),
        stdout=b"TOKEN=must-not-persist",
        stderr=b"warning",
        output_artifact_id="sha256:" + "f" * 64,
    )

    persisted = service.record_execution(run_id, execution, now=NOW)
    manifest = service.store.manifests_for_run(run_id)[0]

    assert persisted.artifact_ids == [manifest.artifact_id]
    assert persisted.artifact_ids != execution.result.artifact_ids
    assert b"must-not-persist" not in service.read_artifact(manifest.manifest_id)
