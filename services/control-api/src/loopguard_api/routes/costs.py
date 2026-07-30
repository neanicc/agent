from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ..authorization import Principal, require_viewer
from ..client_queries import ControlQueryService


router = APIRouter(prefix="/v1/costs", tags=["costs"])


class ObservedCostView(BaseModel):
    agent: str
    judge: str
    verification: str
    critic: str
    repair: str


class CostSummaryView(BaseModel):
    window: Literal["24h", "7d", "30d", "90d"]
    currency: Literal["USD"]
    observed: ObservedCostView
    estimated_avoided_cost: str | None


@router.get("", response_model=CostSummaryView)
async def read_costs(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
    window: Literal["24h", "7d", "30d", "90d"] = Query("30d"),
) -> dict[str, object]:
    service: ControlQueryService = request.app.state.control_queries
    return service.cost_summary(principal.tenant_id, window)
