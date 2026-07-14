from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    kind: TargetKind
    target_id: str


class PolicyDecision(BaseModel):
    schema_version: Literal[1] = 1
    decision_id: str
    action: Literal["allow", "warn", "pause", "interrupt", "inject", "request_approval"]
    reason: str
    target: ActionTarget
    state_version: int
    state_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    schema_version: Literal[1] = 1
    action_id: str
    target: ActionTarget
    kind: ActionKind
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_state_version: int
    expected_state_hash: str
    nonce: str
    expires_at: datetime

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
