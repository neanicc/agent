"""Deterministic, provider-neutral model routing primitives."""

from .catalog import CatalogDocument, CatalogTrustError, ModelCatalog, ModelSpec
from .coordinator import PhaseCoordinator, PhaseState, PhaseTransitionError
from .evaluation import EvaluationReport, ShadowDecision, assign_experiment, evaluate_outcomes
from .features import FeatureExtractor, RepositorySnapshot, TaskProfile
from .outcomes import OutcomeRecorder, RoutingOutcome
from .policy import RouterPolicy, RoutingDecision

__all__ = [
    "CatalogDocument",
    "CatalogTrustError",
    "EvaluationReport",
    "FeatureExtractor",
    "ModelCatalog",
    "ModelSpec",
    "OutcomeRecorder",
    "PhaseCoordinator",
    "PhaseState",
    "PhaseTransitionError",
    "RepositorySnapshot",
    "RouterPolicy",
    "RoutingDecision",
    "RoutingOutcome",
    "ShadowDecision",
    "TaskProfile",
    "assign_experiment",
    "evaluate_outcomes",
]
