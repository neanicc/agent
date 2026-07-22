from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ..authorization import Principal, require_viewer
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/repairs", tags=["repairs"])


@router.get("")
async def list_repairs(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    return {
        "items": request.app.state.control_queries.resources("repairs", principal.tenant_id),
        "next_cursor": None,
        "capability": {"available": False, "reason": "feature_flag_disabled"},
    }


@router.get("/{repair_id}")
async def read_repair(
    request: Request,
    repair_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    value = request.app.state.control_queries.resource("repairs", principal.tenant_id, repair_id)
    if value is None:
        raise ApiProblem("LGAPI-NOT-FOUND")
    return {**value, "capability": {"available": False, "reason": "feature_flag_disabled"}}
