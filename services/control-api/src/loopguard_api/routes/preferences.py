from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import Principal, require_policy_manager, require_viewer
from ..client_queries import ControlQueryService, ManagedRuleWeakened
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/preferences", tags=["preferences"])


class PreferenceRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=256)
    severity: Literal["inform", "warn", "block"]


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[PreferenceRuleUpdate] = Field(max_length=500)


class PreferenceRuleView(BaseModel):
    id: str
    severity: Literal["inform", "warn", "block"]
    managed: bool
    source: Literal["organization", "profile"]
    precedence: Literal["managed minimum", "profile override"]
    affected_capabilities: list[str]


class PreferenceProfileView(BaseModel):
    profile_version: int
    source_manifest_hash: str
    rules: list[PreferenceRuleView]
    updated_by: str | None = None


@router.get("", response_model=PreferenceProfileView)
async def read_preferences(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> dict[str, object]:
    service: ControlQueryService = request.app.state.control_queries
    return service.read_preferences(principal.tenant_id)


@router.put("", response_model=PreferenceProfileView)
async def write_preferences(
    request: Request,
    body: PreferenceUpdate,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> dict[str, object]:
    service: ControlQueryService = request.app.state.control_queries
    try:
        return service.write_preferences(
            principal.tenant_id,
            principal.user_id,
            [rule.model_dump() for rule in body.rules],
        )
    except ManagedRuleWeakened as exc:
        raise ApiProblem("LGAPI-MANAGED-RULE-WEAKENED") from exc
