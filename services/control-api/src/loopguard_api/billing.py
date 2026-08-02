from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .metering import UsageCategory, UsageLedger


class BillingRejected(ValueError):
    pass


class BillingUnavailable(RuntimeError):
    pass


class SubscriptionState(StrEnum):
    ACTIVE = "active"
    GRACE = "grace"
    BLOCKED = "blocked"
    DELETED = "deleted"


class UnitPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: UsageCategory
    unit_price_usd: Decimal = Field(ge=0, max_digits=20, decimal_places=10)

    @field_validator("unit_price_usd")
    @classmethod
    def finite_price(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("unit price must be finite")
        return value


class PriceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=64)
    effective_at: datetime
    prices: tuple[UnitPrice, ...]
    currency: str = Field(default="USD", pattern="^USD$")

    def price(self, category: UsageCategory) -> Decimal:
        matches = [
            value.unit_price_usd
            for value in self.prices
            if value.category == category
        ]
        if len(matches) != 1:
            raise BillingRejected(
                f"catalog {self.version} has no unique price for {category}"
            )
        return matches[0]


class InvoiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: UsageCategory
    catalog_version: str
    units: Decimal
    unit_price_usd: Decimal
    amount_usd: Decimal


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invoice_id: str
    tenant_id: uuid.UUID
    started_at: datetime
    ended_at: datetime
    currency: str = "USD"
    lines: tuple[InvoiceLine, ...]
    adjustments_usd: Decimal
    total_usd: Decimal


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_usd: Decimal
    invoiced_usd: Decimal
    difference: Decimal


class BillingAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adjustment_id: str
    tenant_id: uuid.UUID
    amount_usd: Decimal
    reason: str
    kind: str
    recorded_at: datetime

    @field_validator("amount_usd")
    @classmethod
    def finite_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value == 0:
            raise ValueError("billing adjustment must be finite and non-zero")
        return value


@dataclass(frozen=True, slots=True)
class Subscription:
    tenant_id: uuid.UUID
    provider_customer_id: str
    provider_subscription_id: str | None
    state: SubscriptionState
    latest_provider_event_created: int
    grace_until: datetime | None = None


class BillingProvider(Protocol):
    def checkout_url(
        self,
        *,
        provider_customer_id: str,
        return_url: str,
    ) -> str: ...

    def portal_url(
        self,
        *,
        provider_customer_id: str,
        return_url: str,
    ) -> str: ...


class StripeBillingProvider:
    """Minimal Stripe Checkout/Portal adapter; card data never crosses LoopGuard."""

    def __init__(
        self,
        *,
        api_key: str,
        subscription_price_id: str,
        timeout_seconds: float = 10,
        opener=urlopen,
    ) -> None:
        if (
            not api_key.startswith("sk_")
            or len(api_key) > 512
            or not subscription_price_id.startswith("price_")
            or len(subscription_price_id) > 256
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("Stripe billing adapter configuration is invalid")
        self.api_key = api_key
        self.subscription_price_id = subscription_price_id
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    def checkout_url(
        self,
        *,
        provider_customer_id: str,
        return_url: str,
    ) -> str:
        return self._post(
            "checkout/sessions",
            {
                "customer": provider_customer_id,
                "mode": "subscription",
                "line_items[0][price]": self.subscription_price_id,
                "line_items[0][quantity]": "1",
                "success_url": return_url,
                "cancel_url": return_url,
            },
        )

    def portal_url(
        self,
        *,
        provider_customer_id: str,
        return_url: str,
    ) -> str:
        return self._post(
            "billing_portal/sessions",
            {
                "customer": provider_customer_id,
                "return_url": return_url,
            },
        )

    def _post(self, endpoint: str, fields: dict[str, str]) -> str:
        request = UrlRequest(
            f"https://api.stripe.com/v1/{endpoint}",
            data=urlencode(fields).encode("ascii"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read(64 * 1024))
            url = payload["url"]
        except Exception as exc:
            raise BillingUnavailable("billing provider is unavailable") from exc
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2_048:
            raise BillingUnavailable("billing provider returned an invalid URL")
        return url


class BillingService:
    def __init__(
        self,
        ledger: UsageLedger,
        *,
        provider: BillingProvider | None = None,
        webhook_secret: bytes = b"loopguard-test-stripe-webhook-secret",
        clock=lambda: datetime.now(UTC),
        grace_period: timedelta = timedelta(days=7),
        allowed_return_origins: tuple[str, ...] = ("https://app.loopguard.dev",),
    ) -> None:
        if len(webhook_secret) < 16:
            raise ValueError("billing webhook secret must contain at least 16 bytes")
        self.ledger = ledger
        self.provider = provider
        self.webhook_secret = bytes(webhook_secret)
        self.clock = clock
        self.grace_period = grace_period
        self.allowed_return_origins = frozenset(
            origin.rstrip("/") for origin in allowed_return_origins
        )
        self._catalogs: dict[str, PriceCatalog] = {}
        self._subscriptions: dict[uuid.UUID, Subscription] = {}
        self._customer_tenants: dict[str, uuid.UUID] = {}
        self._webhook_events: set[str] = set()
        self._adjustments: dict[tuple[uuid.UUID, str], BillingAdjustment] = {}
        self._invoices: dict[tuple[uuid.UUID, str], Invoice] = {}
        self._lock = threading.RLock()

    @property
    def durable(self) -> bool:
        return bool(getattr(self.ledger, "durable", False)) and self.provider is not None

    def register_catalog(self, catalog: PriceCatalog) -> None:
        if len({price.category for price in catalog.prices}) != len(catalog.prices):
            raise BillingRejected("price catalog categories must be unique")
        with self._lock:
            existing = self._catalogs.get(catalog.version)
            if existing is not None and existing != catalog:
                raise BillingRejected("price catalog versions are immutable")
            self._catalogs[catalog.version] = catalog

    def bind_customer(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_customer_id: str,
        provider_subscription_id: str | None = None,
    ) -> Subscription:
        if (
            not provider_customer_id.startswith("cus_")
            or len(provider_customer_id) > 256
            or (
                provider_subscription_id is not None
                and (
                    not provider_subscription_id.startswith("sub_")
                    or len(provider_subscription_id) > 256
                )
            )
        ):
            raise BillingRejected("provider identifiers are invalid")
        with self._lock:
            owner = self._customer_tenants.get(provider_customer_id)
            if owner is not None and owner != tenant_id:
                raise BillingRejected("provider customer is already bound")
            subscription = Subscription(
                tenant_id,
                provider_customer_id,
                provider_subscription_id,
                SubscriptionState.ACTIVE,
                0,
            )
            self._subscriptions[tenant_id] = subscription
            self._customer_tenants[provider_customer_id] = tenant_id
            return subscription

    def subscription(self, tenant_id: uuid.UUID) -> Subscription | None:
        value = self._subscriptions.get(tenant_id)
        if (
            value is not None
            and value.state == SubscriptionState.GRACE
            and value.grace_until is not None
            and self.clock() > value.grace_until
        ):
            value = Subscription(
                value.tenant_id,
                value.provider_customer_id,
                value.provider_subscription_id,
                SubscriptionState.BLOCKED,
                value.latest_provider_event_created,
                value.grace_until,
            )
            self._subscriptions[tenant_id] = value
        return value

    def hosted_work_allowed(self, tenant_id: uuid.UUID) -> bool:
        value = self.subscription(tenant_id)
        return value is None or value.state in {
            SubscriptionState.ACTIVE,
            SubscriptionState.GRACE,
        }

    def checkout_url(self, tenant_id: uuid.UUID, return_url: str) -> str:
        subscription = self._required_subscription(tenant_id)
        return self._provider_call(
            "checkout",
            subscription.provider_customer_id,
            return_url,
        )

    def portal_url(self, tenant_id: uuid.UUID, return_url: str) -> str:
        subscription = self._required_subscription(tenant_id)
        return self._provider_call(
            "portal",
            subscription.provider_customer_id,
            return_url,
        )

    def add_adjustment(
        self,
        adjustment_id: str,
        *,
        tenant_id: uuid.UUID,
        amount_usd: Decimal | str,
        reason: str,
        kind: str,
    ) -> BillingAdjustment:
        key = (tenant_id, adjustment_id)
        with self._lock:
            existing = self._adjustments.get(key)
            if existing is not None:
                if (
                    existing.amount_usd != Decimal(amount_usd)
                    or existing.reason != reason
                    or existing.kind != kind
                ):
                    raise BillingRejected("adjustment ID conflicts")
                return existing
            adjustment = BillingAdjustment(
                adjustment_id=adjustment_id,
                tenant_id=tenant_id,
                amount_usd=Decimal(amount_usd),
                reason=reason,
                kind=kind,
                recorded_at=self.clock(),
            )
            self._adjustments[key] = adjustment
        return adjustment

    def create_invoice(
        self,
        invoice_id: str,
        *,
        tenant_id: uuid.UUID,
        started_at: datetime,
        ended_at: datetime,
    ) -> Invoice:
        if started_at >= ended_at:
            raise BillingRejected("invoice period is invalid")
        grouped: dict[tuple[UsageCategory, str], Decimal] = {}
        for entry in self.ledger.entries(
            tenant_id,
            started_at=started_at,
            ended_at=ended_at,
        ):
            key = (entry.category, entry.catalog_version)
            grouped[key] = grouped.get(key, Decimal("0")) + entry.units
        lines: list[InvoiceLine] = []
        for (category, version), units in sorted(
            grouped.items(),
            key=lambda value: (value[0][1], value[0][0].value),
        ):
            try:
                catalog = self._catalogs[version]
            except KeyError as exc:
                raise BillingRejected(
                    f"usage references unknown catalog {version}"
                ) from exc
            price = catalog.price(category)
            lines.append(
                InvoiceLine(
                    category=category,
                    catalog_version=version,
                    units=units,
                    unit_price_usd=price,
                    amount_usd=_money(units * price),
                )
            )
        adjustments = _money(
            sum(
                (
                    value.amount_usd
                    for value in self._adjustments.values()
                    if value.tenant_id == tenant_id
                    and started_at <= value.recorded_at < ended_at
                ),
                Decimal("0"),
            )
        )
        invoice = Invoice(
            invoice_id=invoice_id,
            tenant_id=tenant_id,
            started_at=started_at,
            ended_at=ended_at,
            lines=tuple(lines),
            adjustments_usd=adjustments,
            total_usd=_money(
                sum((line.amount_usd for line in lines), Decimal("0"))
                + adjustments
            ),
        )
        key = (tenant_id, invoice_id)
        with self._lock:
            existing = self._invoices.get(key)
            if existing is not None and existing != invoice:
                raise BillingRejected("invoice ID conflicts with immutable ledger")
            self._invoices[key] = invoice
        return invoice

    def reconcile(self, invoice: Invoice) -> Reconciliation:
        expected = self.create_invoice(
            f"reconcile:{invoice.invoice_id}",
            tenant_id=invoice.tenant_id,
            started_at=invoice.started_at,
            ended_at=invoice.ended_at,
        )
        return Reconciliation(
            expected_usd=expected.total_usd,
            invoiced_usd=invoice.total_usd,
            difference=_money(invoice.total_usd - expected.total_usd),
        )

    def sign_webhook_for_test(
        self,
        body: bytes,
        *,
        timestamp: int,
    ) -> str:
        signature = hmac.new(
            self.webhook_secret,
            str(timestamp).encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        return f"t={timestamp},v1={signature}"

    def handle_webhook(self, body: bytes, signature_header: str) -> bool:
        timestamp, signature = _stripe_signature(signature_header)
        now = int(self.clock().timestamp())
        if abs(now - timestamp) > 300:
            raise BillingRejected("billing webhook timestamp is stale")
        expected = hmac.new(
            self.webhook_secret,
            str(timestamp).encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise BillingRejected("billing webhook signature is invalid")
        try:
            event = json.loads(body)
            event_id = event["id"]
            created = int(event["created"])
            event_type = event["type"]
            payload = event["data"]["object"]
            customer_id = payload["customer"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BillingRejected("billing webhook payload is invalid") from exc
        if (
            not isinstance(event_id, str)
            or not event_id.startswith("evt_")
            or not isinstance(event_type, str)
            or not isinstance(customer_id, str)
        ):
            raise BillingRejected("billing webhook identifiers are invalid")
        with self._lock:
            if event_id in self._webhook_events:
                return False
            tenant_id = self._customer_tenants.get(customer_id)
            if tenant_id is None:
                raise BillingRejected("billing customer is not bound")
            self._webhook_events.add(event_id)
            current = self._subscriptions[tenant_id]
            if created <= current.latest_provider_event_created:
                return False
            state = current.state
            grace_until = current.grace_until
            provider_subscription_id = current.provider_subscription_id
            if event_type == "invoice.payment_failed":
                state = SubscriptionState.GRACE
                grace_until = self.clock() + self.grace_period
            elif event_type in {
                "customer.subscription.deleted",
                "customer.subscription.paused",
            }:
                state = SubscriptionState.BLOCKED
            elif event_type in {
                "invoice.paid",
                "customer.subscription.created",
                "customer.subscription.updated",
            }:
                raw_status = payload.get("status", "active")
                state = (
                    SubscriptionState.ACTIVE
                    if raw_status in {"active", "trialing"}
                    else SubscriptionState.BLOCKED
                )
                grace_until = None
                if isinstance(payload.get("subscription"), str):
                    provider_subscription_id = payload["subscription"]
                elif isinstance(payload.get("id"), str) and payload["id"].startswith(
                    "sub_"
                ):
                    provider_subscription_id = payload["id"]
            self._subscriptions[tenant_id] = Subscription(
                tenant_id,
                customer_id,
                provider_subscription_id,
                state,
                created,
                grace_until,
            )
        return True

    def delete_tenant(self, tenant_id: uuid.UUID) -> None:
        with self._lock:
            subscription = self._subscriptions.pop(tenant_id, None)
            if subscription is not None:
                self._customer_tenants.pop(
                    subscription.provider_customer_id,
                    None,
                )
            self.ledger.anonymize_tenant(tenant_id)

    def _required_subscription(self, tenant_id: uuid.UUID) -> Subscription:
        value = self.subscription(tenant_id)
        if value is None:
            raise BillingRejected("tenant has no billing customer")
        return value

    def _provider_call(
        self,
        kind: str,
        customer_id: str,
        return_url: str,
    ) -> str:
        parsed = urlsplit(return_url)
        origin = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc
            else ""
        )
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or origin not in self.allowed_return_origins
            or len(return_url) > 2_048
        ):
            raise BillingRejected("billing return URL is not allowed")
        if self.provider is None:
            raise BillingUnavailable("billing provider is unavailable")
        try:
            if kind == "checkout":
                return self.provider.checkout_url(
                    provider_customer_id=customer_id,
                    return_url=return_url,
                )
            return self.provider.portal_url(
                provider_customer_id=customer_id,
                return_url=return_url,
            )
        except Exception as exc:
            raise BillingUnavailable("billing provider is unavailable") from exc


def _stripe_signature(header: str) -> tuple[int, str]:
    values: dict[str, str] = {}
    for item in header.split(","):
        key, separator, value = item.partition("=")
        if separator and key in {"t", "v1"} and key not in values:
            values[key] = value
    try:
        timestamp = int(values["t"])
        signature = values["v1"]
    except (KeyError, ValueError) as exc:
        raise BillingRejected("billing webhook signature header is invalid") from exc
    if len(signature) != 64 or any(
        character not in "0123456789abcdef" for character in signature
    ):
        raise BillingRejected("billing webhook signature header is invalid")
    return timestamp, signature


def _money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise BillingRejected("billing amount must be finite")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
