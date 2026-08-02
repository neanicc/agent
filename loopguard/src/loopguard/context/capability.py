from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from loopguard.control.paths import ensure_private_home


ContextTool = Literal[
    "get_repo_state",
    "get_changes_since",
    "get_verification_status",
    "claim_work",
    "release_work",
    "create_handoff",
    "read_handoff",
]
ALL_CONTEXT_TOOLS: tuple[ContextTool, ...] = (
    "claim_work",
    "create_handoff",
    "get_changes_since",
    "get_repo_state",
    "get_verification_status",
    "read_handoff",
    "release_work",
)
_SCHEMA = """
CREATE TABLE capabilities (
    nonce TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);
CREATE INDEX capabilities_expiry ON capabilities(expires_at, consumed_at);
PRAGMA user_version = 1;
"""


class ContextCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host_id: str = Field(min_length=1, max_length=512)
    repo_id: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=512)
    allowed_tools: list[ContextTool] = Field(min_length=1, max_length=len(ALL_CONTEXT_TOOLS))
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    nonce: str = Field(min_length=64, max_length=64)

    @field_validator("host_id", "repo_id", "session_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("capability identity must be non-empty")
        return normalized

    @field_validator("allowed_tools")
    @classmethod
    def validate_tools(cls, values: list[ContextTool]) -> list[ContextTool]:
        if values != sorted(set(values)):
            raise ValueError("capability tools must be sorted and unique")
        return values

    @field_validator("nonce")
    @classmethod
    def validate_nonce(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("capability nonce must be hexadecimal")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> ContextCapability:
        lifetime = (self.expires_at - self.issued_at).total_seconds()
        if not 1 <= lifetime <= 15 * 60:
            raise ValueError("capability lifetime must be between one second and 15 minutes")
        return self


@dataclass(frozen=True, slots=True)
class IssuedCapability:
    capability: ContextCapability
    token: bytes


class CapabilityIssuer:
    def __init__(
        self,
        home: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.home = home.expanduser().absolute()
        ensure_private_home(self.home)
        self.key_path = self.home / "context-capability.key"
        self.database_path = self.home / "context-capabilities.db"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._key = _load_or_create_key(self.key_path)
        self._lock = threading.RLock()
        self._closed = False
        _create_owner_file(self.database_path)
        self._connection = sqlite3.connect(
            self.database_path,
            isolation_level=None,
            check_same_thread=False,
            timeout=5,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 5000")
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        objects = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        if version == 0:
            if objects:
                self._connection.close()
                raise ValueError("unversioned capability database is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            self._connection.close()
            raise ValueError("capability database schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._validate_schema()
        self._secure_files()

    def issue(
        self,
        *,
        host_id: str,
        repo_id: str,
        session_id: str,
        allowed_tools: Iterable[ContextTool] = ALL_CONTEXT_TOOLS,
        ttl: timedelta = timedelta(minutes=5),
    ) -> IssuedCapability:
        now = _aware_utc(self._clock())
        capability = ContextCapability(
            host_id=host_id,
            repo_id=repo_id,
            session_id=session_id,
            allowed_tools=sorted(set(allowed_tools)),
            issued_at=now,
            expires_at=now + ttl,
            nonce=secrets.token_hex(32),
        )
        payload = _canonical_payload(capability)
        payload_hash = hashlib.sha256(payload).hexdigest()
        mac = hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        token = json.dumps(
            {"capability": json.loads(payload), "mac": mac},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO capabilities(nonce, payload_hash, expires_at) VALUES (?, ?, ?)",
                    (capability.nonce, payload_hash, capability.expires_at.timestamp()),
                )
                connection.execute(
                    "DELETE FROM capabilities WHERE expires_at < ? AND consumed_at IS NOT NULL",
                    ((now - timedelta(days=1)).timestamp(),),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
        return IssuedCapability(capability=capability, token=token)

    def write_token(self, issued: IssuedCapability, path: Path | None = None) -> Path:
        destination = path or self.home / f"context-capability-{issued.capability.nonce}.json"
        destination = destination.expanduser().absolute()
        if destination.parent.resolve(strict=True) != self.home.resolve(strict=True):
            raise ValueError("capability token file must stay in the LoopGuard home")
        descriptor = os.open(
            destination,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            _write_all(descriptor, issued.token)
            os.fsync(descriptor)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return destination

    def consume_token(self, token: bytes) -> ContextCapability:
        if not token or len(token) > 64 * 1024:
            raise ValueError("capability token is empty or oversized")
        try:
            envelope = json.loads(token)
            if not isinstance(envelope, dict) or set(envelope) != {"capability", "mac"}:
                raise ValueError
            capability = ContextCapability.model_validate(envelope["capability"])
            supplied_mac = str(envelope["mac"])
        except Exception as exc:
            raise ValueError("capability token is malformed") from exc
        payload = _canonical_payload(capability)
        expected_mac = hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_mac, expected_mac):
            raise ValueError("capability token authentication failed")
        now = _aware_utc(self._clock())
        if capability.issued_at > now + timedelta(seconds=30) or capability.expires_at <= now:
            raise ValueError("capability token is expired or not yet valid")
        payload_hash = hashlib.sha256(payload).hexdigest()
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE capabilities SET consumed_at = ?
                    WHERE nonce = ? AND payload_hash = ? AND consumed_at IS NULL
                      AND expires_at > ?
                    """,
                    (now.timestamp(), capability.nonce, payload_hash, now.timestamp()),
                )
                if cursor.rowcount != 1:
                    raise ValueError("capability token was already consumed or revoked")
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
        return capability

    def consume_file(self, path: Path) -> ContextCapability:
        path = path.expanduser().absolute()
        _validate_owner_file(path, label="capability token")
        token = path.read_bytes()
        capability = self.consume_token(token)
        path.unlink()
        return capability

    def consume_fd(self, descriptor: int) -> ContextCapability:
        if descriptor < 0:
            raise ValueError("capability descriptor must not be negative")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8192, 64 * 1024 + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 64 * 1024:
                raise ValueError("capability token is oversized")
        return self.consume_token(b"".join(chunks))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            self._secure_files()

    def __enter__(self) -> CapabilityIssuer:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _require_open(self) -> sqlite3.Connection:
        if self._closed:
            raise ValueError("capability issuer is closed")
        return self._connection

    def _validate_schema(self) -> None:
        connection = self._require_open()
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        table = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'capabilities'"
        ).fetchone()
        if integrity != "ok" or table is None:
            raise ValueError("capability database schema or integrity check failed")

    def _secure_files(self) -> None:
        if os.name != "posix":
            return
        for path in (
            self.key_path,
            self.database_path,
            Path(f"{self.database_path}-wal"),
            Path(f"{self.database_path}-shm"),
        ):
            if path.is_symlink():
                raise ValueError("capability state cannot be a symlink")
            if path.exists():
                path.chmod(0o600)


def load_capability_from_environment(home: Path) -> ContextCapability:
    descriptor_value = os.environ.get("LOOPGUARD_CONTEXT_CAPABILITY_FD")
    path_value = os.environ.get("LOOPGUARD_CONTEXT_CAPABILITY_FILE")
    if (descriptor_value is None) == (path_value is None):
        raise ValueError("exactly one capability descriptor or file is required")
    with CapabilityIssuer(home) as issuer:
        if descriptor_value is not None:
            try:
                descriptor = int(descriptor_value)
            except ValueError as exc:
                raise ValueError("capability descriptor is invalid") from exc
            return issuer.consume_fd(descriptor)
        assert path_value is not None
        return issuer.consume_file(Path(path_value))


class ContextMCPLauncher:
    def __init__(self, home: Path, *, executable: str | None = None) -> None:
        self.home = home.expanduser().absolute()
        self.executable = executable or sys.executable
        self.issuer = CapabilityIssuer(self.home)

    def launch(
        self,
        *,
        host_id: str,
        repo_id: str,
        session_id: str,
        allowed_tools: Iterable[ContextTool] = ALL_CONTEXT_TOOLS,
        ttl: timedelta = timedelta(minutes=5),
    ) -> subprocess.Popen[bytes]:
        issued = self.issuer.issue(
            host_id=host_id,
            repo_id=repo_id,
            session_id=session_id,
            allowed_tools=allowed_tools,
            ttl=ttl,
        )
        environment = os.environ.copy()
        command = [
            self.executable,
            "-m",
            "loopguard",
            "context-mcp",
            "--home",
            str(self.home),
        ]
        if os.name == "posix":
            read_descriptor, write_descriptor = os.pipe()
            try:
                _write_all(write_descriptor, issued.token)
            finally:
                os.close(write_descriptor)
            environment["LOOPGUARD_CONTEXT_CAPABILITY_FD"] = str(read_descriptor)
            try:
                return subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    pass_fds=(read_descriptor,),
                )
            finally:
                os.close(read_descriptor)
        token_path = self.issuer.write_token(issued)
        environment["LOOPGUARD_CONTEXT_CAPABILITY_FILE"] = str(token_path)
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )

    def close(self) -> None:
        self.issuer.close()

    def __enter__(self) -> ContextMCPLauncher:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _canonical_payload(capability: ContextCapability) -> bytes:
    return json.dumps(
        capability.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_or_create_key(path: Path) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except FileExistsError:
        _validate_owner_file(path, label="capability key")
        key = path.read_bytes()
    else:
        key = secrets.token_bytes(32)
        try:
            _write_all(descriptor, key)
            os.fsync(descriptor)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    if len(key) != 32:
        raise ValueError("capability key has an invalid length")
    return key


def _write_all(descriptor: int, body: bytes) -> None:
    view = memoryview(body)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("capability write stalled")
        written += count


def _create_owner_file(path: Path) -> None:
    if path.exists():
        _validate_owner_file(path, label="capability database")
        return
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    os.close(descriptor)


def _validate_owner_file(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} cannot be a symlink")
    status = os.lstat(path)
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if os.name == "posix" and (
        status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
    ):
        raise ValueError(f"{label} must be owner-only")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("capability clock must return an aware datetime")
    return value.astimezone(timezone.utc)
