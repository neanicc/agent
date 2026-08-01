from __future__ import annotations

import hashlib
import hmac
import os
import stat
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from loopguard.control.crypto import (
    KeyStore,
    MissingKeyError,
    PlatformKeyStore,
    decrypt,
    derive_data_key,
    encrypt,
    generate_data_key,
)
from loopguard.control.paths import ensure_private_home


_HEADER = b"LGBA1"
_MAX_STATE_BYTES = 16 * 1024 * 1024


class AuthScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    repo_id: str = Field(min_length=1, max_length=256)
    profile: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    allowed_origins: tuple[str, ...] = Field(default_factory=tuple, max_length=128)

    @field_validator("tenant_id", "user_id", "repo_id", "profile", "environment")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("browser auth scope cannot contain null bytes")
        return value


class AuthStateReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^auth-[0-9a-f]{48}$")
    scope: AuthScope


class PlaintextAuthLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str = Field(pattern=r"^lease-[0-9a-f]{32}$")
    path: Path


class BrowserAuthStore:
    def __init__(
        self,
        root: str | Path,
        *,
        plaintext_directory: str | Path,
        encryption_key: bytes,
    ) -> None:
        if len(encryption_key) != 32:
            raise ValueError("browser auth encryption key must contain 32 bytes")
        self.root = Path(root).expanduser().absolute()
        self.plaintext_directory = Path(plaintext_directory).expanduser().absolute()
        ensure_private_home(self.root)
        if self.plaintext_directory.is_symlink():
            raise ValueError("browser auth plaintext directory cannot be a symlink")
        ensure_private_home(self.plaintext_directory)
        self._key = bytes(encryption_key)
        self._active: dict[str, Path] = {}
        self._cleanup_plaintext_directory()

    @classmethod
    def for_home(
        cls,
        home: str | Path,
        *,
        key_store: KeyStore | None = None,
    ) -> BrowserAuthStore:
        resolved = Path(home).expanduser().absolute()
        return cls.open(
            resolved / "browser-auth",
            plaintext_directory=resolved / "browser" / "auth-plaintext",
            key_store=key_store,
        )

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        plaintext_directory: str | Path,
        key_store: KeyStore | None = None,
    ) -> BrowserAuthStore:
        resolved = Path(root).expanduser().absolute()
        ensure_private_home(resolved)
        credentials = key_store or PlatformKeyStore(
            service_name="dev.loopguard.browser-auth-state"
        )
        credential_id = hashlib.sha256(
            b"loopguard-browser-auth-credential-v1\0" + str(resolved).encode()
        ).hexdigest()
        existing = any(resolved.glob("*.state"))
        try:
            key = credentials.get(credential_id)
        except MissingKeyError:
            if existing:
                raise
            key = generate_data_key()
            credentials.put(credential_id, key)
        return cls(
            resolved,
            plaintext_directory=plaintext_directory,
            encryption_key=key,
        )

    @classmethod
    def for_test(
        cls,
        root: str | Path,
        *,
        plaintext_directory: str | Path,
        key: bytes = b"loopguard-browser-auth-test-key",
    ) -> BrowserAuthStore:
        return cls(
            root,
            plaintext_directory=plaintext_directory,
            encryption_key=derive_data_key(key),
        )

    def save(
        self,
        *,
        repo_id: str,
        profile: str,
        environment: str,
        body: bytes,
        tenant_id: str = "local",
        user_id: str = "local",
        allowed_origins: Sequence[str] = (),
    ) -> str:
        if not isinstance(body, bytes) or not body or len(body) > _MAX_STATE_BYTES:
            raise ValueError("browser storage state must be bounded non-empty bytes")
        scope = _scope(
            tenant_id=tenant_id,
            user_id=user_id,
            repo_id=repo_id,
            profile=profile,
            environment=environment,
            allowed_origins=allowed_origins,
        )
        reference = self._reference(scope)
        nonce, ciphertext = encrypt(self._key, body, _aad(reference))
        _atomic_write(self.root / f"{reference.key}.state", _HEADER + nonce + ciphertext)
        return reference.key

    def load(
        self,
        repo_id: str,
        profile: str,
        environment: str,
        *,
        tenant_id: str = "local",
        user_id: str = "local",
        allowed_origins: Sequence[str] = (),
    ) -> AuthStateReference | None:
        scope = _scope(
            tenant_id=tenant_id,
            user_id=user_id,
            repo_id=repo_id,
            profile=profile,
            environment=environment,
            allowed_origins=allowed_origins,
        )
        reference = self._reference(scope)
        path = self.root / f"{reference.key}.state"
        if not path.exists() and not path.is_symlink():
            return None
        self._decrypt(reference)
        return reference

    def materialize(self, reference: AuthStateReference) -> PlaintextAuthLease:
        validated = AuthStateReference.model_validate(reference)
        expected = self._reference(validated.scope)
        if not hmac.compare_digest(expected.key, validated.key):
            raise ValueError("browser auth reference does not match its scope")
        body = self._decrypt(validated)
        lease_id = f"lease-{os.urandom(16).hex()}"
        path = self.plaintext_directory / f"{lease_id}.json"
        descriptor = os.open(
            path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            _write_all(descriptor, body)
            os.fsync(descriptor)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self._active[lease_id] = path
        return PlaintextAuthLease(lease_id=lease_id, path=path)

    def release(self, lease: PlaintextAuthLease) -> None:
        validated = PlaintextAuthLease.model_validate(lease)
        path = self._active.pop(validated.lease_id, validated.path)
        _secure_unlink(path, root=self.plaintext_directory)

    def close(self) -> None:
        for path in list(self._active.values()):
            _secure_unlink(path, root=self.plaintext_directory)
        self._active.clear()
        self._cleanup_plaintext_directory()

    def _reference(self, scope: AuthScope) -> AuthStateReference:
        digest = hmac.new(
            self._key,
            b"loopguard-browser-auth-scope-v1\0" + _scope_bytes(scope),
            hashlib.sha256,
        ).hexdigest()[:48]
        return AuthStateReference(key=f"auth-{digest}", scope=scope)

    def _decrypt(self, reference: AuthStateReference) -> bytes:
        path = self.root / f"{reference.key}.state"
        raw = _safe_read(path, maximum=_MAX_STATE_BYTES + 64)
        if not raw.startswith(_HEADER) or len(raw) < len(_HEADER) + 12 + 16:
            raise ValueError("encrypted browser auth state envelope is invalid")
        return decrypt(
            self._key,
            raw[len(_HEADER) : len(_HEADER) + 12],
            raw[len(_HEADER) + 12 :],
            _aad(reference),
        )

    def _cleanup_plaintext_directory(self) -> None:
        for path in self.plaintext_directory.iterdir():
            _secure_unlink(path, root=self.plaintext_directory)


def _scope(
    *,
    tenant_id: str,
    user_id: str,
    repo_id: str,
    profile: str,
    environment: str,
    allowed_origins: Sequence[str],
) -> AuthScope:
    origins = normalize_origins(allowed_origins)
    return AuthScope(
        tenant_id=tenant_id,
        user_id=user_id,
        repo_id=repo_id,
        profile=profile,
        environment=environment,
        allowed_origins=origins,
    )


def normalize_origins(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({_origin(value) for value in values}))


def _origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("browser auth origins must be exact HTTP(S) origins")
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port
    authority = parsed.hostname.lower()
    if ":" in authority:
        authority = f"[{authority}]"
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"


def _scope_bytes(scope: AuthScope) -> bytes:
    return scope.model_dump_json().encode("utf-8")


def _aad(reference: AuthStateReference) -> bytes:
    return b"loopguard-browser-auth-state-v1\0" + reference.key.encode() + b"\0" + _scope_bytes(
        reference.scope
    )


def _safe_read(path: Path, *, maximum: int) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        raise ValueError("browser auth state path is unsafe") from exc
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ValueError("browser auth state path is unsafe")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise ValueError("browser auth state must be owner-only")
        if status.st_size > maximum:
            raise ValueError("browser auth state exceeds its size limit")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > maximum:
            raise ValueError("browser auth state exceeds its size limit")
        return raw
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, body: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        _write_all(descriptor, body)
        os.fsync(descriptor)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _secure_unlink(path: Path, *, root: Path) -> None:
    if path.parent != root:
        raise ValueError("browser auth plaintext path is outside its lease directory")
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(status.st_mode):
        path.unlink()
        _fsync_directory(root)
        return
    if not stat.S_ISREG(status.st_mode):
        raise ValueError("browser auth plaintext lease is not a regular file")
    descriptor = os.open(
        path,
        os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
    )
    try:
        remaining = status.st_size
        zeros = b"\0" * min(64 * 1024, max(1, remaining))
        while remaining:
            written = os.write(descriptor, zeros[:remaining])
            if written <= 0:
                raise OSError("browser auth plaintext overwrite stalled")
            remaining -= written
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.unlink(missing_ok=True)
    _fsync_directory(root)


def _write_all(descriptor: int, body: bytes) -> None:
    view = memoryview(body)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("browser auth write stalled")
        written += count


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
