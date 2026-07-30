from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA, SECP256R1, EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.hashes import SHA256


class DevicePairingRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DevicePairingChallenge:
    pairing_id: str
    challenge: bytes
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    algorithm: str
    public_key: str
    key_id: str
    created_at: datetime
    revoked_at: datetime | None = None
    push_environment: str | None = None
    push_token: str | None = None


@dataclass(slots=True)
class _Pending:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    challenge: bytes
    expires_at: datetime
    consumed_at: datetime | None = None


class DevicePairingService:
    def __init__(
        self, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    ) -> None:
        self._clock = clock
        self._pending: dict[bytes, _Pending] = {}
        self._devices: dict[uuid.UUID, DeviceRecord] = {}
        self._lock = threading.Lock()

    def start(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> DevicePairingChallenge:
        pairing_id = secrets.token_urlsafe(24)
        challenge = secrets.token_bytes(32)
        expires_at = self._clock() + timedelta(minutes=5)
        self._pending[_digest(pairing_id)] = _Pending(tenant_id, user_id, challenge, expires_at)
        return DevicePairingChallenge(pairing_id, challenge, expires_at)

    def complete(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        pairing_id: str,
        public_key_alg: str,
        public_key: str,
        signature: str,
        name: str,
    ) -> DeviceRecord:
        now = self._clock()
        with self._lock:
            pending = self._pending.get(_digest(pairing_id))
            if pending is None or pending.consumed_at is not None:
                raise DevicePairingRejected("device pairing challenge was already consumed")
            if pending.tenant_id != tenant_id or pending.user_id != user_id:
                raise DevicePairingRejected("device pairing principal does not match")
            if now > pending.expires_at:
                raise DevicePairingRejected("device pairing challenge expired")
            try:
                key_bytes = base64.b64decode(public_key, altchars=b"-_", validate=True)
                signature_bytes = base64.b64decode(signature, altchars=b"-_", validate=True)
                if public_key_alg == "Ed25519":
                    Ed25519PublicKey.from_public_bytes(key_bytes).verify(
                        signature_bytes, pending.challenge
                    )
                elif public_key_alg == "P-256":
                    EllipticCurvePublicKey.from_encoded_point(SECP256R1(), key_bytes).verify(
                        signature_bytes, pending.challenge, ECDSA(SHA256())
                    )
                else:
                    raise DevicePairingRejected("device key algorithm is not allowed")
            except (ValueError, InvalidSignature) as exc:
                raise DevicePairingRejected("device proof of possession is invalid") from exc
            pending.consumed_at = now
            device = DeviceRecord(
                uuid.uuid4(),
                tenant_id,
                user_id,
                name.strip(),
                public_key_alg,
                public_key,
                f"dk_{hashlib.sha256(key_bytes).hexdigest()[:32]}",
                now,
            )
            self._devices[device.id] = device
            return device

    def list(self, tenant_id: uuid.UUID) -> list[DeviceRecord]:
        return [device for device in self._devices.values() if device.tenant_id == tenant_id]

    def revoke(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> DeviceRecord | None:
        device = self._devices.get(device_id)
        if device is None or device.tenant_id != tenant_id:
            return None
        revoked = replace(device, revoked_at=self._clock())
        self._devices[device_id] = revoked
        return revoked

    def register_push_destination(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        environment: str,
        token: str,
    ) -> DeviceRecord | None:
        device = self._devices.get(device_id)
        if device is None or device.tenant_id != tenant_id or device.revoked_at is not None:
            return None
        updated = replace(
            device,
            push_environment=environment,
            push_token=token,
        )
        self._devices[device_id] = updated
        return updated

    def push_destination(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID
    ) -> tuple[str, str] | None:
        device = self._devices.get(device_id)
        if (
            device is None
            or device.tenant_id != tenant_id
            or device.revoked_at is not None
            or device.push_environment is None
            or device.push_token is None
        ):
            return None
        return device.push_environment, device.push_token

    def seed_device(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str,
        algorithm: str,
        public_key: str,
    ) -> DeviceRecord:
        device = DeviceRecord(
            uuid.uuid4(),
            tenant_id,
            user_id,
            name,
            algorithm,
            public_key,
            f"seed_{uuid.uuid4().hex}",
            self._clock(),
        )
        self._devices[device.id] = device
        return device


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()
