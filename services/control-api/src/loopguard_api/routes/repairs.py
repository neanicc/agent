from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..authorization import Principal, require_viewer
from ..errors import ApiProblem
from ..repair_workflow import RepairDetail, RepairPage, RepairWorkflowService


router = APIRouter(prefix="/v1/repairs", tags=["repairs"])


@router.get("", response_model=RepairPage)
async def list_repairs(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
    page_cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> RepairPage:
    service: RepairWorkflowService = request.app.state.repair_workflow_service
    try:
        records, next_cursor = service.list_page(
            principal.tenant_id,
            page_cursor=page_cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise ApiProblem("LGAPI-REQUEST-INVALID", field="page_cursor") from exc
    capability = {
        "available": service.workflow_registered,
        "reason": (
            "workflow_registered" if service.workflow_registered else "workflow_unavailable"
        ),
    }
    return RepairPage(
        items=[service.summary(record) for record in records],
        next_cursor=next_cursor,
        capability=capability,
    )


@router.get("/{repair_id}", response_model=RepairDetail)
async def read_repair(
    request: Request,
    repair_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> RepairDetail:
    service: RepairWorkflowService = request.app.state.repair_workflow_service
    value = service.read(principal.tenant_id, repair_id)
    if value is None:
        raise ApiProblem("LGAPI-NOT-FOUND")
    return service.detail(value)
