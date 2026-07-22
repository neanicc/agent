from __future__ import annotations

import base64
import uuid
from typing import Annotated, Literal

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


def _device(device: DeviceRecord) -> dict[str, str | None]:
    return {
        "id": str(device.id),
        "name": device.name,
        "algorithm": device.algorithm,
        "key_id": device.key_id,
        "created_at": device.created_at.isoformat(),
        "revoked_at": device.revoked_at.isoformat() if device.revoked_at else None,
    }


@router.get("")
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
    challenge = service.start(
        tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    return {
        "pairing_id": challenge.pairing_id,
        "challenge": base64.urlsafe_b64encode(challenge.challenge).decode(),
        "expires_at": challenge.expires_at.isoformat(),
    }


@router.post("/pairing/complete", status_code=status.HTTP_201_CREATED)
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
