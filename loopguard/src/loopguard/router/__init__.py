"""Deterministic, provider-neutral model routing primitives."""

from .catalog import CatalogDocument, CatalogTrustError, ModelCatalog, ModelSpec
from .features import FeatureExtractor, RepositorySnapshot, TaskProfile
from .outcomes import OutcomeRecorder, RoutingOutcome
from .policy import RouterPolicy, RoutingDecision

__all__ = [
    "CatalogDocument",
    "CatalogTrustError",
    "FeatureExtractor",
    "ModelCatalog",
    "ModelSpec",
    "OutcomeRecorder",
    "RepositorySnapshot",
    "RouterPolicy",
    "RoutingDecision",
    "RoutingOutcome",
    "TaskProfile",
]
