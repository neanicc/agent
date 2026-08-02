from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..actions import ActionConflict, ActionService, DeviceProofRequired
from ..authorization import Permission, Principal, require_controller, require_viewer
from ..capacity import CapacityExceeded, CapacityLimiter
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
    kind: Literal[
        "interrupt",
        "approve",
        "continue_once",
        "inject",
        "publish_repair",
        "cancel_repair",
        "retry_repair",
    ]
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
    risk = (
        "high"
        if challenge.kind in {"inject", "publish_repair", "cancel_repair"}
        else "medium"
    )
    effects = {
        "interrupt": "Interrupt this run at its next safe host checkpoint.",
        "approve": "Approve the pending bounded host operation.",
        "continue_once": "Continue this paused run once from its verified state.",
        "inject": "Inject a bounded intervention into the selected run.",
        "publish_repair": "Publish the selected repair candidate as a draft change.",
        "cancel_repair": "Cancel this repair and release its isolated resources.",
        "retry_repair": "Retry this repair from its immutable failure intake.",
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
    if body.kind in {"interrupt", "approve", "continue_once", "inject"}:
        if body.target.kind != "session":
            raise ApiProblem("LGAPI-FORBIDDEN")
        try:
            session_id = uuid.UUID(body.target.target_id)
            host_id = uuid.UUID(body.target.host_id)
        except ValueError as exc:
            raise ApiProblem("LGAPI-REQUEST-INVALID", field="target") from exc
        queries = request.app.state.control_queries
        if (
            queries.resource("sessions", principal.tenant_id, session_id) is None
            or queries.host(principal.tenant_id, host_id) is None
        ):
            # Keep resource existence private across tenant boundaries.
            raise ApiProblem("LGAPI-NOT-FOUND")
    if body.kind in {"publish_repair", "cancel_repair", "retry_repair"}:
        if (
            body.target.kind != "repair"
            or (
                body.kind == "publish_repair"
                and Permission.PUBLISH_REPAIR not in principal.permissions
            )
            or (
                body.kind != "publish_repair"
                and Permission.RUN_REPAIR not in principal.permissions
            )
        ):
            raise ApiProblem("LGAPI-FORBIDDEN")
        try:
            repair_id = uuid.UUID(body.target.target_id)
        except ValueError as exc:
            raise ApiProblem("LGAPI-REQUEST-INVALID", field="target.target_id") from exc
        repair = request.app.state.repair_workflow_service.read(
            principal.tenant_id,
            repair_id,
        )
        if repair is None:
            raise ApiProblem("LGAPI-NOT-FOUND")
        allowed_states = {
            "publish_repair": {"awaiting_publication"},
            "cancel_repair": {
                "intake",
                "building_fixture",
                "reproducing",
                "planning",
                "generating",
                "evaluating",
                "ranking",
                "rendering_report",
                "awaiting_publication",
                "failed",
            },
            "retry_repair": {"failed", "cancelled"},
        }
        if (
            repair.state not in allowed_states[body.kind]
            or repair.state_version != body.expected_state_version
            or repair.state_hash != body.expected_state_hash
        ):
            raise ApiProblem("LGAPI-ACTION-CONFLICT")
    service: ActionService = request.app.state.action_service
    limiter: CapacityLimiter = request.app.state.capacity_limiter
    try:
        limiter.admit_action(principal.tenant_id)
    except CapacityExceeded as exc:
        raise ApiProblem(
            "LGAPI-OVERLOADED",
            current_state={
                "resource": exc.resource,
                "retry_after_seconds": exc.retry_after_seconds,
            },
            retry_after_seconds=exc.retry_after_seconds,
        ) from exc
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
    principal: Annotated[Principal, Depends(require_controller)],
) -> dict[str, Any]:
    service: ActionService = request.app.state.action_service
    value = service.read(body.action_id)
    challenge = getattr(value, "challenge", value)
    if (
        value is None
        or challenge.tenant_id != principal.tenant_id
        or challenge.requested_by != principal.user_id
    ):
        raise ApiProblem("LGAPI-NOT-FOUND")
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
    if record.challenge.kind in {"publish_repair", "cancel_repair", "retry_repair"}:
        try:
            repair_id = uuid.UUID(record.challenge.target_id)
        except ValueError as exc:
            raise ApiProblem("LGAPI-ACTION-CONFLICT") from exc
        operation = {
            "publish_repair": request.app.state.repair_workflow_service.signal_publish,
            "cancel_repair": request.app.state.repair_workflow_service.cancel,
            "retry_repair": request.app.state.repair_workflow_service.retry,
        }[record.challenge.kind]
        accepted = await operation(
            repair_id,
            principal=principal,
            expected_state_version=record.challenge.expected_state_version,
            expected_state_hash=record.challenge.expected_state_hash,
            action_id=record.action_id,
        )
        if not accepted:
            raise ApiProblem("LGAPI-ACTION-CONFLICT")
        record = service.resolve_from_host(record.action_id, status="executed")
    return {
        "action_id": record.action_id,
        "state": record.state,
        "executed_at": record.executed_at,
    }
