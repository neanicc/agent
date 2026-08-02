"""Safety-gated pipeline repair contracts and services."""

from loopguard.heal.intake import FailurePayloadRejected, normalize_failure
from loopguard.heal.fixtures import FixtureBuilder, ReplayFixture, UnsafeFixture
from loopguard.heal.candidates import (
    CandidateBudget,
    CandidateArtifactStore,
    CandidateGenerationResult,
    CandidateGenerationService,
    FileCandidateArtifactStore,
    PatchPolicy,
)
from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    DataContractDelta,
    FailureEvent,
    PublicationRecord,
    RepairRun,
    RepairState,
)
from loopguard.heal.reproduce import ReproductionResult, ReproductionService
from loopguard.heal.planner import HostileEvidenceEnvelope, RepairPlanner, RepairStrategy
from loopguard.heal.evaluate import (
    CandidateEvaluator,
    EvaluationCheck,
    EvaluationPlan,
    EvaluationRecord,
)
from loopguard.heal.rank import RankingResult, rank_candidates
from loopguard.heal.github import GitHubPublisher, PublicationRequest
from loopguard.heal.report import RepairReport, render_report
from loopguard.heal.metrics import MetricsSnapshot, RolloutMetrics, RolloutMode, RolloutPolicy

__all__ = [
    "CandidateEvaluation",
    "CandidateEvaluator",
    "CandidateBudget",
    "CandidateArtifactStore",
    "CandidateGenerationResult",
    "CandidateGenerationService",
    "CandidatePatch",
    "DataContractDelta",
    "FailureEvent",
    "FailurePayloadRejected",
    "FixtureBuilder",
    "GitHubPublisher",
    "FileCandidateArtifactStore",
    "EvaluationCheck",
    "EvaluationPlan",
    "EvaluationRecord",
    "HostileEvidenceEnvelope",
    "MetricsSnapshot",
    "PatchPolicy",
    "PublicationRecord",
    "PublicationRequest",
    "RankingResult",
    "RepairRun",
    "RepairPlanner",
    "RepairReport",
    "RepairState",
    "RepairStrategy",
    "RolloutMetrics",
    "RolloutMode",
    "RolloutPolicy",
    "ReplayFixture",
    "ReproductionResult",
    "ReproductionService",
    "UnsafeFixture",
    "normalize_failure",
    "rank_candidates",
    "render_report",
]
