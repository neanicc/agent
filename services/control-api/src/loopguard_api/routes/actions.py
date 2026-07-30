from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..actions import ActionConflict, ActionService, DeviceProofRequired
from ..authorization import Principal, require_controller, require_viewer
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/actions", tags=["actions"])


class ActionTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["session", "repair", "host", "verification", "repository"]
    target_id: str = Field(min_length=1, max_length=512)
    host_id: str = Field(min_length=1, max_length=512)


class ActionChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: ActionTargetRequest
    device_id: uuid.UUID
    kind: Literal["interrupt", "approve", "continue_once", "inject", "publish_repair"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_state_version: int = Field(ge=0)
    expected_state_hash: str = Field(min_length=1, max_length=128)
    expires_in: int = Field(default=30, ge=1, le=300)


class SignedActionAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=256)
    device_id: uuid.UUID
    device_key_id: str = Field(min_length=1, max_length=256)
    device_algorithm: Literal["Ed25519", "P-256"]
    device_signature: str = Field(min_length=1, max_length=4096)


class ActionTargetView(BaseModel):
    kind: str
    target_id: str


class ActionView(BaseModel):
    action_id: str
    target: ActionTargetView
    target_label: str
    host_id: str
    kind: str
    state: str
    effect: str
    risk: Literal["medium", "high"]
    requires_biometric: bool
    parameters_hash: str
    expected_state: str
    expected_state_version: int
    expected_state_hash: str
    nonce: str
    canonical_payload: str
    host_available: bool
    issued_at: datetime
    expires_at: datetime
    executed_at: datetime | None


class ActionCollection(BaseModel):
    items: list[ActionView]
    next_cursor: str | None


class ActionChallengeView(BaseModel):
    action_id: str
    state: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    canonical_payload: str


class ActionAcceptanceView(BaseModel):
    action_id: str
    state: str
    executed_at: datetime | None


def _action_view(value: Any) -> dict[str, Any]:
    challenge = getattr(value, "challenge", value)
    canonical = json.loads(challenge.canonical_bytes)
    risk = "high" if challenge.kind in {"inject", "publish_repair"} else "medium"
    effects = {
        "interrupt": "Interrupt this run at its next safe host checkpoint.",
        "approve": "Approve the pending bounded host operation.",
        "continue_once": "Continue this paused run once from its verified state.",
        "inject": "Inject a bounded intervention into the selected run.",
        "publish_repair": "Publish the selected repair candidate as a draft change.",
    }
    return {
        "action_id": challenge.action_id,
        "target": {"kind": challenge.target_kind, "target_id": challenge.target_id},
        "target_label": f"{challenge.target_kind.capitalize()} {challenge.target_id}",
        "host_id": challenge.host_id,
        "kind": challenge.kind,
        "state": value.state,
        "effect": effects[challenge.kind],
        "risk": risk,
        "requires_biometric": risk == "high",
        "parameters_hash": canonical["parameters_hash"],
        "expected_state": (
            f"State version {challenge.expected_state_version} must still match "
            f"{challenge.expected_state_hash}."
        ),
        "expected_state_version": challenge.expected_state_version,
        "expected_state_hash": challenge.expected_state_hash,
        "nonce": challenge.nonce,
        "canonical_payload": base64.urlsafe_b64encode(challenge.canonical_bytes).decode(),
        "host_available": value.state not in {"host_offline", "revoked", "expired"},
        "issued_at": challenge.issued_at.isoformat(),
        "expires_at": challenge.expires_at.isoformat(),
        "executed_at": (
            value.executed_at.isoformat()
            if getattr(value, "executed_at", None) is not None
            else None
        ),
    }


@router.get("", response_model=ActionCollection)
async def list_actions(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    service: ActionService = request.app.state.action_service
    return {
        "items": [_action_view(item) for item in service.list(principal.tenant_id)],
        "next_cursor": None,
    }


@router.post(
    "/challenge",
    status_code=status.HTTP_201_CREATED,
    response_model=ActionChallengeView,
)
async def create_action_challenge(
    request: Request,
    body: ActionChallengeRequest,
    principal: Annotated[Principal, Depends(require_controller)],
) -> dict[str, Any]:
    service: ActionService = request.app.state.action_service
    challenge = service.create_challenge(
        principal=principal,
        requested_by_device_id=body.device_id,
        target_kind=body.target.kind,
        target_id=body.target.target_id,
        host_id=body.target.host_id,
        kind=body.kind,
        parameters=body.parameters,
        expected_state_version=body.expected_state_version,
        expected_state_hash=body.expected_state_hash,
        expires_in=body.expires_in,
    )
    return {
        "action_id": challenge.action_id,
        "state": challenge.state,
        "nonce": challenge.nonce,
        "issued_at": challenge.issued_at.isoformat(),
        "expires_at": challenge.expires_at.isoformat(),
        "canonical_payload": base64.urlsafe_b64encode(challenge.canonical_bytes).decode(),
    }


@router.get("/{action_id}", response_model=ActionView)
async def read_action(
    request: Request,
    action_id: str,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    service: ActionService = request.app.state.action_service
    value = service.read(action_id)
    challenge = getattr(value, "challenge", value)
    if value is None or challenge.tenant_id != principal.tenant_id:
        raise ApiProblem("LGAPI-NOT-FOUND")
    return _action_view(value)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ActionAcceptanceView,
)
async def accept_signed_action(
    request: Request,
    body: SignedActionAcceptance,
    _principal: Annotated[Principal, Depends(require_controller)],
) -> dict[str, Any]:
    service: ActionService = request.app.state.action_service
    try:
        record = service.accept_signed(
            body.action_id,
            device_id=body.device_id,
            device_key_id=body.device_key_id,
            device_algorithm=body.device_algorithm,
            device_signature=body.device_signature,
        )
    except DeviceProofRequired as exc:
        raise ApiProblem("LGAPI-DEVICE-PROOF-REQUIRED") from exc
    except ActionConflict as exc:
        raise ApiProblem("LGAPI-ACTION-CONFLICT") from exc
    return {
        "action_id": record.action_id,
        "state": record.state,
        "executed_at": record.executed_at,
    }
