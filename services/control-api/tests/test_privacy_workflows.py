from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from loopguard_api.privacy import (
    DELETION_STAGES,
    InMemoryPrivacyBackend,
    InMemoryPrivacyCheckpoints,
    PrivacyService,
)
from loopguard_api.retention import (
    DataCategory,
    RetainedRecord,
    RetentionCandidate,
    RetentionService,
)


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def seeded() -> tuple[uuid.UUID, InMemoryPrivacyBackend, PrivacyService]:
    tenant = uuid.uuid4()
    backend = InMemoryPrivacyBackend(
        rows={
            tenant: [
                {"kind": "session", "id": "session-1", "private_key": "must-not-export"},
                {
                    "kind": "event",
                    "id": "event-1",
                    "metadata": {"authorization": "must-not-export"},
                },
            ]
        },
        objects={
            tenant: [
                {
                    "object_key": f"tenants/{tenant}/artifacts/1",
                    "sha256": "a" * 64,
                    "presigned_url": "must-not-export",
                }
            ]
        },
        temporal_search={tenant: [{"workflow_id": "repair-1"}]},
        notifications={tenant: [{"token": "opaque-push-token"}]},
        derived_metrics={tenant: [{"counter": "events", "value": 2}]},
    )
    service = PrivacyService(
        backend,
        InMemoryPrivacyCheckpoints(),
        tombstone_key=b"t" * 32,
        clock=lambda: NOW,
    )
    return tenant, backend, service


def test_tenant_export_is_scoped_manifested_and_excludes_credentials() -> None:
    tenant, _backend, service = seeded()

    exported = service.export(tenant)

    assert exported.tenant_id == tenant
    assert len(exported.rows) == 2
    assert len(exported.objects) == 1
    assert len(exported.manifest_sha256) == 64
    assert "private_key" not in str(exported)
    assert "presigned_url" not in str(exported)
    assert "authorization" not in str(exported)
    assert service.export(uuid.uuid4()).rows == ()


def test_tenant_deletion_removes_rows_objects_and_search_indexes() -> None:
    tenant, backend, service = seeded()

    result = service.delete_tenant(tenant)

    assert result.status == "completed"
    assert tenant not in backend.rows
    assert tenant not in backend.objects
    assert tenant not in backend.temporal_search
    assert tenant not in backend.notifications
    assert tenant not in backend.derived_metrics
    assert result.completed_stages == DELETION_STAGES


def test_audit_retains_tombstone_without_sensitive_payload() -> None:
    tenant, backend, service = seeded()

    result = service.delete_tenant(tenant)
    tombstone = result.tombstone

    assert tombstone is not None
    assert tombstone.tenant_id_hash
    assert str(tenant) not in str(tombstone)
    assert not hasattr(tombstone, "payload")
    assert backend.tombstones == [tombstone]


def test_deletion_is_resumable_and_does_not_repeat_completed_stages() -> None:
    tenant, backend, service = seeded()
    backend.fail_once_at = "temporal_search"

    first = service.delete_tenant(tenant)
    second = service.delete_tenant(tenant)
    third = service.delete_tenant(tenant)

    assert first.status == "interrupted"
    assert first.completed_stages == ("postgres", "objects")
    assert second.status == "completed"
    assert third == second
    assert backend.calls.count("postgres") == 1
    assert backend.calls.count("objects") == 1
    assert backend.calls.count("temporal_search") == 2
    assert len(backend.tombstones) == 1


class RecordingRetentionBackend:
    durable = True

    def __init__(self) -> None:
        self.deleted: list[RetentionCandidate] = []

    def delete_expired(self, candidate: RetentionCandidate) -> None:
        self.deleted.append(candidate)


def test_retention_honors_category_window_and_legal_hold() -> None:
    tenant = uuid.uuid4()
    backend = RecordingRetentionBackend()
    service = RetentionService(backend)
    records = [
        RetainedRecord(
            tenant,
            DataCategory.EVENT,
            "old-event",
            NOW - timedelta(days=31),
        ),
        RetainedRecord(
            tenant,
            DataCategory.EVENT,
            "held-event",
            NOW - timedelta(days=31),
            legal_hold=True,
        ),
        RetainedRecord(
            tenant,
            DataCategory.AUDIT,
            "recent-audit",
            NOW - timedelta(days=31),
        ),
        RetainedRecord(
            tenant,
            DataCategory.NOTIFICATION_TOKEN,
            "active-device-token",
            NOW - timedelta(days=500),
        ),
    ]

    plan = service.plan(records, now=NOW)

    assert [item.record_id for item in plan] == ["old-event"]
    assert service.apply(plan) == 1
    assert backend.deleted == list(plan)


def test_hosted_privacy_services_refuse_in_memory_adapters() -> None:
    try:
        PrivacyService(
            InMemoryPrivacyBackend(),
            InMemoryPrivacyCheckpoints(),
            tombstone_key=b"t" * 32,
            require_durable=True,
        )
    except ValueError as exc:
        assert "durable" in str(exc)
    else:
        raise AssertionError("hosted privacy workflow accepted in-memory storage")
