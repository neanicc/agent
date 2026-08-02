from __future__ import annotations

import hashlib
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


class PairingError(ValueError):
    pass


class PairingConflict(PairingError):
    pass


class PairingExpired(PairingError):
    pass


@dataclass(frozen=True, slots=True)
class PairingCode:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PairedHost:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    public_key: str
    algorithm: str


@dataclass(slots=True)
class _PendingCode:
    tenant_id: uuid.UUID
    created_by: uuid.UUID
    expires_at: datetime
    consumed_at: datetime | None = None


class PairingService:
    """Atomic one-use pairing codes; the injected backend can be swapped for SQL."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        self._clock = clock
        self._ttl = ttl
        self._codes: dict[bytes, _PendingCode] = {}
        self._hosts: dict[uuid.UUID, PairedHost] = {}
        self._lock = threading.Lock()

    def create_code(self, *, tenant_id: uuid.UUID, created_by: uuid.UUID) -> PairingCode:
        code = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        with self._lock:
            self._codes[_digest(code)] = _PendingCode(tenant_id, created_by, expires_at)
        return PairingCode(code=code, expires_at=expires_at)

    def consume(
        self,
        code: str,
        *,
        name: str,
        public_key: str,
        algorithm: str,
    ) -> PairedHost:
        if algorithm != "Ed25519":
            raise PairingError("host keys must use Ed25519")
        if not name.strip() or not public_key.strip():
            raise PairingError("host identity must be non-empty")
        now = self._clock()
        with self._lock:
            pending = self._codes.get(_digest(code))
            if pending is None or pending.consumed_at is not None:
                raise PairingConflict("pairing code was already consumed or is unknown")
            if now > pending.expires_at:
                raise PairingExpired("pairing code expired")
            pending.consumed_at = now
            host = PairedHost(
                id=uuid.uuid4(),
                tenant_id=pending.tenant_id,
                name=name.strip(),
                public_key=public_key.strip(),
                algorithm=algorithm,
            )
            self._hosts[host.id] = host
            return host

    def debug_stored_values(self) -> tuple[str, ...]:
        return tuple(value.hex() for value in self._codes)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()
