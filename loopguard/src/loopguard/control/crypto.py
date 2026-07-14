from __future__ import annotations

import hashlib
import hmac
import os
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_KEY_DERIVATION_DOMAIN = b"loopguard.control.data-key.v1\x00"
_KEY_ID_DOMAIN = b"loopguard.control.key-id.v1\x00"
_EVENT_ID_DOMAIN = b"loopguard.control.event-id.v1\x00"


class CryptoError(Exception):
    """Base class for local event encryption failures."""


class IntegrityError(CryptoError):
    """Ciphertext or authenticated metadata failed verification."""


class KeyStoreError(CryptoError):
    """The platform credential service could not safely provide a data key."""


class MissingKeyError(KeyStoreError):
    """The credential referenced by an existing event store is missing."""


class CorruptKeyError(KeyStoreError):
    """A stored credential is malformed or is not an AES-256 key."""


class KeyStore(Protocol):
    def put(self, key_id: str, data_key: bytes) -> None: ...

    def get(self, key_id: str) -> bytes: ...


class PlatformKeyStore:
    """Data-key storage backed by the operating system's keyring integration."""

    def __init__(
        self,
        *,
        backend: Any | None = None,
        service_name: str = "dev.loopguard.local-event-store",
    ) -> None:
        if backend is None:
            import keyring

            backend = keyring
        self._backend = backend
        self._service_name = service_name

    def put(self, key_id: str, data_key: bytes) -> None:
        _require_data_key(data_key)
        encoded = f"v1:{b64encode(data_key).decode('ascii')}"
        try:
            self._backend.set_password(self._service_name, key_id, encoded)
        except Exception as exc:
            raise KeyStoreError("platform credential service rejected the event-store key") from exc

    def get(self, key_id: str) -> bytes:
        try:
            encoded = self._backend.get_password(self._service_name, key_id)
        except Exception as exc:
            raise KeyStoreError("platform credential service could not read the event-store key") from exc
        if encoded is None:
            raise MissingKeyError(f"event-store key {key_id} is missing")
        if not encoded.startswith("v1:"):
            raise CorruptKeyError(f"event-store key {key_id} has an unsupported encoding")
        try:
            data_key = b64decode(encoded[3:], validate=True)
        except (Base64Error, ValueError) as exc:
            raise CorruptKeyError(f"event-store key {key_id} is not valid base64") from exc
        if len(data_key) != 32:
            raise CorruptKeyError(f"event-store key {key_id} is not 32 bytes")
        return data_key


class InMemoryKeyStore:
    """Explicit test key store that never accesses the user's real keychain."""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def put(self, key_id: str, data_key: bytes) -> None:
        _require_data_key(data_key)
        self._keys[key_id] = bytes(data_key)

    def get(self, key_id: str) -> bytes:
        if key_id not in self._keys:
            raise MissingKeyError(f"event-store key {key_id} is missing")
        data_key = self._keys[key_id]
        if len(data_key) != 32:
            raise CorruptKeyError(f"event-store key {key_id} is not 32 bytes")
        return bytes(data_key)

    def delete(self, key_id: str) -> None:
        self._keys.pop(key_id, None)

    def key_ids(self) -> list[str]:
        return sorted(self._keys)

    def put_raw_for_test(self, key_id: str, value: bytes) -> None:
        self._keys[key_id] = bytes(value)


def derive_data_key(material: bytes) -> bytes:
    """Derive a stable AES-256 key from injected test material."""
    if not material:
        raise ValueError("key material must not be empty")
    return hashlib.sha256(_KEY_DERIVATION_DOMAIN + material).digest()


def generate_data_key() -> bytes:
    return os.urandom(32)


def key_id_for(data_key: bytes) -> str:
    _require_data_key(data_key)
    return hashlib.sha256(_KEY_ID_DOMAIN + data_key).hexdigest()


def event_id_token(data_key: bytes, event_id: str) -> str:
    _require_data_key(data_key)
    return hmac.new(data_key, _EVENT_ID_DOMAIN + event_id.encode(), hashlib.sha256).hexdigest()


def encrypt(data_key: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    _require_data_key(data_key)
    nonce = os.urandom(12)
    return nonce, AESGCM(data_key).encrypt(nonce, plaintext, aad)


def decrypt(data_key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    _require_data_key(data_key)
    try:
        return AESGCM(data_key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise IntegrityError("encrypted event integrity verification failed") from exc


def _require_data_key(data_key: bytes) -> None:
    if len(data_key) != 32:
        raise ValueError("AES-256-GCM data keys must contain exactly 32 bytes")
