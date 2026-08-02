from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ActionKind(StrEnum):
    INTERRUPT = "interrupt"
    APPROVE = "approve"
    CONTINUE_ONCE = "continue_once"
    INJECT = "inject"
    PUBLISH_REPAIR = "publish_repair"


class TargetKind(StrEnum):
    SESSION = "session"
    REPAIR = "repair"
    HOST = "host"
    VERIFICATION = "verification"
    REPOSITORY = "repository"


class ActionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TargetKind
    target_id: str


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    decision_id: str
    action: Literal["allow", "warn", "pause", "interrupt", "inject", "request_approval"]
    reason: str
    target: ActionTarget
    state_version: int
    state_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    action_id: str
    target: ActionTarget
    kind: ActionKind
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_state_version: int
    expected_state_hash: str
    nonce: str
    expires_at: AwareDatetime

    def validate_state(
        self,
        *,
        current_state_version: int,
        current_state_hash: str,
    ) -> None:
        if (
            self.expected_state_version != current_state_version
            or self.expected_state_hash != current_state_hash
        ):
            raise ValueError("action request state mismatch")
