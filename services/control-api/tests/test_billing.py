from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.authorization import Permission, Principal
from loopguard_api.billing import (
    BillingRejected,
    BillingService,
    BillingUnavailable,
    PriceCatalog,
    StripeBillingProvider,
    SubscriptionState,
    UnitPrice,
)
from loopguard_api.metering import UsageCategory, UsageLedger
from loopguard_api.settings import Settings


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def catalog(version: str, price: str, effective_at: datetime = NOW) -> PriceCatalog:
    return PriceCatalog(
        version=version,
        effective_at=effective_at,
        prices=(
            UnitPrice(
                category=UsageCategory.MANAGED_COMPUTE_SECONDS,
                unit_price_usd=Decimal(price),
            ),
        ),
    )


def test_invoice_reconciles_to_immutable_versioned_usage_ledger() -> None:
    tenant_id = uuid.uuid4()
    meter = UsageLedger(clock=lambda: NOW)
    billing = BillingService(meter, clock=lambda: NOW)
    billing.register_catalog(catalog("v1", "0.015"))
    billing.register_catalog(
        catalog("v2", "0.020", NOW + timedelta(hours=1))
    )
    meter.record(
        "usage-v1",
        tenant_id=tenant_id,
        category=UsageCategory.MANAGED_COMPUTE_SECONDS,
        units=3,
        catalog_version="v1",
        source="worker",
        observed_at=NOW,
    )
    meter.record(
        "usage-v2",
        tenant_id=tenant_id,
        category=UsageCategory.MANAGED_COMPUTE_SECONDS,
        units=3,
        catalog_version="v2",
        source="worker",
        observed_at=NOW + timedelta(hours=2),
    )

    invoice = billing.create_invoice(
        "invoice-1",
        tenant_id=tenant_id,
        started_at=NOW - timedelta(seconds=1),
        ended_at=NOW + timedelta(days=1),
    )
    reconciliation = billing.reconcile(invoice)

    assert [line.catalog_version for line in invoice.lines] == ["v1", "v2"]
    assert [line.amount_usd for line in invoice.lines] == [
        Decimal("0.05"),
        Decimal("0.06"),
    ]
    assert invoice.total_usd == Decimal("0.11")
    assert reconciliation.difference == 0


def test_refund_credit_is_append_only_and_invoice_id_is_immutable() -> None:
    tenant_id = uuid.uuid4()
    meter = UsageLedger(clock=lambda: NOW)
    billing = BillingService(meter, clock=lambda: NOW)
    billing.register_catalog(catalog("v1", "1.00"))
    meter.record(
        "usage",
        tenant_id=tenant_id,
        category=UsageCategory.MANAGED_COMPUTE_SECONDS,
        units=2,
        catalog_version="v1",
        source="worker",
    )
    first = billing.add_adjustment(
        "refund-1",
        tenant_id=tenant_id,
        amount_usd="-0.50",
        reason="provider interruption",
        kind="refund",
    )
    replay = billing.add_adjustment(
        "refund-1",
        tenant_id=tenant_id,
        amount_usd="-0.50",
        reason="provider interruption",
        kind="refund",
    )
    invoice = billing.create_invoice(
        "invoice-1",
        tenant_id=tenant_id,
        started_at=NOW - timedelta(seconds=1),
        ended_at=NOW + timedelta(seconds=1),
    )

    assert replay is first
    assert invoice.adjustments_usd == Decimal("-0.50")
    assert invoice.total_usd == Decimal("1.50")
    meter.record(
        "late",
        tenant_id=tenant_id,
        category=UsageCategory.MANAGED_COMPUTE_SECONDS,
        units=1,
        catalog_version="v1",
        source="worker",
    )
    with pytest.raises(BillingRejected, match="invoice ID conflicts"):
        billing.create_invoice(
            "invoice-1",
            tenant_id=tenant_id,
            started_at=NOW - timedelta(seconds=1),
            ended_at=NOW + timedelta(seconds=1),
        )


def webhook(
    service: BillingService,
    *,
    event_id: str,
    created: int,
    event_type: str,
    customer: str = "cus_tenant",
    status: str = "active",
) -> tuple[bytes, str]:
    body = json.dumps(
        {
            "id": event_id,
            "created": created,
            "type": event_type,
            "data": {
                "object": {
                    "customer": customer,
                    "status": status,
                    "subscription": "sub_tenant",
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    return body, service.sign_webhook_for_test(
        body,
        timestamp=int(NOW.timestamp()),
    )


def test_webhooks_are_signature_verified_replay_safe_and_ordered() -> None:
    tenant_id = uuid.uuid4()
    service = BillingService(UsageLedger(), clock=lambda: NOW)
    service.bind_customer(
        tenant_id,
        provider_customer_id="cus_tenant",
    )
    failed_body, failed_signature = webhook(
        service,
        event_id="evt_failed",
        created=20,
        event_type="invoice.payment_failed",
    )

    forged = failed_signature[:-1] + (
        "0" if failed_signature[-1] != "0" else "1"
    )
    with pytest.raises(BillingRejected, match="signature"):
        service.handle_webhook(failed_body, forged)
    assert service.handle_webhook(failed_body, failed_signature) is True
    assert service.handle_webhook(failed_body, failed_signature) is False
    assert service.subscription(tenant_id).state == SubscriptionState.GRACE  # type: ignore[union-attr]

    stale_body, stale_signature = webhook(
        service,
        event_id="evt_stale",
        created=19,
        event_type="invoice.paid",
    )
    assert service.handle_webhook(stale_body, stale_signature) is False
    assert service.subscription(tenant_id).state == SubscriptionState.GRACE  # type: ignore[union-attr]

    paid_body, paid_signature = webhook(
        service,
        event_id="evt_paid",
        created=21,
        event_type="invoice.paid",
    )
    assert service.handle_webhook(paid_body, paid_signature) is True
    assert service.subscription(tenant_id).state == SubscriptionState.ACTIVE  # type: ignore[union-attr]


def test_failed_payment_grace_blocks_only_new_hosted_expensive_work() -> None:
    current = NOW
    tenant_id = uuid.uuid4()
    service = BillingService(
        UsageLedger(),
        clock=lambda: current,
        grace_period=timedelta(days=2),
    )
    service.bind_customer(tenant_id, provider_customer_id="cus_tenant")
    body, signature = webhook(
        service,
        event_id="evt_failed",
        created=20,
        event_type="invoice.payment_failed",
    )
    service.handle_webhook(body, signature)

    assert service.hosted_work_allowed(tenant_id) is True
    current += timedelta(days=3)
    assert service.hosted_work_allowed(tenant_id) is False
    assert service.subscription(tenant_id).state == SubscriptionState.BLOCKED  # type: ignore[union-attr]


def test_provider_outage_preserves_state_and_tenant_deletion_anonymizes_ledger() -> None:
    tenant_id = uuid.uuid4()
    meter = UsageLedger(clock=lambda: NOW)
    service = BillingService(meter, clock=lambda: NOW)
    service.bind_customer(tenant_id, provider_customer_id="cus_tenant")
    meter.record(
        "usage",
        tenant_id=tenant_id,
        category=UsageCategory.RETAINED_EVENTS,
        units=1,
        catalog_version="v1",
        source="relay",
    )

    with pytest.raises(BillingUnavailable, match="unavailable"):
        service.checkout_url(tenant_id, "https://app.loopguard.dev/billing")
    assert service.subscription(tenant_id).state == SubscriptionState.ACTIVE  # type: ignore[union-attr]

    service.delete_tenant(tenant_id)
    assert service.subscription(tenant_id) is None
    assert meter.entries(tenant_id) == ()


def test_stripe_adapter_sends_only_provider_ids_and_bounded_redirects() -> None:
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum: int) -> bytes:
            assert maximum == 64 * 1024
            return b'{"url":"https://checkout.stripe.com/session/test"}'

    def opener(request, *, timeout: float):
        requests.append((request, timeout))
        return Response()

    provider = StripeBillingProvider(
        api_key="sk_test_reference",
        subscription_price_id="price_loopguard",
        opener=opener,
    )
    url = provider.checkout_url(
        provider_customer_id="cus_tenant",
        return_url="https://app.loopguard.dev/billing/complete",
    )

    assert url.startswith("https://checkout.stripe.com/")
    request, timeout = requests[0]
    assert timeout == 10
    assert request.full_url == "https://api.stripe.com/v1/checkout/sessions"
    assert request.headers["Authorization"] == "Bearer sk_test_reference"
    fields = parse_qs(request.data.decode())
    assert fields["customer"] == ["cus_tenant"]
    assert fields["line_items[0][price]"] == ["price_loopguard"]
    assert not any("card" in key or "payment_method" in key for key in fields)


def test_billing_api_is_tenant_scoped_and_webhook_is_signed() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    class StaticAuth:
        async def authenticate(self, _token: str) -> Principal:
            return Principal(
                tenant_id,
                user_id,
                "owner",
                "owner",
                frozenset(Permission),
            )

    class Provider:
        def checkout_url(
            self,
            *,
            provider_customer_id: str,
            return_url: str,
        ) -> str:
            assert provider_customer_id == "cus_tenant"
            assert return_url.startswith("https://app.loopguard.dev/")
            return "https://checkout.stripe.com/session/test"

        def portal_url(
            self,
            *,
            provider_customer_id: str,
            return_url: str,
        ) -> str:
            return "https://billing.stripe.com/session/test"

    service = BillingService(
        UsageLedger(),
        provider=Provider(),
        clock=lambda: NOW,
        allowed_return_origins=("https://app.loopguard.dev",),
    )
    service.bind_customer(tenant_id, provider_customer_id="cus_tenant")
    app = create_app(
        Settings.for_test(allowed_origins=("https://app.loopguard.dev",)),
        auth_service=StaticAuth(),  # type: ignore[arg-type]
        billing_service=service,
        usage_ledger=service.ledger,
    )
    body, signature = webhook(
        service,
        event_id="evt_api_paid",
        created=30,
        event_type="invoice.paid",
    )

    with TestClient(app) as client:
        summary = client.get(
            "/v1/billing",
            headers={"Authorization": "Bearer owner"},
        )
        checkout = client.post(
            "/v1/billing/checkout",
            headers={"Authorization": "Bearer owner"},
            json={"return_url": "https://app.loopguard.dev/billing"},
        )
        denied_redirect = client.post(
            "/v1/billing/checkout",
            headers={"Authorization": "Bearer owner"},
            json={"return_url": "https://evil.example/steal"},
        )
        accepted = client.post(
            "/v1/billing/webhooks/stripe",
            content=body,
            headers={
                "Stripe-Signature": signature,
                "Content-Type": "application/json",
            },
        )

    assert summary.json()["payment_data_stored"] is False
    assert summary.json()["local_guarding_available"] is True
    assert checkout.status_code == 200
    assert denied_redirect.status_code == 422
    assert accepted.status_code == 202
    assert accepted.json() == {"processed": True}
