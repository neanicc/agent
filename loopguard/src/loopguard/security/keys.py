from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from loopguard.control.crypto import KeyStore, PlatformKeyStore

from .permissions import (
    create_private_file,
    validate_private_directory,
    validate_private_file,
)


class SecretStore(Protocol):
    def put(self, key_id: str, secret: bytes) -> None: ...

    def get(self, key_id: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class StoredSecret:
    key_id: str
    storage: str
    warning: str | None = None


class PlatformSecretStore:
    """Private-key material stored through Keychain, DPAPI, or libsecret."""

    def __init__(self, backend: KeyStore | None = None) -> None:
        self.backend = backend or PlatformKeyStore(
            service_name="dev.loopguard.private-credentials"
        )

    def put(self, key_id: str, secret: bytes) -> None:
        self.backend.put(key_id, secret)

    def get(self, key_id: str) -> bytes:
        return self.backend.get(key_id)


class EncryptedFileSecretStore:
    """Explicit fallback for hosts without an available platform credential service."""

    warning = (
        "Using encrypted owner-only file fallback; configure Keychain, DPAPI, or "
        "libsecret before production use."
    )

    def __init__(self, root: Path, wrapping_key: bytes) -> None:
        if len(wrapping_key) != 32:
            raise ValueError("fallback wrapping key must contain 32 bytes")
        self.root = root
        self.wrapping_key = bytes(wrapping_key)

    def put(self, key_id: str, secret: bytes) -> None:
        if not key_id or any(character not in _KEY_ID for character in key_id):
            raise ValueError("key ID contains unsupported characters")
        nonce = os.urandom(12)
        encrypted = AESGCM(self.wrapping_key).encrypt(
            nonce,
            bytes(secret),
            key_id.encode("utf-8"),
        )
        if self.root.exists():
            directory = validate_private_directory(self.root)
            if not directory.safe:
                raise PermissionError(directory.code)
        else:
            self.root.mkdir(mode=0o700, parents=True)
            if os.name == "posix":
                os.chmod(self.root, 0o700)
        create_private_file(self.root / f"{key_id}.key", b"LGKEY1" + nonce + encrypted)

    def get(self, key_id: str) -> bytes:
        path = self.root / f"{key_id}.key"
        result = validate_private_file(path)
        if not result.safe:
            raise PermissionError(result.code)
        payload = path.read_bytes()
        if len(payload) < 35 or not payload.startswith(b"LGKEY1"):
            raise ValueError("encrypted key file is malformed")
        return AESGCM(self.wrapping_key).decrypt(
            payload[6:18],
            payload[18:],
            key_id.encode("utf-8"),
        )


_KEY_ID = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
