from __future__ import annotations

import hashlib
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


class StreamTicketRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedStreamTicket:
    ticket: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StreamGrant:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    origin: str
    after_session_seq: int


@dataclass(slots=True)
class _StoredTicket:
    grant: StreamGrant
    expires_at: datetime
    consumed_at: datetime | None = None


class StreamTicketService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        self._clock = clock
        self._ttl = ttl
        self._tickets: dict[bytes, _StoredTicket] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        origin: str,
        after_session_seq: int,
    ) -> IssuedStreamTicket:
        if not origin or after_session_seq < 0:
            raise StreamTicketRejected("stream ticket binding is invalid")
        ticket = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        grant = StreamGrant(
            tenant_id, user_id, session_id, origin, after_session_seq
        )
        with self._lock:
            self._tickets[_digest(ticket)] = _StoredTicket(grant, expires_at)
        return IssuedStreamTicket(ticket, expires_at)

    def consume(self, ticket: str, *, origin: str) -> StreamGrant:
        now = self._clock()
        with self._lock:
            stored = self._tickets.get(_digest(ticket))
            if stored is None:
                raise StreamTicketRejected("stream ticket is unknown")
            if stored.consumed_at is not None:
                raise StreamTicketRejected("stream ticket was already consumed")
            if now > stored.expires_at:
                raise StreamTicketRejected("stream ticket expired")
            if not secrets.compare_digest(stored.grant.origin, origin):
                raise StreamTicketRejected("stream ticket origin does not match")
            stored.consumed_at = now
            return stored.grant

    def debug_stored_values(self) -> tuple[str, ...]:
        return tuple(value.hex() for value in self._tickets)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()
