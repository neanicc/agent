from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


_HOOK_KEY_DOMAIN = "loopguard.hook.hmac.v1:"


class HookRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedHookCredential:
    key_id: str
    secret: str
    tenant_id: uuid.UUID
    host_id: uuid.UUID | None
    repository_handle: str
    scopes: tuple[str, ...]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedHook:
    tenant_id: uuid.UUID
    host_id: uuid.UUID | None
    repository_handle: str
    scopes: frozenset[str]


@dataclass(slots=True)
class _StoredCredential:
    derived_key: bytes
    tenant_id: uuid.UUID
    host_id: uuid.UUID | None
    repository_handle: str
    scopes: frozenset[str]
    expires_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None


class HookService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        maximum_body_bytes: int = 1_048_576,
        maximum_clock_skew: timedelta = timedelta(minutes=5),
    ) -> None:
        self._clock = clock
        self.maximum_body_bytes = maximum_body_bytes
        self.maximum_clock_skew = maximum_clock_skew
        self._credentials: dict[str, _StoredCredential] = {}
        self._nonces: dict[tuple[str, str], datetime] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        tenant_id: uuid.UUID,
        host_id: uuid.UUID | None,
        repository_handle: str,
        scopes: tuple[str, ...] = ("events:write",),
        expires_at: datetime,
    ) -> IssuedHookCredential:
        if expires_at <= self._clock():
            raise HookRejected("credential expiry must be in the future")
        normalized_scopes = _scopes(scopes)
        key_id = f"hk_{secrets.token_urlsafe(12)}"
        secret = secrets.token_urlsafe(32)
        self._credentials[key_id] = _StoredCredential(
            derived_key=self._derive(secret),
            tenant_id=tenant_id,
            host_id=host_id,
            repository_handle=repository_handle,
            scopes=normalized_scopes,
            expires_at=expires_at,
        )
        return IssuedHookCredential(
            key_id,
            secret,
            tenant_id,
            host_id,
            repository_handle,
            tuple(sorted(normalized_scopes)),
            expires_at,
        )

    def revoke(self, key_id: str) -> None:
        credential = self._credentials.get(key_id)
        if credential is None:
            raise HookRejected("credential not found")
        credential.revoked_at = self._clock()

    def rotate(
        self,
        key_id: str,
        *,
        expires_at: datetime,
    ) -> IssuedHookCredential:
        credential = self._credentials.get(key_id)
        now = self._clock()
        if credential is None:
            raise HookRejected("credential not found")
        if credential.revoked_at is not None or credential.rotated_at is not None:
            raise HookRejected("credential is not active")
        if now > credential.expires_at:
            raise HookRejected("credential is expired")
        replacement = self.issue(
            tenant_id=credential.tenant_id,
            host_id=credential.host_id,
            repository_handle=credential.repository_handle,
            scopes=tuple(sorted(credential.scopes)),
            expires_at=expires_at,
        )
        credential.rotated_at = now
        credential.revoked_at = now
        return replacement

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
        return sign_hook_request(
            credential.secret,
            key_id=credential.key_id,
            method=method,
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            repository_handle=repository_handle,
            body=body,
        )

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
        required_scope: str | None = None,
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
        if required_scope is not None and required_scope not in credential.scopes:
            raise HookRejected("hook repository binding lacks required scope")
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
            credential.tenant_id,
            credential.host_id,
            credential.repository_handle,
            credential.scopes,
        )

    def _derive(self, secret: str) -> bytes:
        return hashlib.sha256((_HOOK_KEY_DOMAIN + secret).encode("utf-8")).digest()


def sign_hook_request(
    secret: str,
    *,
    key_id: str,
    method: str,
    path: str,
    timestamp: datetime,
    nonce: str,
    repository_handle: str,
    body: bytes,
) -> dict[str, str]:
    """Create the host-side signature without access to any server-only key."""

    derived = hashlib.sha256((_HOOK_KEY_DOMAIN + secret).encode("utf-8")).digest()
    signature = hmac.new(
        derived,
        _canonical(method, path, timestamp, nonce, repository_handle, body),
        hashlib.sha256,
    ).hexdigest()
    return {
        "key_id": key_id,
        "timestamp": timestamp.isoformat(),
        "nonce": nonce,
        "repository_handle": repository_handle,
        "signature": signature,
    }


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


def _scopes(values: tuple[str, ...]) -> frozenset[str]:
    allowed = {"events:write", "repair:intake"}
    normalized = frozenset(value.strip() for value in values)
    if not normalized or len(normalized) > 16 or not normalized <= allowed:
        raise HookRejected("credential scopes are invalid")
    return normalized
