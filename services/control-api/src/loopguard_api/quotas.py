from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Callable

from .metering import UsageCategory, UsageEntry, UsageLedger


class QuotaExceeded(ValueError):
    pass


class QuotaConflict(ValueError):
    pass


class OperationClass(StrEnum):
    HOSTED_EXPENSIVE = "hosted_expensive"
    LOCAL_GUARDING = "local_guarding"
    READ = "read"
    EXPORT = "export"
    DELETE = "delete"
    SECURITY_ACTION = "security_action"


_ALWAYS_AVAILABLE = {
    OperationClass.LOCAL_GUARDING,
    OperationClass.READ,
    OperationClass.EXPORT,
    OperationClass.DELETE,
    OperationClass.SECURITY_ACTION,
}


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    reservation_id: str
    tenant_id: uuid.UUID
    category: UsageCategory
    units: Decimal
    operation: OperationClass
    quota_applies: bool


class QuotaManager:
    """Atomic preflight reservations prevent expensive work from overshooting quota."""

    durable = False

    def __init__(
        self,
        ledger: UsageLedger,
        *,
        hosted_work_allowed: Callable[[uuid.UUID], bool] = lambda _tenant_id: True,
    ) -> None:
        self.ledger = ledger
        self.hosted_work_allowed = hosted_work_allowed
        self._limits: dict[tuple[uuid.UUID, UsageCategory], Decimal] = {}
        self._reservations: dict[tuple[uuid.UUID, str], QuotaReservation] = {}
        self._lock = threading.RLock()

    def set_limit(
        self,
        tenant_id: uuid.UUID,
        category: UsageCategory | str,
        units: Decimal | int | str,
    ) -> None:
        limit = Decimal(units)
        if not limit.is_finite() or limit < 0:
            raise ValueError("quota limit must be finite and non-negative")
        with self._lock:
            self._limits[(tenant_id, UsageCategory(category))] = limit

    def reserve(
        self,
        reservation_id: str,
        *,
        tenant_id: uuid.UUID,
        category: UsageCategory | str,
        units: Decimal | int | str,
        operation: OperationClass | str = OperationClass.HOSTED_EXPENSIVE,
    ) -> QuotaReservation:
        requested = Decimal(units)
        operation = OperationClass(operation)
        category = UsageCategory(category)
        if (
            not reservation_id
            or len(reservation_id) > 256
            or not requested.is_finite()
            or requested <= 0
        ):
            raise ValueError("quota reservation is invalid")
        candidate = QuotaReservation(
            reservation_id,
            tenant_id,
            category,
            requested,
            operation,
            operation not in _ALWAYS_AVAILABLE,
        )
        key = (tenant_id, reservation_id)
        with self._lock:
            existing = self._reservations.get(key)
            if existing is not None:
                if existing != candidate:
                    raise QuotaConflict(
                        "reservation ID conflicts with existing semantics"
                    )
                return existing
            if candidate.quota_applies:
                if not self.hosted_work_allowed(tenant_id):
                    raise QuotaExceeded(
                        "billing grace expired; local guarding and safety operations remain available"
                    )
                limit = self._limits.get((tenant_id, category))
                reserved = sum(
                    (
                        reservation.units
                        for reservation in self._reservations.values()
                        if reservation.tenant_id == tenant_id
                        and reservation.category == category
                        and reservation.quota_applies
                    ),
                    Decimal("0"),
                )
                if (
                    limit is not None
                    and self.ledger.total(tenant_id, category)
                    + reserved
                    + requested
                    > limit
                ):
                    raise QuotaExceeded(
                        "hosted quota exhausted; local guarding remains available"
                    )
            self._reservations[key] = candidate
        return candidate

    def commit(
        self,
        tenant_id: uuid.UUID,
        reservation_id: str,
        *,
        usage_id: str,
        catalog_version: str,
        source: str,
        provider_cost_usd: Decimal | int | str | None = None,
    ) -> UsageEntry | None:
        key = (tenant_id, reservation_id)
        with self._lock:
            try:
                reservation = self._reservations[key]
            except KeyError as exc:
                raise QuotaConflict("quota reservation is unavailable") from exc
            if not reservation.quota_applies:
                self._reservations.pop(key)
                return None
            entry = self.ledger.record(
                usage_id,
                tenant_id=tenant_id,
                category=reservation.category,
                units=reservation.units,
                catalog_version=catalog_version,
                source=source,
                provider_cost_usd=provider_cost_usd,
            )
            self._reservations.pop(key)
            return entry

    def release(self, tenant_id: uuid.UUID, reservation_id: str) -> bool:
        with self._lock:
            return self._reservations.pop((tenant_id, reservation_id), None) is not None

    def active_units(
        self,
        tenant_id: uuid.UUID,
        category: UsageCategory | str,
    ) -> Decimal:
        selected = UsageCategory(category)
        with self._lock:
            return sum(
                (
                    reservation.units
                    for reservation in self._reservations.values()
                    if reservation.tenant_id == tenant_id
                    and reservation.category == selected
                    and reservation.quota_applies
                ),
                Decimal("0"),
            )
