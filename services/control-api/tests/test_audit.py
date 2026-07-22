from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from loopguard_api.audit import (
    AuditStore,
    ImmutableRecordError,
    RETENTION_MATRIX,
)
from loopguard_api.db import (
    TenantContext,
    create_database_engine,
    tenant_session_factory,
    tenant_transaction,
)
from loopguard_api.models import AuditEntry, Base, Tenant


def test_audit_entry_cannot_be_updated_or_deleted():
    store = AuditStore()
    entry = store.append(
        tenant_id=uuid.uuid4(),
        actor_id="user-1",
        action="action.execute",
        target_kind="action",
        target_id="a1",
        request_id="req_1",
        before_state_hash="sha256:before",
        after_state_hash="sha256:after",
        result="executed",
        actor_metadata={"device_id": "device-1"},
    )

    with pytest.raises(ImmutableRecordError):
        store.update(entry.id, {"result": "changed"})
    with pytest.raises(ImmutableRecordError):
        store.delete(entry.id)


def test_sqlalchemy_audit_row_is_append_only(tmp_path: Path):
    async def scenario() -> None:
        engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
        tenant_id = uuid.uuid4()
        context = TenantContext(tenant_id)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                Tenant.__table__.insert(), {"id": tenant_id, "name": "tenant"}
            )
        factory = tenant_session_factory(engine)
        try:
            async with tenant_transaction(factory, context) as database:
                entry = AuditEntry(
                    tenant_id=tenant_id,
                    actor_id="user-1",
                    action="device.revoke",
                    target_kind="device",
                    target_id="device-1",
                    request_id="req_1",
                    result="revoked",
                    actor_metadata={},
                )
                database.add(entry)
                await database.flush()
                entry_id = entry.id
            with pytest.raises(ImmutableRecordError):
                async with tenant_transaction(factory, context) as database:
                    entry = await database.get(AuditEntry, entry_id)
                    entry.result = "changed"
                    await database.flush()
            with pytest.raises(ImmutableRecordError):
                async with tenant_transaction(factory, context) as database:
                    entry = await database.get(AuditEntry, entry_id)
                    await database.delete(entry)
                    await database.flush()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_retention_matrix_covers_every_required_data_class():
    assert set(RETENTION_MATRIX) == {
        "events",
        "source_and_log_artifacts",
        "verification_proofs",
        "repair_artifacts",
        "user_and_device_metadata",
        "billing_records",
        "audit",
    }
    assert RETENTION_MATRIX["audit"].mutation == "append_tombstone_only"
    assert RETENTION_MATRIX["user_and_device_metadata"].tenant_deletion == "deidentify"
    assert all(policy.legal_hold_overrides for policy in RETENTION_MATRIX.values())


def test_tenant_deletion_deidentifies_audit_without_rewriting_history():
    store = AuditStore()
    tenant_id = uuid.uuid4()
    store.append(
        tenant_id=tenant_id,
        actor_id="user-personal-id",
        action="tenant.delete",
        target_kind="tenant",
        target_id=str(tenant_id),
        request_id="req_delete",
        before_state_hash=None,
        after_state_hash=None,
        result="accepted",
        actor_metadata={"ip": "203.0.113.4", "device_id": "device-personal-id"},
    )

    tombstone = store.deidentify_tenant(
        tenant_id=tenant_id, authorized_by="retention-admin", legal_hold=False
    )

    entries = store.list(tenant_id)
    assert entries[0].actor_id.startswith("deleted:")
    assert entries[0].actor_metadata == {"identity_state": "deidentified"}
    assert tombstone.action == "tenant.deidentified"
    assert len(entries) == 2
