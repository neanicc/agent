from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from ..authorization import Principal, require_viewer
from ..client_queries import ControlQueryService


router = APIRouter(prefix="/v1/costs", tags=["costs"])


@router.get("")
async def read_costs(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
    window: Literal["24h", "7d", "30d", "90d"] = Query("30d"),
) -> dict[str, Any]:
    service: ControlQueryService = request.app.state.control_queries
    return service.cost_summary(principal.tenant_id, window)
