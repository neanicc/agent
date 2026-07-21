"""Supervised, isolated browser acceleration services."""

from .auth import AuthStateReference, BrowserAuthStore, PlaintextAuthLease
from .client import BrowserBrokerClient, BrowserResult, LocalBrokerFactory
from .execution import BrowserExecutionPlan, BrowserExecutionPolicy, playwright_adapter_status
from .manifest import PlaywrightManifest, PlaywrightTestFile
from .selection import PlaywrightSelection, PlaywrightSelector
from .service import BrowserLease, BrowserService

__all__ = [
    "BrowserBrokerClient",
    "BrowserExecutionPlan",
    "BrowserExecutionPolicy",
    "playwright_adapter_status",
    "BrowserAuthStore",
    "BrowserLease",
    "BrowserResult",
    "BrowserService",
    "AuthStateReference",
    "LocalBrokerFactory",
    "PlaintextAuthLease",
    "PlaywrightManifest",
    "PlaywrightSelection",
    "PlaywrightSelector",
    "PlaywrightTestFile",
]
