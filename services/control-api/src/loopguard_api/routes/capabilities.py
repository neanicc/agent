from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ..authorization import Principal, require_viewer
from ..client_queries import ControlQueryService


router = APIRouter(prefix="/v1/capabilities", tags=["capabilities"])


@router.get("")
async def effective_capabilities(
    request: Request,
    host_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    service: ControlQueryService = request.app.state.control_queries
    return service.capabilities(principal, host_id)
