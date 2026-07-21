from __future__ import annotations

import base64
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..actions import ActionConflict, ActionService, DeviceProofRequired
from ..authorization import Principal, require_controller
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
    device_algorithm: Literal["Ed25519"]
    device_signature: str = Field(min_length=1, max_length=4096)


@router.post("/challenge", status_code=status.HTTP_201_CREATED)
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
        "canonical_payload": base64.urlsafe_b64encode(
            challenge.canonical_bytes
        ).decode(),
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
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
