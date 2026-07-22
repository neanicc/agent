from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import Principal, require_policy_manager, require_viewer
from ..client_queries import ControlQueryService, ManagedRuleWeakened
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/preferences", tags=["preferences"])


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[dict[str, Any]] = Field(max_length=500)


@router.get("")
async def read_preferences(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, Any]:
    service: ControlQueryService = request.app.state.control_queries
    return service.read_preferences(principal.tenant_id)


@router.put("")
async def write_preferences(
    request: Request,
    body: PreferenceUpdate,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> dict[str, Any]:
    service: ControlQueryService = request.app.state.control_queries
    try:
        return service.write_preferences(
            principal.tenant_id, principal.user_id, body.rules
        )
    except ManagedRuleWeakened as exc:
        raise ApiProblem("LGAPI-MANAGED-RULE-WEAKENED") from exc
