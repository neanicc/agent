from __future__ import annotations

import pytest

from loopguard.control.crypto import (
    CorruptKeyError,
    IntegrityError,
    MissingKeyError,
    PlatformKeyStore,
    decrypt,
    derive_data_key,
    encrypt,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))


def test_injected_key_material_is_domain_derived_to_stable_aes_256_key():
    first = derive_data_key(b"test-key-material")
    second = derive_data_key(b"test-key-material")

    assert first == second
    assert len(first) == 32
    assert first != b"test-key-material".ljust(32, b"\x00")


def test_aes_gcm_authenticates_ciphertext_and_aad():
    key = derive_data_key(b"fixture")
    nonce, ciphertext = encrypt(key, b"control-event", b"cursor-domain:a:1")

    assert decrypt(key, nonce, ciphertext, b"cursor-domain:a:1") == b"control-event"
    with pytest.raises(IntegrityError):
        decrypt(key, nonce, ciphertext, b"cursor-domain:b:1")


def test_platform_key_store_round_trips_through_injected_keyring_backend():
    backend = _FakeKeyring()
    store = PlatformKeyStore(backend=backend, service_name="loopguard-tests")
    key = derive_data_key(b"fixture")

    store.put("key-1", key)

    assert store.get("key-1") == key
    assert backend.values[("loopguard-tests", "key-1")].startswith("v1:")


def test_platform_key_store_names_missing_and_corrupt_credentials():
    backend = _FakeKeyring()
    store = PlatformKeyStore(backend=backend, service_name="loopguard-tests")

    with pytest.raises(MissingKeyError):
        store.get("missing")

    backend.values[("loopguard-tests", "bad")] = "v1:not-base64!"
    with pytest.raises(CorruptKeyError):
        store.get("bad")
