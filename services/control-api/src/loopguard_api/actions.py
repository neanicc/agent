from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    EllipticCurvePublicKey,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.hashes import SHA256

from .action_signing import Ed25519ActionSigner
from .authorization import Principal


class DeviceProofRequired(ValueError):
    code = "device_proof_required"


class ActionConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActionChallenge:
    action_id: str
    tenant_id: uuid.UUID
    requested_by: uuid.UUID
    requested_by_device_id: uuid.UUID
    target_kind: str
    target_id: str
    host_id: str
    kind: str
    parameters: dict[str, Any]
    expected_state_version: int
    expected_state_hash: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    canonical_bytes: bytes
    state: str = "reviewed"


@dataclass(frozen=True, slots=True)
class ActionRecord:
    challenge: ActionChallenge
    requested_by_device_id: uuid.UUID
    device_key_id: str
    device_algorithm: str
    device_signature: str
    cloud_key_id: str
    cloud_algorithm: str
    cloud_signature: str
    state: str
    executed_at: datetime | None = None

    @property
    def action_id(self) -> str:
        return self.challenge.action_id


@dataclass(slots=True)
class _Device:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    key_id: str
    algorithm: str
    public_key: Ed25519PublicKey | EllipticCurvePublicKey
    revoked: bool = False


class ActionService:
    def __init__(
        self,
        *,
        signer: Ed25519ActionSigner,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.signer = signer
        self._clock = clock
        self._devices: dict[uuid.UUID, _Device] = {}
        self._challenges: dict[str, ActionChallenge] = {}
        self._records: dict[str, ActionRecord] = {}
        self._lock = threading.Lock()

    def register_device(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        key_id: str,
        algorithm: str,
        public_key: Ed25519PublicKey | EllipticCurvePublicKey,
    ) -> None:
        if algorithm not in {"Ed25519", "P-256"}:
            raise ValueError("unsupported device algorithm")
        if algorithm == "Ed25519" and not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("device public key does not match algorithm")
        if algorithm == "P-256" and not isinstance(public_key, EllipticCurvePublicKey):
            raise ValueError("device public key does not match algorithm")
        self._devices[device_id] = _Device(tenant_id, user_id, key_id, algorithm, public_key)

    def revoke_device(self, device_id: uuid.UUID) -> None:
        device = self._devices.get(device_id)
        if device is not None:
            device.revoked = True
            for action_id, record in tuple(self._records.items()):
                if record.requested_by_device_id == device_id and record.state not in {
                    "executed",
                    "rejected",
                    "expired",
                }:
                    self._records[action_id] = replace(record, state="revoked")

    def create_challenge(
        self,
        *,
        principal: Principal,
        requested_by_device_id: uuid.UUID,
        target_kind: str,
        target_id: str,
        host_id: str,
        kind: str,
        parameters: dict[str, Any],
        expected_state_version: int,
        expected_state_hash: str,
        expires_in: int,
    ) -> ActionChallenge:
        if expires_in < 1 or expires_in > 300:
            raise ValueError("action expiry must be between 1 and 300 seconds")
        now = self._clock()
        action_id = f"act_{uuid.uuid4()}"
        values = {
            "schema_version": 1,
            "canonicalization_version": 1,
            "action_id": action_id,
            "target": {"kind": target_kind, "target_id": target_id},
            "host_id": host_id,
            "kind": kind,
            "parameters_hash": "sha256:"
            + hashlib.sha256(
                json.dumps(
                    parameters,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            "expected_state_version": expected_state_version,
            "expected_state_hash": expected_state_hash,
            "tenant_id": str(principal.tenant_id),
            "requested_by": str(principal.user_id),
            "requested_by_device_id": str(requested_by_device_id),
            "issued_at": _timestamp(now),
            "expires_at": _timestamp(now + timedelta(seconds=expires_in)),
            "nonce": secrets.token_urlsafe(32),
        }
        canonical = json.dumps(
            values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        challenge = ActionChallenge(
            action_id=action_id,
            tenant_id=principal.tenant_id,
            requested_by=principal.user_id,
            requested_by_device_id=requested_by_device_id,
            target_kind=target_kind,
            target_id=target_id,
            host_id=host_id,
            kind=kind,
            parameters=parameters,
            expected_state_version=expected_state_version,
            expected_state_hash=expected_state_hash,
            nonce=values["nonce"],
            issued_at=now,
            expires_at=now + timedelta(seconds=expires_in),
            canonical_bytes=canonical,
        )
        self._challenges[action_id] = challenge
        return challenge

    def accept_signed(
        self,
        action_id: str,
        *,
        device_id: uuid.UUID,
        device_key_id: str,
        device_algorithm: str,
        device_signature: str,
    ) -> ActionRecord:
        with self._lock:
            existing = self._records.get(action_id)
            if existing is not None:
                return existing
            challenge = self._challenges.get(action_id)
            device = self._devices.get(device_id)
            if (
                challenge is None
                or device is None
                or device.revoked
                or device.tenant_id != challenge.tenant_id
                or device.user_id != challenge.requested_by
                or device_id != challenge.requested_by_device_id
                or device.key_id != device_key_id
                or device.algorithm != device_algorithm
            ):
                raise DeviceProofRequired("current registered device proof is required")
            if self._clock() > challenge.expires_at:
                raise ActionConflict("action challenge expired")
            try:
                signature = base64.b64decode(device_signature, altchars=b"-_", validate=True)
                if device.algorithm == "P-256":
                    assert isinstance(device.public_key, EllipticCurvePublicKey)
                    device.public_key.verify(signature, challenge.canonical_bytes, ECDSA(SHA256()))
                else:
                    assert isinstance(device.public_key, Ed25519PublicKey)
                    device.public_key.verify(signature, challenge.canonical_bytes)
            except (ValueError, InvalidSignature) as exc:
                raise DeviceProofRequired("current registered device proof is required") from exc
            record = ActionRecord(
                challenge=challenge,
                requested_by_device_id=device_id,
                device_key_id=device_key_id,
                device_algorithm=device_algorithm,
                device_signature=device_signature,
                cloud_key_id=self.signer.key_id,
                cloud_algorithm=self.signer.algorithm,
                cloud_signature=self.signer.sign(challenge.canonical_bytes),
                state="queued",
            )
            self._records[action_id] = record
            return record

    def resolve_from_host(self, action_id: str, *, status: str) -> ActionRecord:
        if status not in {"executed", "rejected"}:
            raise ValueError("host resolution must be terminal")
        with self._lock:
            record = self._records.get(action_id)
            if record is None:
                raise ActionConflict("action is not queued")
            if record.state in {"executed", "rejected"}:
                return record
            if record.state not in {"queued", "delivered", "host_executing", "reconciling"}:
                raise ActionConflict("action cannot be resolved from its current state")
            resolved = replace(
                record,
                state=status,
                executed_at=self._clock() if status == "executed" else None,
            )
            self._records[action_id] = resolved
            return resolved

    def expire(self, action_id: str) -> ActionRecord | ActionChallenge:
        record = self._records.get(action_id)
        if record is not None:
            if record.state in {"executed", "rejected", "expired", "revoked"}:
                return record
            expired = replace(record, state="expired")
            self._records[action_id] = expired
            return expired
        challenge = self._challenges[action_id]
        if self._clock() <= challenge.expires_at:
            return challenge
        expired_challenge = replace(challenge, state="expired")
        self._challenges[action_id] = expired_challenge
        return expired_challenge

    def read(self, action_id: str) -> ActionRecord | ActionChallenge | None:
        value = self._records.get(action_id) or self._challenges.get(action_id)
        if value is None:
            return None
        challenge = value.challenge if isinstance(value, ActionRecord) else value
        if (
            value.state not in {"executed", "rejected", "expired", "revoked"}
            and self._clock() > challenge.expires_at
        ):
            return self.expire(action_id)
        return value

    def list(self, tenant_id: uuid.UUID) -> list[ActionRecord | ActionChallenge]:
        values: list[ActionRecord | ActionChallenge] = []
        for challenge in self._challenges.values():
            if challenge.tenant_id != tenant_id:
                continue
            value = self.read(challenge.action_id)
            if value is not None:
                values.append(value)
        values.sort(
            key=lambda item: (
                item.challenge.issued_at if isinstance(item, ActionRecord) else item.issued_at
            ),
            reverse=True,
        )
        return values


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
