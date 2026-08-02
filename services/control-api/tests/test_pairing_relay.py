from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from loopguard_api.pairing import PairingConflict, PairingExpired, PairingService
from loopguard_api.relay import RelayBatch, RelayEvent, RelayRejected, RelayService


NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def test_pairing_code_is_hashed_expiring_and_single_use():
    service = PairingService(clock=lambda: NOW)
    issued = service.create_code(tenant_id=uuid.uuid4(), created_by=uuid.uuid4())

    assert issued.code not in service.debug_stored_values()
    host = service.consume(
        issued.code,
        name="dev-machine",
        public_key="ed25519-public-key",
        algorithm="Ed25519",
    )
    assert host.public_key == "ed25519-public-key"
    with pytest.raises(PairingConflict):
        service.consume(
            issued.code,
            name="other",
            public_key="other-key",
            algorithm="Ed25519",
        )


def test_pairing_code_expires_after_five_minutes():
    current = NOW
    service = PairingService(clock=lambda: current)
    issued = service.create_code(tenant_id=uuid.uuid4(), created_by=uuid.uuid4())
    current += timedelta(minutes=5, microseconds=1)

    with pytest.raises(PairingExpired):
        service.consume(
            issued.code,
            name="late",
            public_key="key",
            algorithm="Ed25519",
        )


def test_relay_rejects_repository_substitution_and_is_idempotent():
    tenant_id = uuid.uuid4()
    host_id = uuid.uuid4()
    repository_id = uuid.uuid4()
    service = RelayService(max_batch_events=3, max_batch_bytes=4_096)
    service.register_repository(
        tenant_id=tenant_id,
        host_id=host_id,
        repository_id=repository_id,
        repository_handle="rh_1",
    )
    event = RelayEvent(
        event_id="evt_1",
        host_id=host_id,
        repository_id=repository_id,
        session_id=uuid.uuid4(),
        local_log_seq=1,
        repo_seq=1,
        kind="tool.result",
        payload={"ok": True},
    )
    batch = RelayBatch(repository_handle="rh_1", after_local_log_seq=0, events=[event])

    first = service.ingest(tenant_id=tenant_id, host_id=host_id, batch=batch)
    second = service.ingest(tenant_id=tenant_id, host_id=host_id, batch=batch)

    assert first == second
    assert first.through_local_log_seq == 1
    assert service.persisted_event_ids == ["evt_1"]
    assert service.outbox_event_ids == ["evt_1"]
    with pytest.raises(RelayRejected, match="repository binding"):
        service.ingest(
            tenant_id=tenant_id,
            host_id=uuid.uuid4(),
            batch=batch,
        )


def test_relay_rejects_reordered_or_oversized_batches():
    service = RelayService(max_batch_events=1, max_batch_bytes=4_096)
    tenant_id, host_id, repository_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    service.register_repository(
        tenant_id=tenant_id,
        host_id=host_id,
        repository_id=repository_id,
        repository_handle="rh_1",
    )
    event = RelayEvent(
        event_id="evt_2",
        host_id=host_id,
        repository_id=repository_id,
        session_id=uuid.uuid4(),
        local_log_seq=2,
        repo_seq=2,
        kind="tool.result",
        payload={},
    )
    with pytest.raises(RelayRejected, match="contiguous"):
        service.ingest(
            tenant_id=tenant_id,
            host_id=host_id,
            batch=RelayBatch(repository_handle="rh_1", after_local_log_seq=0, events=[event]),
        )
