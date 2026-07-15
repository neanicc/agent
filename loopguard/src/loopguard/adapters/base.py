from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from loopguard.control.decisions import ActionRequest
from loopguard.control.events import ControlEvent, SessionRef


class Capability(StrEnum):
    OBSERVE = "observe"
    START_MANAGED = "start_managed"
    BLOCK_TOOL = "block_tool"
    INJECT_CONTEXT = "inject_context"
    INTERRUPT = "interrupt"
    SELECT_MODEL = "select_model"
    SELECT_EFFORT = "select_effort"
    APPROVE_TOOL = "approve_tool"


@dataclass(frozen=True, slots=True)
class CapabilityError:
    code: Literal["capability_unavailable"]
    capability: Capability
    surface: str

    def __post_init__(self) -> None:
        if self.code != "capability_unavailable":
            raise ValueError("capability errors must use the stable capability_unavailable code")
        _require_surface(self.surface)


class LifecycleErrorCode(StrEnum):
    UNKNOWN_SESSION = "unknown_session"
    STALE_STATE = "stale_state"
    PROCESS_EXITED = "process_exited"
    TIMEOUT = "timeout"
    PROTOCOL_VERSION = "protocol_version"


_LIFECYCLE_MESSAGES = {
    LifecycleErrorCode.UNKNOWN_SESSION: "The adapter does not own the requested session.",
    LifecycleErrorCode.STALE_STATE: "The requested control targets stale session state.",
    LifecycleErrorCode.PROCESS_EXITED: "The managed agent process has already exited.",
    LifecycleErrorCode.TIMEOUT: "The adapter operation exceeded its bounded time budget.",
    LifecycleErrorCode.PROTOCOL_VERSION: "The adapter protocol version is unsupported.",
}


@dataclass(frozen=True, slots=True)
class LifecycleError:
    code: LifecycleErrorCode
    message: str
    surface: str
    session_id: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        _require_surface(self.surface)
        if not self.message.strip():
            raise ValueError("lifecycle errors require a message")
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must be non-empty when provided")

    @classmethod
    def for_code(
        cls,
        code: LifecycleErrorCode,
        *,
        surface: str,
        session_id: str | None = None,
    ) -> LifecycleError:
        return cls(
            code=code,
            message=_LIFECYCLE_MESSAGES[code],
            surface=surface,
            session_id=session_id,
            retryable=code is LifecycleErrorCode.TIMEOUT,
        )


AdapterFailure: TypeAlias = CapabilityError | LifecycleError


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    surface: str
    supported: Collection[Capability]

    def __post_init__(self) -> None:
        _require_surface(self.surface)
        normalized = frozenset(self.supported)
        if not all(isinstance(capability, Capability) for capability in normalized):
            raise TypeError("supported capabilities must be Capability values")
        object.__setattr__(self, "supported", normalized)

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported

    def require(self, capability: Capability) -> CapabilityError | None:
        if self.supports(capability):
            return None
        return CapabilityError(
            code="capability_unavailable",
            capability=capability,
            surface=self.surface,
        )


class ManagedRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    repository_id: str = Field(min_length=1, max_length=256)
    repository_root: Path
    worktree_id: str = Field(min_length=1, max_length=256)
    worktree_root: Path
    prompt: str = Field(min_length=1, max_length=262_144)
    model: str = Field(min_length=1, max_length=256)
    effort: str = Field(min_length=1, max_length=64)
    sandbox: str = Field(min_length=1, max_length=128)
    permission_policy: str = Field(min_length=1, max_length=128)
    proof_contract_id: str = Field(min_length=1, max_length=256)
    max_tokens: int = Field(gt=0, le=1_000_000_000)
    max_cost_usd: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)

    @field_validator("repository_root", "worktree_root")
    @classmethod
    def _require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("repository and worktree paths must be absolute")
        return value


class ManagedStartEvidence(BaseModel):
    """Proof that managed execution is authorized before its first mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proof_contract_id: str = Field(min_length=1, max_length=256)
    baseline_id: str = Field(min_length=1, max_length=256)
    worktree_lease_id: str = Field(min_length=1, max_length=256)
    worktree_id: str = Field(min_length=1, max_length=256)
    worktree_root: Path
    captured_before_first_mutation: bool

    @field_validator("worktree_root")
    @classmethod
    def _require_absolute_worktree(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("managed evidence worktree root must be absolute")
        return value


@runtime_checkable
class AgentAdapter(Protocol):
    capabilities: AdapterCapabilities

    async def attach(self, session: SessionRef) -> AdapterFailure | None: ...

    async def start(self, request: ManagedRunRequest) -> SessionRef | AdapterFailure: ...

    async def interrupt(self, session: SessionRef) -> AdapterFailure | None: ...

    async def inject(self, session: SessionRef, context: str) -> AdapterFailure | None: ...

    async def resolve_action(
        self,
        session: SessionRef,
        action: ActionRequest,
    ) -> AdapterFailure | None: ...

    def events(
        self,
        session: SessionRef,
    ) -> AsyncIterator[ControlEvent | AdapterFailure]: ...


def _require_surface(surface: str) -> None:
    if not isinstance(surface, str) or not surface.strip() or len(surface) > 128:
        raise ValueError("adapter surface must be a non-empty bounded string")
