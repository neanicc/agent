from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from loopguard.heal.candidates import FileCandidateArtifactStore
from loopguard.heal.evaluate import (
    CandidateEvaluator,
    CheckOutcome,
    CheckStatus,
    ContractAnalysis,
    EvaluationCheck,
    EvaluationPlan,
    EvaluationStage,
)
from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    DataContractDelta,
)
from loopguard.heal.rank import rank_candidates


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, bytes]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "loopguard@example.test")
    _git(repository, "config", "user.name", "LoopGuard Tests")
    (repository / "pipeline.py").write_text("VALUE = 'broken'\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "baseline")
    revision = _git(repository, "rev-parse", "HEAD")
    (repository / "pipeline.py").write_text("VALUE = 'fixed'\n", encoding="utf-8")
    patch = subprocess.run(
        ["git", "diff", "--binary", "--full-index", "HEAD", "--"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    _git(repository, "restore", "pipeline.py")
    return repository, revision, patch


def _candidate(store: FileCandidateArtifactStore, revision: str, patch: bytes) -> CandidatePatch:
    artifact_id = store.put_patch(patch)
    return CandidatePatch(
        candidate_id="candidate-boundary",
        base_sha=revision,
        patch_artifact_id=artifact_id,
        patch_sha256=artifact_id.removeprefix("sha256:"),
        changed_files=("pipeline.py",),
        changed_lines=2,
        strategy="normalize_ingestion_boundary",
    )


def _plan() -> EvaluationPlan:
    return EvaluationPlan(
        reproduction=EvaluationCheck(
            check_id="replay",
            stage=EvaluationStage.REPRODUCTION,
            command=("python", "pipeline.py"),
        ),
        fixture_checks=(
            EvaluationCheck(
                check_id="fixture",
                stage=EvaluationStage.FIXTURE_SCHEMA,
                command=("python", "-m", "pytest", "tests/fixture"),
            ),
        ),
        data_quality_checks=(
            EvaluationCheck(
                check_id="quality",
                stage=EvaluationStage.DATA_QUALITY,
                command=("python", "quality.py"),
            ),
        ),
        impacted_checks=(
            EvaluationCheck(
                check_id="impacted",
                stage=EvaluationStage.IMPACTED_TESTS,
                command=("python", "-m", "pytest", "tests/impacted"),
            ),
        ),
        security_checks=(
            EvaluationCheck(
                check_id="security",
                stage=EvaluationStage.SECURITY,
                command=("python", "-m", "bandit", "pipeline.py"),
            ),
        ),
        static_checks=(
            EvaluationCheck(
                check_id="static",
                stage=EvaluationStage.STATIC,
                command=("python", "-m", "ruff", "check", "pipeline.py"),
            ),
        ),
    )


class RecordingRunner:
    def __init__(self, statuses: dict[str, CheckStatus] | None = None) -> None:
        self.statuses = statuses or {}
        self.order: list[str] = []
        self.observed_source: list[str] = []

    async def run(self, check: EvaluationCheck, *, worktree: Path) -> CheckOutcome:
        self.order.append(check.stage.value)
        self.observed_source.append((worktree / "pipeline.py").read_text(encoding="utf-8"))
        status = self.statuses.get(check.check_id, CheckStatus.PASSED)
        return CheckOutcome(
            check_id=check.check_id,
            stage=check.stage,
            status=status,
            duration_ms=7,
            artifact_ids=(f"artifact-{check.check_id}",),
        )


class RecordingContractAnalyzer:
    def __init__(self, *, breaking: bool = False) -> None:
        self.breaking = breaking
        self.called = 0

    async def analyze(self, *, worktree: Path) -> ContractAnalysis:
        self.called += 1
        assert (worktree / "pipeline.py").read_text(encoding="utf-8") == "VALUE = 'fixed'\n"
        return ContractAnalysis(
            delta=DataContractDelta(
                changes=({"path": "coordinate", "from": "string", "to": "number"},),
                breaking=self.breaking,
            ),
            duration_ms=5,
            artifact_ids=("artifact-contract",),
        )


def test_evaluator_applies_patch_then_runs_required_order(tmp_path: Path) -> None:
    repository, revision, patch = _repository(tmp_path)
    store = FileCandidateArtifactStore(tmp_path / "artifacts")
    runner = RecordingRunner()
    analyzer = RecordingContractAnalyzer()
    evaluator = CandidateEvaluator(
        repository=repository,
        worktree_root=tmp_path / "evaluation-worktrees",
        patch_reader=store,
        runner=runner,
        contract_analyzer=analyzer,
    )

    result = asyncio.run(evaluator.evaluate(_candidate(store, revision, patch), _plan()))

    assert result.evaluation.verified is True
    assert runner.order == [
        "reproduction",
        "fixture_schema",
        "data_quality",
        "impacted_tests",
        "security",
        "static",
    ]
    assert runner.observed_source == ["VALUE = 'fixed'\n"] * 6
    assert analyzer.called == 1
    assert result.evaluation.verification_duration_ms == 47
    assert result.evaluation.evidence_artifact_ids[-1] == "artifact-contract"


def test_required_timeout_fails_closed_and_stops_later_checks(tmp_path: Path) -> None:
    repository, revision, patch = _repository(tmp_path)
    store = FileCandidateArtifactStore(tmp_path / "artifacts")
    runner = RecordingRunner({"quality": CheckStatus.TIMED_OUT})
    analyzer = RecordingContractAnalyzer()
    evaluator = CandidateEvaluator(
        repository=repository,
        worktree_root=tmp_path / "evaluation-worktrees",
        patch_reader=store,
        runner=runner,
        contract_analyzer=analyzer,
    )

    result = asyncio.run(evaluator.evaluate(_candidate(store, revision, patch), _plan()))

    assert result.evaluation.required_inconclusive is True
    assert result.evaluation.verified is False
    assert result.evaluation.rejection_reason == "data_quality_timed_out"
    assert runner.order == ["reproduction", "fixture_schema", "data_quality"]
    assert analyzer.called == 0


def test_tampered_or_unapplicable_patch_is_rejected_before_execution(tmp_path: Path) -> None:
    repository, revision, patch = _repository(tmp_path)
    store = FileCandidateArtifactStore(tmp_path / "artifacts")
    candidate = _candidate(store, revision, patch).model_copy(update={"patch_sha256": "0" * 64})
    runner = RecordingRunner()
    evaluator = CandidateEvaluator(
        repository=repository,
        worktree_root=tmp_path / "evaluation-worktrees",
        patch_reader=store,
        runner=runner,
        contract_analyzer=RecordingContractAnalyzer(),
    )

    result = asyncio.run(evaluator.evaluate(candidate, _plan()))

    assert result.evaluation.rejection_reason == "patch_artifact_invalid"
    assert runner.order == []


def _evaluation(
    candidate_id: str,
    *,
    replay: bool = True,
    regression: bool = True,
    security: bool = True,
    changed_files: int = 1,
    changed_lines: int = 10,
    contract_break: bool = False,
    required_inconclusive: bool = False,
    risk_penalty: int = 0,
    duration_ms: int = 100,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_id=candidate_id,
        replay_passed=replay,
        regression_passed=regression,
        security_passed=security,
        required_inconclusive=required_inconclusive,
        contract_delta=DataContractDelta(
            changes=({"path": "x", "from": "a", "to": "b"},) if contract_break else (),
            breaking=contract_break,
        ),
        changed_files=changed_files,
        changed_lines=changed_lines,
        risk_penalty=risk_penalty,
        verification_duration_ms=duration_ms,
        rejection_reason=None,
    )


def test_passing_minimal_boundary_fix_wins() -> None:
    candidates = [
        _evaluation("broad", changed_lines=80, contract_break=True),
        _evaluation("boundary", changed_lines=12),
        _evaluation("small-fail", replay=False, changed_lines=4),
    ]

    result = rank_candidates(candidates)

    assert result.winner_id == "boundary"
    assert result.rejected["small-fail"] == "replay_failed"
    assert result.rejected["broad"] == "contract_breaking"


def test_no_candidate_wins_if_required_check_is_inconclusive() -> None:
    result = rank_candidates([_evaluation("x", required_inconclusive=True)])

    assert result.winner_id is None
    assert result.rejected == {"x": "required_check_inconclusive"}


def test_rank_is_deterministic_by_contract_risk_size_duration_then_id() -> None:
    candidates = [
        _evaluation("slower", duration_ms=200),
        _evaluation("riskier", risk_penalty=2, changed_lines=1),
        _evaluation("winner-b", duration_ms=50),
        _evaluation("winner-a", duration_ms=50),
    ]

    result = rank_candidates(candidates)

    assert result.winner_id == "winner-a"
    assert result.ordered == ("winner-a", "winner-b", "slower", "riskier")
