from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


class HookRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedHookCredential:
    key_id: str
    secret: str
    tenant_id: uuid.UUID
    host_id: uuid.UUID | None
    repository_handle: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedHook:
    tenant_id: uuid.UUID
    host_id: uuid.UUID | None
    repository_handle: str


@dataclass(slots=True)
class _StoredCredential:
    derived_key: bytes
    tenant_id: uuid.UUID
    host_id: uuid.UUID | None
    repository_handle: str
    expires_at: datetime
    revoked_at: datetime | None = None


class HookService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        maximum_body_bytes: int = 1_048_576,
        maximum_clock_skew: timedelta = timedelta(minutes=5),
        derivation_key: bytes | None = None,
    ) -> None:
        self._clock = clock
        self.maximum_body_bytes = maximum_body_bytes
        self.maximum_clock_skew = maximum_clock_skew
        self._derivation_key = derivation_key or secrets.token_bytes(32)
        self._credentials: dict[str, _StoredCredential] = {}
        self._nonces: dict[tuple[str, str], datetime] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        tenant_id: uuid.UUID,
        host_id: uuid.UUID | None,
        repository_handle: str,
        expires_at: datetime,
    ) -> IssuedHookCredential:
        if expires_at <= self._clock():
            raise HookRejected("credential expiry must be in the future")
        key_id = f"hk_{secrets.token_urlsafe(12)}"
        secret = secrets.token_urlsafe(32)
        self._credentials[key_id] = _StoredCredential(
            derived_key=self._derive(secret),
            tenant_id=tenant_id,
            host_id=host_id,
            repository_handle=repository_handle,
            expires_at=expires_at,
        )
        return IssuedHookCredential(
            key_id, secret, tenant_id, host_id, repository_handle, expires_at
        )

    def revoke(self, key_id: str) -> None:
        credential = self._credentials.get(key_id)
        if credential is None:
            raise HookRejected("credential not found")
        credential.revoked_at = self._clock()

    def sign_for_test(
        self,
        credential: IssuedHookCredential,
        *,
        method: str,
        path: str,
        timestamp: datetime,
        nonce: str,
        repository_handle: str,
        body: bytes,
    ) -> dict[str, str]:
        signature = hmac.new(
            self._derive(credential.secret),
            _canonical(method, path, timestamp, nonce, repository_handle, body),
            hashlib.sha256,
        ).hexdigest()
        return {
            "key_id": credential.key_id,
            "timestamp": timestamp.isoformat(),
            "nonce": nonce,
            "repository_handle": repository_handle,
            "signature": signature,
        }

    def verify(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        key_id: str,
        timestamp: str,
        nonce: str,
        repository_handle: str,
        signature: str,
    ) -> VerifiedHook:
        if len(body) > self.maximum_body_bytes:
            raise HookRejected("hook body is too large")
        try:
            observed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise HookRejected("hook timestamp is invalid") from exc
        if observed.tzinfo is None:
            raise HookRejected("hook timestamp must include a timezone")
        now = self._clock()
        credential = self._credentials.get(key_id)
        if credential is None:
            raise HookRejected("hook signature is invalid")
        if credential.revoked_at is not None:
            raise HookRejected("hook credential is revoked")
        if now > credential.expires_at:
            raise HookRejected("hook credential is expired")
        if abs(now - observed) > self.maximum_clock_skew:
            raise HookRejected("hook timestamp is outside the allowed clock skew")
        if credential.repository_handle != repository_handle:
            raise HookRejected("hook repository binding does not match")
        if not nonce or len(nonce) > 256:
            raise HookRejected("hook nonce is invalid")
        expected = hmac.new(
            credential.derived_key,
            _canonical(method, path, observed, nonce, repository_handle, body),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HookRejected("hook signature is invalid")
        with self._lock:
            self._nonces = {
                key: expiry for key, expiry in self._nonces.items() if expiry >= now
            }
            nonce_key = (key_id, nonce)
            if nonce_key in self._nonces:
                raise HookRejected("hook nonce replay detected")
            self._nonces[nonce_key] = min(
                credential.expires_at, now + self.maximum_clock_skew
            )
        return VerifiedHook(
            credential.tenant_id, credential.host_id, credential.repository_handle
        )

    def _derive(self, secret: str) -> bytes:
        return hmac.new(self._derivation_key, secret.encode(), hashlib.sha256).digest()


def _canonical(
    method: str,
    path: str,
    timestamp: datetime,
    nonce: str,
    repository_handle: str,
    body: bytes,
) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join(
        (
            method.upper(),
            path,
            timestamp.astimezone(timezone.utc).isoformat(),
            nonce,
            repository_handle,
            digest,
        )
    ).encode()
