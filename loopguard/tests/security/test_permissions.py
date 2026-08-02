from __future__ import annotations

import os
from pathlib import Path

import pytest

from loopguard.security.keys import EncryptedFileSecretStore
from loopguard.security.permissions import (
    create_private_file,
    validate_private_directory,
    validate_private_file,
)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_local_state_rejects_group_readable_secret_file(tmp_path: Path) -> None:
    path = tmp_path / "host.key"
    path.write_text("secret")
    path.chmod(0o640)

    assert validate_private_file(path).code == "unsafe_permissions"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_private_state_requires_owner_only_directory(tmp_path: Path) -> None:
    path = tmp_path / "state"
    path.mkdir(mode=0o755)

    assert validate_private_directory(path).code == "unsafe_permissions"


def test_private_file_creation_refuses_overwrite_and_symlink(tmp_path: Path) -> None:
    path = tmp_path / "credential"
    create_private_file(path, b"first")

    with pytest.raises(FileExistsError):
        create_private_file(path, b"second")

    assert path.read_bytes() == b"first"
    assert validate_private_file(path).safe


def test_encrypted_fallback_is_explicit_owner_only_and_not_plaintext(
    tmp_path: Path,
) -> None:
    store = EncryptedFileSecretStore(tmp_path / "keys", b"k" * 32)
    store.put("host-1", b"secret-host-key")

    payload = (tmp_path / "keys" / "host-1.key").read_bytes()
    assert b"secret-host-key" not in payload
    assert store.get("host-1") == b"secret-host-key"
    assert "fallback" in store.warning
    assert validate_private_file(tmp_path / "keys" / "host-1.key").safe


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_encrypted_fallback_does_not_repair_an_attacker_controlled_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "keys"
    root.mkdir(mode=0o755)

    with pytest.raises(PermissionError, match="unsafe_permissions"):
        EncryptedFileSecretStore(root, b"k" * 32).put("host-1", b"secret")

    assert (root.stat().st_mode & 0o777) == 0o755
