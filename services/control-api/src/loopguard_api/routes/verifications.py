from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ..authorization import Principal, require_viewer
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/verifications", tags=["verifications"])


@router.get("")
async def list_verifications(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    return {"items": request.app.state.control_queries.resources("verifications", principal.tenant_id), "next_cursor": None}


@router.get("/{verification_id}")
async def read_verification(
    request: Request,
    verification_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    value = request.app.state.control_queries.resource("verifications", principal.tenant_id, verification_id)
    if value is None:
        raise ApiProblem("LGAPI-NOT-FOUND")
    return value
