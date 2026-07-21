from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StreamEvent:
    session_seq: int
    payload: dict[str, Any]


class SessionSubscriptionService:
    """Durable-read abstraction; notifications only wake readers and carry no truth."""

    def __init__(self) -> None:
        self._sessions: set[tuple[uuid.UUID, uuid.UUID]] = set()
        self._events: dict[tuple[uuid.UUID, uuid.UUID], dict[int, StreamEvent]] = {}
        self._lock = threading.Lock()

    def register_session(self, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> None:
        with self._lock:
            key = (tenant_id, session_id)
            self._sessions.add(key)
            self._events.setdefault(key, {})

    def delete_session(self, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> None:
        with self._lock:
            self._sessions.discard((tenant_id, session_id))

    def publish(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        session_seq: int,
        payload: dict[str, Any],
    ) -> None:
        if session_seq < 1:
            raise ValueError("session sequence must be positive")
        key = (tenant_id, session_id)
        with self._lock:
            if key not in self._sessions:
                raise ValueError("session is not registered")
            event = StreamEvent(session_seq, payload)
            existing = self._events[key].get(session_seq)
            if existing is not None and existing != event:
                raise ValueError("session sequence conflicts with a durable event")
            self._events[key][session_seq] = event

    def exists(self, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        with self._lock:
            return (tenant_id, session_id) in self._sessions

    def read_after(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID, after_session_seq: int
    ) -> list[StreamEvent] | None:
        key = (tenant_id, session_id)
        with self._lock:
            if key not in self._sessions:
                return None
            return [
                event
                for sequence, event in sorted(self._events[key].items())
                if sequence > after_session_seq
            ]

    async def wait_for_change(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        after_session_seq: int,
        timeout: float,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            page = self.read_after(
                tenant_id=tenant_id,
                session_id=session_id,
                after_session_seq=after_session_seq,
            )
            if page is None or page:
                return
            await asyncio.sleep(0.025)
