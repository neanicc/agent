from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from ..authorization import (
    Principal,
    require_policy_manager,
    require_viewer,
)
from ..billing import (
    BillingRejected,
    BillingService,
    BillingUnavailable,
)
from ..errors import ApiProblem


router = APIRouter(prefix="/v1/billing", tags=["billing"])


class BillingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["unconfigured", "active", "grace", "blocked", "deleted"]
    grace_until: datetime | None
    hosted_work_allowed: bool
    local_guarding_available: Literal[True] = True
    payment_data_stored: Literal[False] = False


class BillingRedirectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_url: str = Field(min_length=1, max_length=2_048)


class BillingRedirect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


@router.get("", response_model=BillingSummary)
async def read_billing(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> BillingSummary:
    service: BillingService = request.app.state.billing_service
    subscription = service.subscription(principal.tenant_id)
    return BillingSummary(
        state="unconfigured" if subscription is None else subscription.state,
        grace_until=None if subscription is None else subscription.grace_until,
        hosted_work_allowed=service.hosted_work_allowed(principal.tenant_id),
    )


@router.post("/checkout", response_model=BillingRedirect)
async def create_checkout(
    request: Request,
    body: BillingRedirectRequest,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> BillingRedirect:
    return BillingRedirect(
        url=_provider_url(
            lambda: request.app.state.billing_service.checkout_url(
                principal.tenant_id,
                body.return_url,
            )
        )
    )


@router.post("/portal", response_model=BillingRedirect)
async def create_portal(
    request: Request,
    body: BillingRedirectRequest,
    principal: Annotated[Principal, Depends(require_policy_manager)],
) -> BillingRedirect:
    return BillingRedirect(
        url=_provider_url(
            lambda: request.app.state.billing_service.portal_url(
                principal.tenant_id,
                body.return_url,
            )
        )
    )


@router.post("/webhooks/stripe", status_code=202)
async def stripe_webhook(
    request: Request,
    stripe_signature: Annotated[str, Header()],
) -> dict[str, bool]:
    body = await request.body()
    service: BillingService = request.app.state.billing_service
    try:
        processed = service.handle_webhook(body, stripe_signature)
    except BillingRejected as exc:
        raise ApiProblem("LGAPI-BILLING-WEBHOOK-INVALID") from exc
    return {"processed": processed}


def _provider_url(callback) -> str:
    try:
        return callback()
    except BillingUnavailable as exc:
        raise ApiProblem("LGAPI-BILLING-UNAVAILABLE") from exc
    except BillingRejected as exc:
        raise ApiProblem("LGAPI-REQUEST-INVALID", field="return_url") from exc
