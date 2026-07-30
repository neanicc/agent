from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ..authorization import Principal, require_viewer
from ..client_queries import ControlQueryService


router = APIRouter(prefix="/v1/audit", tags=["audit"])


class AuditEntryView(BaseModel):
    id: str
    action: str
    target_kind: str
    target_id: str
    created_at: datetime
    actor: str | None = None
    result: str | None = None
    request_id: str | None = None


class AuditCollectionView(BaseModel):
    items: list[AuditEntryView]
    next_cursor: str | None


@router.get("", response_model=AuditCollectionView)
async def list_audit(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
    page_cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, object]:
    service: ControlQueryService = request.app.state.control_queries
    return {
        "items": service.audit(principal.tenant_id, limit=limit),
        "next_cursor": None,
    }
