from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RelayRejected(ValueError):
    pass


class RelayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=256)
    host_id: uuid.UUID
    repository_id: uuid.UUID
    session_id: uuid.UUID
    local_log_seq: int = Field(ge=1)
    repo_seq: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]


class RelayBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_handle: str = Field(min_length=1, max_length=256)
    after_local_log_seq: int = Field(ge=0)
    events: list[RelayEvent]


@dataclass(frozen=True, slots=True)
class RelayAck:
    through_local_log_seq: int
    through_cloud_ingest_seq: int


@dataclass(frozen=True, slots=True)
class _RepositoryBinding:
    tenant_id: uuid.UUID
    host_id: uuid.UUID
    repository_id: uuid.UUID


class RelayService:
    """Validates relay topology and models atomic event/outbox persistence."""

    def __init__(self, *, max_batch_events: int = 100, max_batch_bytes: int = 1_048_576) -> None:
        self.max_batch_events = max_batch_events
        self.max_batch_bytes = max_batch_bytes
        self._repositories: dict[str, _RepositoryBinding] = {}
        self._events: dict[str, tuple[RelayEvent, int]] = {}
        self._outbox: dict[str, int] = {}
        self._cloud_sequence = 0
        self._lock = threading.Lock()

    def register_repository(
        self,
        *,
        tenant_id: uuid.UUID,
        host_id: uuid.UUID,
        repository_id: uuid.UUID,
        repository_handle: str,
    ) -> None:
        if repository_handle in self._repositories:
            raise RelayRejected("repository handle already exists")
        self._repositories[repository_handle] = _RepositoryBinding(
            tenant_id, host_id, repository_id
        )

    def ingest(
        self, *, tenant_id: uuid.UUID, host_id: uuid.UUID, batch: RelayBatch
    ) -> RelayAck:
        if not batch.events or len(batch.events) > self.max_batch_events:
            raise RelayRejected("batch count exceeds the allowed range")
        encoded_size = len(batch.model_dump_json().encode())
        if encoded_size > self.max_batch_bytes:
            raise RelayRejected("batch bytes exceed the allowed range")
        binding = self._repositories.get(batch.repository_handle)
        if (
            binding is None
            or binding.tenant_id != tenant_id
            or binding.host_id != host_id
        ):
            raise RelayRejected("repository binding does not match the authenticated host")
        expected = batch.after_local_log_seq + 1
        for event in batch.events:
            if event.local_log_seq != expected:
                raise RelayRejected("relay sequence must be contiguous and ordered")
            expected += 1
            if event.host_id != host_id or event.repository_id != binding.repository_id:
                raise RelayRejected("event repository binding does not match the relay")

        with self._lock:
            highest_cloud = 0
            for event in batch.events:
                existing = self._events.get(event.event_id)
                if existing is not None:
                    if existing[0] != event:
                        raise RelayRejected("duplicate event conflicts with persisted semantics")
                    highest_cloud = max(highest_cloud, existing[1])
                    continue
                self._cloud_sequence += 1
                self._events[event.event_id] = (event, self._cloud_sequence)
                self._outbox[event.event_id] = self._cloud_sequence
                highest_cloud = self._cloud_sequence
            return RelayAck(batch.events[-1].local_log_seq, highest_cloud)

    @property
    def persisted_event_ids(self) -> list[str]:
        return list(self._events)

    @property
    def outbox_event_ids(self) -> list[str]:
        return list(self._outbox)
