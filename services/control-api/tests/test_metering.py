from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from loopguard_api.metering import (
    UsageCategory,
    UsageConflict,
    UsageLedger,
)
from loopguard_api.quotas import (
    OperationClass,
    QuotaExceeded,
    QuotaManager,
)


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def test_usage_event_is_idempotent_and_conflicts_fail() -> None:
    tenant_id = uuid.uuid4()
    meter = UsageLedger(clock=lambda: NOW)

    first = meter.record(
        "usage_1",
        tenant_id=tenant_id,
        category=UsageCategory.RETAINED_EVENTS,
        units=10,
        catalog_version="2026-07",
        source="relay",
        provider_cost_usd="0.001",
    )
    replay = meter.record(
        "usage_1",
        tenant_id=tenant_id,
        category=UsageCategory.RETAINED_EVENTS,
        units=10,
        catalog_version="2026-07",
        source="relay",
        provider_cost_usd="0.001",
    )

    assert replay is first
    assert meter.total(tenant_id) == Decimal("10")
    assert first.provider_cost_usd == Decimal("0.001")
    with pytest.raises(UsageConflict, match="conflicts"):
        meter.record(
            "usage_1",
            tenant_id=tenant_id,
            category=UsageCategory.RETAINED_EVENTS,
            units=11,
            catalog_version="2026-07",
            source="relay",
        )


def test_quota_reservations_are_atomic_under_race() -> None:
    tenant_id = uuid.uuid4()
    manager = QuotaManager(UsageLedger(clock=lambda: NOW))
    manager.set_limit(
        tenant_id,
        UsageCategory.REPAIR_WORKER_SECONDS,
        10,
    )
    barrier = threading.Barrier(3)
    accepted: list[str] = []
    rejected: list[str] = []

    def reserve(reservation_id: str) -> None:
        barrier.wait()
        try:
            manager.reserve(
                reservation_id,
                tenant_id=tenant_id,
                category=UsageCategory.REPAIR_WORKER_SECONDS,
                units=7,
            )
            accepted.append(reservation_id)
        except QuotaExceeded:
            rejected.append(reservation_id)

    workers = [
        threading.Thread(target=reserve, args=(f"reservation-{index}",))
        for index in range(2)
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()

    assert len(accepted) == len(rejected) == 1
    assert manager.active_units(
        tenant_id,
        UsageCategory.REPAIR_WORKER_SECONDS,
    ) == Decimal("7")


def test_commit_records_once_and_release_returns_capacity() -> None:
    tenant_id = uuid.uuid4()
    ledger = UsageLedger(clock=lambda: NOW)
    manager = QuotaManager(ledger)
    manager.set_limit(tenant_id, UsageCategory.BROWSER_MINUTES, 5)
    manager.reserve(
        "browser-1",
        tenant_id=tenant_id,
        category=UsageCategory.BROWSER_MINUTES,
        units=5,
    )
    entry = manager.commit(
        tenant_id,
        "browser-1",
        usage_id="usage-browser-1",
        catalog_version="2026-07",
        source="browser-worker",
        provider_cost_usd="0.25",
    )

    assert entry is not None
    assert ledger.total(tenant_id, UsageCategory.BROWSER_MINUTES) == 5
    with pytest.raises(QuotaExceeded):
        manager.reserve(
            "browser-2",
            tenant_id=tenant_id,
            category=UsageCategory.BROWSER_MINUTES,
            units=1,
        )

    other = uuid.uuid4()
    manager.set_limit(other, UsageCategory.BROWSER_MINUTES, 5)
    manager.reserve(
        "browser-failed",
        tenant_id=other,
        category=UsageCategory.BROWSER_MINUTES,
        units=5,
    )
    assert manager.release(other, "browser-failed") is True
    assert manager.active_units(other, UsageCategory.BROWSER_MINUTES) == 0


@pytest.mark.parametrize(
    "operation",
    [
        OperationClass.LOCAL_GUARDING,
        OperationClass.READ,
        OperationClass.EXPORT,
        OperationClass.DELETE,
        OperationClass.SECURITY_ACTION,
    ],
)
def test_local_and_safety_operations_never_consume_hosted_quota(
    operation: OperationClass,
) -> None:
    tenant_id = uuid.uuid4()
    ledger = UsageLedger(clock=lambda: NOW)
    manager = QuotaManager(ledger)
    manager.set_limit(tenant_id, UsageCategory.MANAGED_COMPUTE_SECONDS, 0)

    reservation = manager.reserve(
        f"safe-{operation}",
        tenant_id=tenant_id,
        category=UsageCategory.MANAGED_COMPUTE_SECONDS,
        units=100,
        operation=operation,
    )
    entry = manager.commit(
        tenant_id,
        reservation.reservation_id,
        usage_id=f"unused-{operation}",
        catalog_version="2026-07",
        source="local",
    )

    assert reservation.quota_applies is False
    assert entry is None
    assert ledger.total(tenant_id) == 0


def test_expired_billing_grace_blocks_only_hosted_reservations() -> None:
    tenant_id = uuid.uuid4()
    manager = QuotaManager(
        UsageLedger(clock=lambda: NOW),
        hosted_work_allowed=lambda candidate: candidate != tenant_id,
    )

    with pytest.raises(QuotaExceeded, match="billing grace"):
        manager.reserve(
            "hosted",
            tenant_id=tenant_id,
            category=UsageCategory.REPAIR_WORKER_SECONDS,
            units=1,
        )
    safe = manager.reserve(
        "delete",
        tenant_id=tenant_id,
        category=UsageCategory.REPAIR_WORKER_SECONDS,
        units=1,
        operation=OperationClass.DELETE,
    )
    assert safe.quota_applies is False
