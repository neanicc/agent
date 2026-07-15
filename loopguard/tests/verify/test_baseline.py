from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopguard.verify.baseline import (
    BaselineCaptureError,
    BaselineService,
    RepositorySnapshot,
    capture_repository_snapshot,
)
from loopguard.verify.models import (
    AcceptanceState,
    CheckPhase,
    CheckResult,
    CheckSpec,
    CheckStatus,
    ContractSource,
    IsolationLevel,
    ProofContract,
)
from loopguard.verify.isolation import IsolationPlan
from loopguard.verify.runner import CommandRunner, ExecutionAuthorization
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
        invariants=["Existing behavior remains"],
        checks=[
            CheckSpec(
                id="unit",
                command=["pytest", "-q"],
                phase=CheckPhase.COMPLETION,
            )
        ],
    )


class _FakeRunner:
    def __init__(self, result: CheckResult):
        self.result = result
        self.calls: list[str] = []

    async def run(self, spec, *, authorization):
        self.calls.append(spec.id)
        return CheckExecution(
            result=self.result,
            stdout=b"FAILED tests/test_old.py::test_old",
            stderr=b"",
            output_artifact_id="sha256:" + "f" * 64,
        )


def test_baseline_records_existing_failure_without_marking_regression(tmp_path: Path) -> None:
    result = CheckResult(
        check_id="unit",
        status=CheckStatus.FAILED,
        started_at=NOW,
        completed_at=NOW,
        exit_code=1,
        failure_ids=["tests/test_old.py::test_old"],
        artifact_ids=["sha256:" + "f" * 64],
        isolation=IsolationLevel.SANDBOXED,
        worktree_hash="c" * 64,
    )
    runner = _FakeRunner(result)
    snapshot = RepositorySnapshot(
        repository_sha="d" * 40,
        worktree_hash="c" * 64,
        dirty_manifest=["M existing.py"],
        untracked_manifest=["notes.txt"],
    )
    service = BaselineService(runner, snapshotter=lambda _path: snapshot)

    baseline = asyncio.run(
        service.capture(
            _contract(),
            repository=tmp_path,
            owning_session_id="session-1",
        )
    )

    assert baseline.results[0].status is CheckStatus.FAILED
    assert baseline.results[0].failure_ids == ["tests/test_old.py::test_old"]
    assert baseline.captured_before_first_mutation is True
    assert runner.calls == ["unit"]


def test_baseline_preserves_dirty_and_untracked_pre_mutation_manifest(tmp_path: Path) -> None:
    result = CheckResult(
        check_id="unit",
        status=CheckStatus.PASSED,
        started_at=NOW,
        completed_at=NOW,
        exit_code=0,
        isolation=IsolationLevel.SANDBOXED,
        worktree_hash="c" * 64,
    )
    snapshot = RepositorySnapshot(
        repository_sha="d" * 40,
        worktree_hash="c" * 64,
        dirty_manifest=["M src/existing.py"],
        untracked_manifest=["scratch.txt"],
    )

    baseline = asyncio.run(
        BaselineService(_FakeRunner(result), snapshotter=lambda _path: snapshot).capture(
            _contract(),
            repository=tmp_path,
            owning_session_id="session-1",
        )
    )

    assert baseline.dirty_manifest == ["M src/existing.py"]
    assert baseline.untracked_manifest == ["scratch.txt"]


def test_real_repository_snapshot_hashes_dirty_and_untracked_bytes(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LoopGuard"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "loopguard@example.test"],
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    tracked.write_text("after")
    (tmp_path / "untracked.txt").write_text("one")

    first = capture_repository_snapshot(tmp_path)
    (tmp_path / "untracked.txt").write_text("two")
    second = capture_repository_snapshot(tmp_path)

    assert first.repository_sha == second.repository_sha
    assert " M tracked.txt" in first.dirty_manifest
    assert first.untracked_manifest == ["untracked.txt"]
    assert first.worktree_hash != second.worktree_hash


def test_baseline_refuses_verification_command_that_mutates_worktree(tmp_path: Path) -> None:
    result = CheckResult(
        check_id="unit",
        status=CheckStatus.PASSED,
        started_at=NOW,
        completed_at=NOW,
        exit_code=0,
        isolation=IsolationLevel.SANDBOXED,
        worktree_hash="c" * 64,
    )
    snapshots = iter(
        [
            RepositorySnapshot(repository_sha="d" * 40, worktree_hash="c" * 64),
            RepositorySnapshot(repository_sha="d" * 40, worktree_hash="e" * 64),
        ]
    )
    service = BaselineService(_FakeRunner(result), snapshotter=lambda _path: next(snapshots))

    with pytest.raises(BaselineCaptureError, match="mutated"):
        asyncio.run(
            service.capture(
                _contract(),
                repository=tmp_path,
                owning_session_id="session-1",
            )
        )


def test_real_runner_result_binds_same_pre_command_worktree_hash_as_baseline(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LoopGuard"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "loopguard@example.test"],
        check=True,
    )
    (tmp_path / "tracked.txt").write_text("stable")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)

    class Sandboxed:
        def prepare(self, worktree: Path, *, network_allowed: bool) -> IsolationPlan:
            return IsolationPlan(
                level=IsolationLevel.SANDBOXED,
                enforces_memory_limit=True,
                enforces_process_limit=True,
            )

    runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=Sandboxed(),
    )
    contract = _contract().model_copy(
        update={
            "checks": [
                CheckSpec(
                    id="unit",
                    command=["python", "-c", "print('verified')"],
                )
            ]
        }
    )
    baseline = asyncio.run(
        BaselineService(
            runner,
            authorization_factory=lambda _id: ExecutionAuthorization(approved=True),
        ).capture(
            contract,
            repository=tmp_path,
            owning_session_id="session-1",
        )
    )

    assert baseline.results[0].worktree_hash == baseline.worktree_hash
