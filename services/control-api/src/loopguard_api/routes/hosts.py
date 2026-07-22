from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import Principal, require_device_manager, require_viewer
from ..errors import ApiProblem
from ..pairing import PairingConflict, PairingExpired, PairingService


router = APIRouter(prefix="/v1/hosts", tags=["hosts"])


class PairingCodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    expires_at: datetime


class HostPairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=20, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    public_key: str = Field(min_length=1, max_length=4096)
    algorithm: str


@router.get("")
async def list_hosts(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, object]:
    return {"items": request.app.state.control_queries.hosts(principal.tenant_id)}


@router.post("/pairing-codes", response_model=PairingCodeResponse)
async def create_pairing_code(
    request: Request,
    principal: Annotated[Principal, Depends(require_device_manager)],
) -> PairingCodeResponse:
    service: PairingService = request.app.state.pairing_service
    issued = service.create_code(
        tenant_id=principal.tenant_id, created_by=principal.user_id
    )
    return PairingCodeResponse(code=issued.code, expires_at=issued.expires_at)


@router.post("/pair", status_code=status.HTTP_201_CREATED)
async def pair_host(request: Request, body: HostPairingRequest) -> dict[str, Any]:
    service: PairingService = request.app.state.pairing_service
    try:
        host = service.consume(
            body.code,
            name=body.name,
            public_key=body.public_key,
            algorithm=body.algorithm,
        )
    except PairingExpired as exc:
        raise ApiProblem("LGAPI-PAIRING-EXPIRED") from exc
    except PairingConflict as exc:
        raise ApiProblem("LGAPI-PAIRING-CONFLICT") from exc
    return {
        "id": str(host.id),
        "tenant_id": str(host.tenant_id),
        "name": host.name,
        "algorithm": host.algorithm,
    }


@router.get("/{host_id}")
async def read_host(
    request: Request,
    host_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    host = request.app.state.control_queries.host(principal.tenant_id, host_id)
    if host is None:
        raise ApiProblem("LGAPI-NOT-FOUND")
    return host
