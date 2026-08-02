from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ..authorization import Principal, require_viewer
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/changes", tags=["changes"])


@router.get("")
async def list_changes(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    return {"items": request.app.state.control_queries.resources("changes", principal.tenant_id), "next_cursor": None}


@router.get("/{change_id}")
async def read_change(
    request: Request,
    change_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    value = request.app.state.control_queries.resource("changes", principal.tenant_id, change_id)
    if value is None:
        raise ApiProblem("LGAPI-NOT-FOUND")
    return value
