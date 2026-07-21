from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


@dataclass(frozen=True, slots=True)
class Ed25519ActionSigner:
    key_id: str
    _private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls, key_id: str) -> Ed25519ActionSigner:
        return cls(key_id, Ed25519PrivateKey.generate())

    @property
    def algorithm(self) -> str:
        return "Ed25519"

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._private_key.public_key()

    def sign(self, payload: bytes) -> str:
        return base64.urlsafe_b64encode(self._private_key.sign(payload)).decode()
