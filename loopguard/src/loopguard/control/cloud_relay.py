from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from .store import EventStore, StoredEvent


@dataclass(frozen=True, slots=True)
class RelayAck:
    through_local_log_seq: int
    through_cloud_ingest_seq: int


class RelayDisconnected(ConnectionError):
    def __init__(self, *, acknowledged_through: int | None = None) -> None:
        self.acknowledged_through = acknowledged_through
        super().__init__("cloud relay disconnected")


class RelayProtocolError(RuntimeError):
    pass


class CloudTransport(Protocol):
    async def send_events(
        self,
        *,
        repository_handle: str,
        after_local_log_seq: int,
        events: list[dict[str, object]],
    ) -> RelayAck: ...


class CloudRelay:
    def __init__(
        self,
        store: EventStore,
        cloud: CloudTransport,
        *,
        repository_handle: str,
        batch_size: int = 100,
        max_reconnects: int = 5,
        retry_delay_seconds: float = 0.05,
    ) -> None:
        if not repository_handle or batch_size < 1 or max_reconnects < 0:
            raise ValueError("relay configuration is invalid")
        self.store = store
        self.cloud = cloud
        self.repository_handle = repository_handle
        self.batch_size = batch_size
        self.max_reconnects = max_reconnects
        self.retry_delay_seconds = retry_delay_seconds

    async def sync(self) -> int:
        reconnects = 0
        while True:
            checkpoint = self.store.relay_checkpoint(self.repository_handle)
            page = self.store.read_local_after(checkpoint, self.batch_size)
            if not page:
                return checkpoint
            try:
                ack = await self.cloud.send_events(
                    repository_handle=self.repository_handle,
                    after_local_log_seq=checkpoint,
                    events=[_serialize(item) for item in page],
                )
            except RelayDisconnected as exc:
                if exc.acknowledged_through is not None:
                    self._accept_ack(
                        RelayAck(exc.acknowledged_through, 0), checkpoint, page
                    )
                reconnects += 1
                if reconnects > self.max_reconnects:
                    raise
                await asyncio.sleep(self.retry_delay_seconds * reconnects)
                continue
            self._accept_ack(ack, checkpoint, page)
            reconnects = 0

    def _accept_ack(
        self, ack: RelayAck, previous_checkpoint: int, page: list[StoredEvent]
    ) -> None:
        maximum = page[-1].local_log_seq
        if not previous_checkpoint < ack.through_local_log_seq <= maximum:
            raise RelayProtocolError("relay acknowledgement is outside the sent batch")
        self.store.advance_relay_checkpoint(
            self.repository_handle, ack.through_local_log_seq
        )


def _serialize(stored: StoredEvent) -> dict[str, object]:
    event = stored.event.model_dump(mode="json")
    return {
        **event,
        "local_log_seq": stored.local_log_seq,
        "repo_seq": stored.repo_seq,
        "session_seq": stored.session_seq,
    }
