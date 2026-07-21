from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from loopguard.browser.auth import BrowserAuthStore
from loopguard.browser.client import BrowserResult
from loopguard.browser.service import BrowserService
from loopguard.control.crypto import InMemoryKeyStore, MissingKeyError


SECRET = b'{"cookies":[{"name":"token","value":"cookie-secret"}]}'


def test_storage_state_is_scoped_to_user_repo_profile_environment_and_origins(
    tmp_path: Path,
) -> None:
    store = BrowserAuthStore.for_test(
        tmp_path / "auth",
        plaintext_directory=tmp_path / "browser" / "auth-plaintext",
    )
    key = store.save(
        tenant_id="tenant",
        user_id="user",
        repo_id="r1",
        profile="admin",
        environment="staging",
        allowed_origins=["https://staging.example.test"],
        body=SECRET,
    )
    loaded = store.load(
        "r1",
        "admin",
        "staging",
        tenant_id="tenant",
        user_id="user",
        allowed_origins=["https://staging.example.test"],
    )
    assert loaded is not None and loaded.key == key
    assert store.load("r2", "admin", "staging") is None
    assert (
        store.load(
            "r1",
            "admin",
            "production",
            tenant_id="tenant",
            user_id="user",
            allowed_origins=["https://staging.example.test"],
        )
        is None
    )
    assert (
        store.load(
            "r1",
            "admin",
            "staging",
            tenant_id="tenant",
            user_id="user",
            allowed_origins=["https://other.example.test"],
        )
        is None
    )
    persisted = b"".join(path.read_bytes() for path in (tmp_path / "auth").rglob("*") if path.is_file())
    assert SECRET not in persisted
    assert b"cookie-secret" not in persisted
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (tmp_path / "auth").rglob("*.state"))
    store.close()


def test_materialized_state_is_owner_only_and_release_removes_secret(tmp_path: Path) -> None:
    plaintext = tmp_path / "browser" / "auth-plaintext"
    store = BrowserAuthStore.for_test(tmp_path / "auth", plaintext_directory=plaintext)
    key = store.save(repo_id="repo", profile="admin", environment="test", body=SECRET)
    reference = store.load("repo", "admin", "test")
    assert reference is not None and reference.key == key
    lease = store.materialize(reference)
    assert lease.path.read_bytes() == SECRET
    assert stat.S_IMODE(lease.path.stat().st_mode) == 0o600
    store.release(lease)
    assert not lease.path.exists()
    assert b"cookie-secret" not in _directory_bytes(plaintext)
    store.close()


def test_restart_cleans_stale_plaintext_before_accepting_work(tmp_path: Path) -> None:
    plaintext = tmp_path / "browser" / "auth-plaintext"
    plaintext.mkdir(parents=True, mode=0o700)
    stale = plaintext / "stale.json"
    stale.write_bytes(SECRET)
    stale.chmod(0o600)

    store = BrowserAuthStore.for_test(tmp_path / "auth", plaintext_directory=plaintext)
    assert not stale.exists()
    assert b"cookie-secret" not in _directory_bytes(plaintext)
    store.close()


def test_service_cleans_plaintext_after_creation_failure_timeout_and_cancel(
    tmp_path: Path,
) -> None:
    async def scenario(mode: str) -> None:
        auth = BrowserAuthStore.for_test(
            tmp_path / f"auth-{mode}",
            plaintext_directory=tmp_path / f"browser-{mode}" / "auth-plaintext",
        )
        auth.save(
            repo_id="repo",
            profile="admin",
            environment="test",
            allowed_origins=["https://example.test"],
            body=SECRET,
        )
        reference = auth.load(
            "repo", "admin", "test", allowed_origins=["https://example.test"]
        )
        assert reference is not None
        client = FailingClient(mode)
        service = BrowserService(
            tmp_path / f"leases-{mode}.json",
            client=client,  # type: ignore[arg-type]
            auth_store=auth,
        )
        await service.session_started("session")
        if mode == "cancel":
            task = asyncio.create_task(
                service.create_context(
                    "session",
                    browser="chromium",
                    allowed_origins=["https://example.test"],
                    auth_reference=reference,
                )
            )
            await client.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError):
                await service.create_context(
                    "session",
                    browser="chromium",
                    allowed_origins=["https://example.test"],
                    auth_reference=reference,
                )
        assert b"cookie-secret" not in _directory_bytes(auth.plaintext_directory)
        await service.close()

    for mode in ("failure", "timeout", "cancel"):
        asyncio.run(scenario(mode))


def test_auth_store_rejects_plaintext_directory_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "plaintext"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        BrowserAuthStore.for_test(tmp_path / "auth", plaintext_directory=link)


def test_platform_key_reference_survives_restart_and_missing_key_fails_closed(
    tmp_path: Path,
) -> None:
    keys = InMemoryKeyStore()
    root = tmp_path / "auth"
    plaintext = tmp_path / "browser" / "auth-plaintext"
    first = BrowserAuthStore.open(
        root,
        plaintext_directory=plaintext,
        key_store=keys,
    )
    first.save(repo_id="repo", profile="admin", environment="test", body=SECRET)
    first.close()
    second = BrowserAuthStore.open(
        root,
        plaintext_directory=plaintext,
        key_store=keys,
    )
    assert second.load("repo", "admin", "test") is not None
    second.close()
    with pytest.raises(MissingKeyError):
        BrowserAuthStore.open(
            root,
            plaintext_directory=plaintext,
            key_store=InMemoryKeyStore(),
        )


class FailingClient:
    generation = 1

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.entered = asyncio.Event()

    async def health(self) -> BrowserResult:
        return BrowserResult(ok=True)

    async def create_context(self, **kwargs) -> BrowserResult:
        path = Path(kwargs["storage_state_path"])
        assert path.read_bytes() == SECRET
        self.entered.set()
        if self.mode == "cancel":
            await asyncio.Event().wait()
        if self.mode == "timeout":
            return BrowserResult(ok=False, code="timeout")
        raise RuntimeError("simulated broker failure")

    async def close(self) -> None:
        return None


def _directory_bytes(path: Path) -> bytes:
    if not path.exists():
        return b""
    return b"".join(candidate.read_bytes() for candidate in path.rglob("*") if candidate.is_file())
