from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..authorization import Principal, require_viewer
from ..client_queries import ControlQueryService


router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("")
async def list_audit(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
    page_cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    service: ControlQueryService = request.app.state.control_queries
    return {
        "items": service.audit(principal.tenant_id, limit=limit),
        "next_cursor": None,
    }
