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

__all__ = [
    "CandidateEvaluation",
    "CandidateBudget",
    "CandidateArtifactStore",
    "CandidateGenerationResult",
    "CandidateGenerationService",
    "CandidatePatch",
    "DataContractDelta",
    "FailureEvent",
    "FailurePayloadRejected",
    "FixtureBuilder",
    "FileCandidateArtifactStore",
    "HostileEvidenceEnvelope",
    "PatchPolicy",
    "PublicationRecord",
    "RepairRun",
    "RepairPlanner",
    "RepairState",
    "RepairStrategy",
    "ReplayFixture",
    "ReproductionResult",
    "ReproductionService",
    "UnsafeFixture",
    "normalize_failure",
]
