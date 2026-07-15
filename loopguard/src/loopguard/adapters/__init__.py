"""Typed contracts for attached and managed agent integrations."""

from .base import (
    AdapterCapabilities,
    AdapterFailure,
    AgentAdapter,
    Capability,
    CapabilityError,
    LifecycleError,
    LifecycleErrorCode,
    ManagedRunRequest,
)

__all__ = [
    "AdapterCapabilities",
    "AdapterFailure",
    "AgentAdapter",
    "Capability",
    "CapabilityError",
    "LifecycleError",
    "LifecycleErrorCode",
    "ManagedRunRequest",
]
