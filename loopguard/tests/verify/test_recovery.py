from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopguard.verify.artifacts import ArtifactUnavailableError
from loopguard.verify.manifest import ArtifactContext
from loopguard.verify.models import (
    AcceptanceState,
    CheckSpec,
    ContractSource,
    ProofContract,
    RetentionClass,
    RunStatus,
)
from loopguard.verify.service import VerificationService
from loopguard.verify.store import VerificationStoreError


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _contract() -> ProofContract:
    return ProofContract(
        task_id="task-recovery",
        source_event_id="prompt-1",
        source_prompt_hash="a" * 64,
        source=ContractSource.CONTRACT_EVENT,
        source_hash="b" * 64,
        acceptance_state=AcceptanceState.CONFIRMED,
        acceptance=["Recovery is honest"],
        checks=[CheckSpec(id="unit", command=["pytest", "-q"])],
    )


def _context(run_id: str) -> ArtifactContext:
    return ArtifactContext(
        repository_id="repo-1",
        worktree_hash="c" * 64,
        verification_id=run_id,
        command_id="unit",
        result_id="result-1",
        media_type="text/plain",
        retention_class=RetentionClass.RAW_LOGS,
    )


def test_restart_marks_nonterminal_run_recovering_and_resumes_only_exact_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "verify.db"
    first = VerificationService.for_path(path)
    run_id = first.start(_contract(), repository_id="repo-1")
    first.begin_baselining(run_id)
    first.close()

    restarted = VerificationService.for_path(path)
    assert restarted.read(run_id).status is RunStatus.RECOVERING
    metadata = restarted.metadata(run_id)
    resumed = restarted.resume_recovery(
        run_id,
        contract_hash=metadata.contract_hash,
        worktree_hash=metadata.worktree_hash,
        command_hash=metadata.command_hash,
    )

    assert resumed.status is RunStatus.BASELINING


def test_mismatch_or_abandoned_inflight_command_becomes_orphaned(tmp_path: Path) -> None:
    path = tmp_path / "verify.db"
    first = VerificationService.for_path(path)
    mismatch_id = first.start(_contract(), repository_id="repo-1")
    first.begin_baselining(mismatch_id)
    inflight_id = first.start(_contract(), repository_id="repo-1")
    first.begin_baselining(inflight_id)
    first.mark_command_started(inflight_id, "unit")
    first.close()

    restarted = VerificationService.for_path(path)
    mismatch = restarted.resume_recovery(
        mismatch_id,
        contract_hash="0" * 64,
        worktree_hash=restarted.metadata(mismatch_id).worktree_hash,
        command_hash=restarted.metadata(mismatch_id).command_hash,
    )
    inflight = restarted.resume_recovery(
        inflight_id,
        contract_hash=restarted.metadata(inflight_id).contract_hash,
        worktree_hash=restarted.metadata(inflight_id).worktree_hash,
        command_hash=restarted.metadata(inflight_id).command_hash,
    )

    assert mismatch.status is inflight.status is RunStatus.ORPHANED
    assert restarted.metadata(mismatch_id).recovery_reason == "contract_hash_mismatch"
    assert restarted.metadata(inflight_id).recovery_reason == "abandoned_command"


def test_orphan_object_is_cleaned_and_database_record_without_object_is_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "verify.db"
    service = VerificationService.for_path(path)
    run_id = service.start(_contract(), repository_id="repo-1")
    orphan = service.artifacts.put(b"orphan", _context(run_id), now=NOW)
    orphan_path = service.artifacts.object_path(orphan.manifest.artifact_id)
    assert orphan_path.exists()
    service.close()

    restarted = VerificationService.for_path(path)
    assert not orphan_path.exists()
    manifest = restarted.add_artifact(run_id, b"registered", _context(run_id), now=NOW)
    restarted.artifacts.object_path(manifest.artifact_id).unlink()

    with pytest.raises(ArtifactUnavailableError):
        restarted.read_artifact(manifest.manifest_id)


def test_db_manifest_without_object_is_detected_even_after_direct_corruption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "verify.db"
    service = VerificationService.for_path(path)
    run_id = service.start(_contract(), repository_id="repo-1")
    manifest = service.add_artifact(run_id, b"registered", _context(run_id), now=NOW)
    service.close()
    service.artifacts.object_path(manifest.artifact_id).unlink(missing_ok=True)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM artifact_manifests WHERE manifest_id = ?",
            (manifest.manifest_id,),
        ).fetchone()[0] == 1

    reopened = VerificationService.for_path(path)
    assert reopened.artifact_availability(manifest.manifest_id) == "missing"


def test_direct_run_record_tampering_fails_authentication(tmp_path: Path) -> None:
    path = tmp_path / "verify.db"
    service = VerificationService.for_path(path)
    run_id = service.start(_contract(), repository_id="repo-1")
    service.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE verification_runs SET repository_id = 'forged' WHERE run_id = ?",
            (run_id,),
        )

    reopened = VerificationService.for_path(path)
    with pytest.raises(VerificationStoreError, match="authentication"):
        reopened.read(run_id)
