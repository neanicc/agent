from __future__ import annotations

import base64
import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ConfigDict

from .decisions import ActionRequest


class SignedActionRequest(ActionRequest):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    host_id: str
    requested_by: str
    requested_by_device_id: str
    issued_at: datetime
    device_key_id: str
    device_algorithm: Literal["Ed25519"]
    device_signature: str
    cloud_key_id: str
    cloud_algorithm: Literal["Ed25519"]
    cloud_signature: str
    canonicalization_version: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class ActionResolution:
    status: str
    code: str


@dataclass(slots=True)
class _DeviceKey:
    key_id: str
    public_key: Ed25519PublicKey
    revoked: bool = False


@dataclass(slots=True)
class _CloudKey:
    public_key: Ed25519PublicKey
    state: str = "active"


class ActionVerifier:
    def __init__(
        self,
        *,
        clock=lambda: datetime.now(timezone.utc),
        tenant_id: str | None = None,
        host_id: str | None = None,
        allowed_kinds: frozenset[str] | None = None,
    ) -> None:
        self._clock = clock
        self.tenant_id = tenant_id
        self.host_id = host_id
        self.allowed_kinds = allowed_kinds
        self._devices: dict[str, _DeviceKey] = {}
        self._cloud_keys: dict[str, _CloudKey] = {}
        self._resolved: dict[str, ActionResolution] = {}
        self._nonces: set[str] = set()
        self._lock = threading.Lock()

    def register_device(
        self, device_id: str, key_id: str, public_key: Ed25519PublicKey
    ) -> None:
        self._devices[device_id] = _DeviceKey(key_id, public_key)

    def revoke_device(self, device_id: str) -> None:
        if device_id in self._devices:
            self._devices[device_id].revoked = True

    def register_cloud_key(
        self, key_id: str, public_key: Ed25519PublicKey, *, state: str = "active"
    ) -> None:
        if state not in {"active", "retiring", "revoked"}:
            raise ValueError("cloud key state is invalid")
        self._cloud_keys[key_id] = _CloudKey(public_key, state)

    def consume(
        self,
        action: SignedActionRequest,
        *,
        current_state_version: int,
        current_state_hash: str,
    ) -> ActionResolution:
        with self._lock:
            if action.action_id in self._resolved:
                return ActionResolution("rejected", "already_resolved")
            if self._clock() > action.expires_at:
                return ActionResolution("rejected", "expired")
            if self.tenant_id is not None and action.tenant_id != self.tenant_id:
                return ActionResolution("rejected", "tenant_mismatch")
            if self.host_id is not None and action.host_id != self.host_id:
                return ActionResolution("rejected", "host_mismatch")
            if self.allowed_kinds is not None and action.kind not in self.allowed_kinds:
                return ActionResolution("rejected", "policy_denied")
            if (
                action.expected_state_version != current_state_version
                or action.expected_state_hash != current_state_hash
            ):
                return ActionResolution("rejected", "stale_state")
            device = self._devices.get(action.requested_by_device_id)
            if device is None or device.key_id != action.device_key_id:
                return ActionResolution("rejected", "unknown_device_key")
            if device.revoked:
                return ActionResolution("rejected", "device_revoked")
            cloud = self._cloud_keys.get(action.cloud_key_id)
            if cloud is None:
                return ActionResolution("rejected", "unknown_cloud_key")
            if cloud.state == "revoked":
                return ActionResolution("rejected", "cloud_key_revoked")
            if action.nonce in self._nonces:
                return ActionResolution("rejected", "nonce_replayed")
            canonical = canonical_action_bytes(action)
            if not _verify(device.public_key, action.device_signature, canonical):
                return ActionResolution("rejected", "device_signature_invalid")
            if not _verify(cloud.public_key, action.cloud_signature, canonical):
                return ActionResolution("rejected", "cloud_signature_invalid")
            resolution = ActionResolution("executed", "executed")
            self._nonces.add(action.nonce)
            self._resolved[action.action_id] = resolution
            return resolution


def canonical_action_bytes(action: SignedActionRequest) -> bytes:
    payload = {
        "action_id": action.action_id,
        "canonicalization_version": action.canonicalization_version,
        "expires_at": _timestamp(action.expires_at),
        "expected_state_hash": action.expected_state_hash,
        "expected_state_version": action.expected_state_version,
        "host_id": action.host_id,
        "issued_at": _timestamp(action.issued_at),
        "kind": str(action.kind),
        "nonce": action.nonce,
        "parameters_hash": "sha256:"
        + hashlib.sha256(
            json.dumps(
                action.parameters,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
        "requested_by": action.requested_by,
        "requested_by_device_id": action.requested_by_device_id,
        "schema_version": action.schema_version,
        "target": action.target.model_dump(mode="json"),
        "tenant_id": action.tenant_id,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _verify(key: Ed25519PublicKey, signature: str, message: bytes) -> bool:
    try:
        decoded = base64.b64decode(signature, altchars=b"-_", validate=True)
        key.verify(decoded, message)
    except (ValueError, InvalidSignature):
        return False
    return True


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("action timestamps must be timezone aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
