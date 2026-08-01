from __future__ import annotations

import uuid

from loopguard_api.relay import RelayBatch, RelayEvent, RelayService


class ResponseConnectionLost(ConnectionError):
    pass


def test_relay_retry_after_ack_connection_loss_is_idempotent() -> None:
    """Model a process/connection loss after the transaction commits but before its ACK arrives."""

    tenant_id = uuid.uuid4()
    host_id = uuid.uuid4()
    repository_id = uuid.uuid4()
    relay = RelayService(max_batch_events=10, max_batch_bytes=16_384)
    relay.register_repository(
        tenant_id=tenant_id,
        host_id=host_id,
        repository_id=repository_id,
        repository_handle="rh_chaos",
    )
    event = RelayEvent(
        event_id="evt_chaos_1",
        host_id=host_id,
        repository_id=repository_id,
        session_id=uuid.uuid4(),
        local_log_seq=1,
        repo_seq=1,
        kind="tool.result",
        payload={"redacted": True},
    )
    batch = RelayBatch(
        repository_handle="rh_chaos",
        after_local_log_seq=0,
        events=[event],
    )

    committed_ack = relay.ingest(
        tenant_id=tenant_id,
        host_id=host_id,
        batch=batch,
    )
    try:
        raise ResponseConnectionLost("socket closed before ACK delivery")
    except ResponseConnectionLost:
        recovered_ack = relay.ingest(
            tenant_id=tenant_id,
            host_id=host_id,
            batch=batch,
        )

    assert recovered_ack == committed_ack
    assert relay.persisted_event_ids == ["evt_chaos_1"]
    assert relay.outbox_event_ids == ["evt_chaos_1"]
