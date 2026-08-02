from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol


class DataCategory(StrEnum):
    EVENT = "event"
    ARTIFACT = "artifact"
    VERIFICATION = "verification"
    REPAIR = "repair"
    AUDIT = "audit"
    NOTIFICATION_TOKEN = "notification_token"
    USAGE = "usage"
    DELETION_TOMBSTONE = "deletion_tombstone"


DEFAULT_RETENTION_DAYS: dict[DataCategory, int | None] = {
    DataCategory.EVENT: 30,
    DataCategory.ARTIFACT: 30,
    DataCategory.VERIFICATION: 90,
    DataCategory.REPAIR: 90,
    DataCategory.AUDIT: 365,
    DataCategory.NOTIFICATION_TOKEN: None,
    DataCategory.USAGE: 365,
    DataCategory.DELETION_TOMBSTONE: 2_555,
}


@dataclass(frozen=True, slots=True)
class RetainedRecord:
    tenant_id: uuid.UUID
    category: DataCategory
    record_id: str
    created_at: datetime
    legal_hold: bool = False


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    tenant_id: uuid.UUID
    category: DataCategory
    record_id: str
    expired_at: datetime


class RetentionBackend(Protocol):
    durable: bool

    def delete_expired(self, candidate: RetentionCandidate) -> None: ...


class RetentionService:
    def __init__(
        self,
        backend: RetentionBackend,
        *,
        policy: dict[DataCategory, int | None] | None = None,
        require_durable: bool = False,
    ) -> None:
        if require_durable and not backend.durable:
            raise ValueError("hosted retention requires a durable backend")
        self.backend = backend
        self.policy = {**DEFAULT_RETENTION_DAYS, **(policy or {})}
        for category, days in self.policy.items():
            if category not in DataCategory or (
                days is not None and (not isinstance(days, int) or days < 1)
            ):
                raise ValueError("retention policy is invalid")

    def plan(
        self,
        records: list[RetainedRecord],
        *,
        now: datetime | None = None,
    ) -> tuple[RetentionCandidate, ...]:
        observed = now or datetime.now(UTC)
        candidates: list[RetentionCandidate] = []
        for record in records:
            days = self.policy[record.category]
            if days is None or record.legal_hold:
                continue
            expires = record.created_at + timedelta(days=days)
            if expires <= observed:
                candidates.append(
                    RetentionCandidate(
                        record.tenant_id,
                        record.category,
                        record.record_id,
                        expires,
                    )
                )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.expired_at,
                    str(item.tenant_id),
                    item.category,
                    item.record_id,
                ),
            )
        )

    def apply(self, candidates: tuple[RetentionCandidate, ...]) -> int:
        for candidate in candidates:
            self.backend.delete_expired(candidate)
        return len(candidates)
