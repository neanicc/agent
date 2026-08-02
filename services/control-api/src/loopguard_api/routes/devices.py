from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Annotated, Literal

from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import Principal, require_device_manager, require_viewer
from ..device_pairing import DevicePairingRejected, DevicePairingService, DeviceRecord
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/devices", tags=["devices"])


class DevicePairingCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairing_id: str = Field(min_length=16, max_length=256)
    public_key_alg: Literal["Ed25519", "P-256"]
    public_key: str = Field(min_length=1, max_length=4096)
    signature: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=256)


class PushDestinationRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[0-9a-f]{64,200}$")
    environment: Literal["sandbox", "production"]


class PushDestinationRegistrationResponse(BaseModel):
    registered: bool


class DeviceView(BaseModel):
    id: uuid.UUID
    name: str
    algorithm: Literal["Ed25519", "P-256"]
    key_id: str
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


class DeviceCollectionView(BaseModel):
    items: list[DeviceView]


def _device(device: DeviceRecord) -> dict[str, str | None]:
    return {
        "id": str(device.id),
        "name": device.name,
        "algorithm": device.algorithm,
        "key_id": device.key_id,
        "created_at": device.created_at.isoformat(),
        "last_seen_at": device.last_seen_at.isoformat(),
        "revoked_at": device.revoked_at.isoformat() if device.revoked_at else None,
    }


@router.get("", response_model=DeviceCollectionView)
async def list_devices(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, object]:
    service: DevicePairingService = request.app.state.device_pairing_service
    return {"items": [_device(item) for item in service.list(principal.tenant_id)]}


@router.post("/pairing/start")
async def start_device_pairing(
    request: Request,
    principal: Annotated[Principal, Depends(require_device_manager)],
) -> dict[str, str]:
    service: DevicePairingService = request.app.state.device_pairing_service
    challenge = service.start(tenant_id=principal.tenant_id, user_id=principal.user_id)
    return {
        "pairing_id": challenge.pairing_id,
        "challenge": base64.urlsafe_b64encode(challenge.challenge).decode(),
        "expires_at": challenge.expires_at.isoformat(),
    }


@router.post(
    "/pairing/complete",
    status_code=status.HTTP_201_CREATED,
    response_model=DeviceView,
)
async def complete_device_pairing(
    request: Request,
    body: DevicePairingCompletion,
    principal: Annotated[Principal, Depends(require_device_manager)],
) -> dict[str, str | None]:
    service: DevicePairingService = request.app.state.device_pairing_service
    try:
        device = service.complete(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            pairing_id=body.pairing_id,
            public_key_alg=body.public_key_alg,
            public_key=body.public_key,
            signature=body.signature,
            name=body.name,
        )
    except DevicePairingRejected as exc:
        code = (
            "LGAPI-DEVICE-PAIRING-CONFLICT"
            if "consumed" in str(exc)
            else "LGAPI-DEVICE-PROOF-REQUIRED"
        )
        raise ApiProblem(code) from exc
    key_bytes = base64.b64decode(device.public_key, altchars=b"-_", validate=True)
    public_key = (
        Ed25519PublicKey.from_public_bytes(key_bytes)
        if device.algorithm == "Ed25519"
        else EllipticCurvePublicKey.from_encoded_point(SECP256R1(), key_bytes)
    )
    request.app.state.action_service.register_device(
        tenant_id=device.tenant_id,
        user_id=device.user_id,
        device_id=device.id,
        key_id=device.key_id,
        algorithm=device.algorithm,
        public_key=public_key,
    )
    return _device(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    request: Request,
    device_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_device_manager)],
) -> Response:
    service: DevicePairingService = request.app.state.device_pairing_service
    device = service.revoke(principal.tenant_id, device_id)
    if device is None:
        raise ApiProblem("LGAPI-NOT-FOUND")
    request.app.state.action_service.revoke_device(device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{device_id}/push-token",
    response_model=PushDestinationRegistrationResponse,
)
async def register_push_destination(
    request: Request,
    device_id: uuid.UUID,
    body: PushDestinationRegistration,
    principal: Annotated[Principal, Depends(require_device_manager)],
) -> dict[str, bool]:
    service: DevicePairingService = request.app.state.device_pairing_service
    device = service.register_push_destination(
        principal.tenant_id,
        device_id,
        environment=body.environment,
        token=body.token,
    )
    if device is None:
        raise ApiProblem("LGAPI-NOT-FOUND")
    return {"registered": True}
