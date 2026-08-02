from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UsageConflict(ValueError):
    pass


class UsageCategory(StrEnum):
    HOSTED_STORAGE_BYTE_HOURS = "hosted_storage_byte_hours"
    RETAINED_EVENTS = "retained_events"
    MANAGED_COMPUTE_SECONDS = "managed_compute_seconds"
    JUDGE_USD = "judge_usd"
    CRITIC_USD = "critic_usd"
    BROWSER_MINUTES = "browser_minutes"
    REPAIR_WORKER_SECONDS = "repair_worker_seconds"


class UsageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    usage_id: str = Field(min_length=1, max_length=256)
    tenant_id: uuid.UUID
    category: UsageCategory
    units: Decimal = Field(gt=0, max_digits=28, decimal_places=8)
    provider_cost_usd: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=28,
        decimal_places=8,
    )
    catalog_version: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    recorded_at: datetime

    @field_validator("units", "provider_cost_usd")
    @classmethod
    def finite_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("metering decimals must be finite")
        return value


class UsageLedger:
    """Append-only, exactly-once usage ledger; hosted storage injects a durable adapter."""

    durable = False

    def __init__(
        self,
        *,
        clock=lambda: datetime.now(UTC),
        deletion_key: bytes = b"loopguard-test-meter-deletion-key",
    ) -> None:
        if len(deletion_key) < 16:
            raise ValueError("meter deletion key must contain at least 16 bytes")
        self.clock = clock
        self.deletion_key = bytes(deletion_key)
        self._entries: dict[tuple[uuid.UUID, str], UsageEntry] = {}
        self._lock = threading.RLock()

    def record(
        self,
        usage_id: str,
        *,
        tenant_id: uuid.UUID,
        category: UsageCategory | str,
        units: Decimal | int | str,
        catalog_version: str,
        source: str,
        provider_cost_usd: Decimal | int | str | None = None,
        observed_at: datetime | None = None,
    ) -> UsageEntry:
        entry = UsageEntry(
            usage_id=usage_id,
            tenant_id=tenant_id,
            category=UsageCategory(category),
            units=Decimal(units),
            provider_cost_usd=(
                None
                if provider_cost_usd is None
                else Decimal(provider_cost_usd)
            ),
            catalog_version=catalog_version,
            source=source,
            observed_at=observed_at or self.clock(),
            recorded_at=self.clock(),
        )
        key = (tenant_id, usage_id)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                if _semantic(existing) != _semantic(entry):
                    raise UsageConflict(
                        "duplicate usage ID conflicts with immutable ledger semantics"
                    )
                return existing
            self._entries[key] = entry
        return entry

    def entries(
        self,
        tenant_id: uuid.UUID,
        *,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> tuple[UsageEntry, ...]:
        with self._lock:
            values = (
                entry
                for entry in self._entries.values()
                if entry.tenant_id == tenant_id
                and (started_at is None or entry.observed_at >= started_at)
                and (ended_at is None or entry.observed_at < ended_at)
            )
            return tuple(
                sorted(values, key=lambda item: (item.observed_at, item.usage_id))
            )

    def total(
        self,
        tenant_id: uuid.UUID,
        category: UsageCategory | str | None = None,
    ) -> Decimal:
        selected = None if category is None else UsageCategory(category)
        return sum(
            (
                entry.units
                for entry in self.entries(tenant_id)
                if selected is None or entry.category == selected
            ),
            Decimal("0"),
        )

    def anonymize_tenant(self, tenant_id: uuid.UUID) -> int:
        """Retain mandatory financial evidence without retaining the deleted tenant ID."""

        digest = hashlib.sha256(self.deletion_key + tenant_id.bytes).digest()
        tombstone_id = uuid.UUID(bytes=digest[:16])
        changed = 0
        with self._lock:
            replacements: list[tuple[tuple[uuid.UUID, str], UsageEntry]] = []
            for key, entry in tuple(self._entries.items()):
                if entry.tenant_id == tenant_id:
                    replacements.append(
                        (
                            (tombstone_id, entry.usage_id),
                            entry.model_copy(update={"tenant_id": tombstone_id}),
                        )
                    )
                    self._entries.pop(key)
                    changed += 1
            self._entries.update(replacements)
        return changed


def categories() -> Iterable[UsageCategory]:
    return tuple(UsageCategory)


def _semantic(entry: UsageEntry) -> tuple[object, ...]:
    return (
        entry.tenant_id,
        entry.category,
        entry.units,
        entry.provider_cost_usd,
        entry.catalog_version,
        entry.source,
        entry.observed_at,
    )
