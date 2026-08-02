from __future__ import annotations

import os
import sqlite3
import stat
import threading
import unicodedata
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .symbols import SymbolSnapshot


_SCHEMA = """
CREATE TABLE leases (
    lease_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    owner_session_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('file', 'symbol')),
    path TEXT NOT NULL,
    symbol_name TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL CHECK(expires_at > acquired_at),
    enforcement_mode TEXT NOT NULL CHECK(enforcement_mode IN ('advisory', 'enforced')),
    UNIQUE(repo_id, scope_kind, path, symbol_name)
);
CREATE INDEX leases_overlap
ON leases(repo_id, path, expires_at, scope_kind, symbol_name);
CREATE INDEX leases_owner
ON leases(repo_id, owner_session_id, expires_at);
PRAGMA user_version = 1;
"""

LeaseKind = Literal["file", "symbol"]
EnforcementMode = Literal["advisory", "enforced"]
LeaseStatus = Literal["acquired", "renewed", "conflict"]


class LeaseScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    kind: LeaseKind
    path: str
    symbol_name: str | None = Field(default=None, alias="symbol")

    @classmethod
    def file(cls, path: str) -> LeaseScope:
        return cls(kind="file", path=path)

    @classmethod
    def symbol(cls, path: str, symbol: str) -> LeaseScope:
        return cls(kind="symbol", path=path, symbol=symbol)

    @model_validator(mode="after")
    def normalize_scope(self) -> LeaseScope:
        path = SymbolSnapshot(path=self.path, language="unknown").path
        symbol = _normalized_symbol(self.symbol_name) if self.symbol_name is not None else None
        if self.kind == "file" and symbol is not None:
            raise ValueError("file lease cannot name a symbol")
        if self.kind == "symbol" and symbol is None:
            raise ValueError("symbol lease requires a symbol")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "symbol_name", symbol)
        return self


class LeaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: LeaseStatus
    lease_id: str
    repo_id: str
    owner_session_id: str
    scope: LeaseScope
    acquired_at: datetime
    expires_at: datetime
    enforcement_mode: EnforcementMode


class LeaseManager:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path.expanduser().absolute()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        sidecars = (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
        if any(candidate.is_symlink() for candidate in sidecars):
            raise ValueError("lease database cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.resolve(strict=True) != self.path.parent:
            raise ValueError("lease database parent cannot contain a symlink")
        if self.path.exists():
            status = os.lstat(self.path)
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("lease database must be a regular file")
            if os.name == "posix" and (
                status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
            ):
                raise ValueError("lease database must be owner-only")
        else:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.path,
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
                raise ValueError("unversioned lease database is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            self._connection.close()
            raise ValueError("lease database schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._validate_schema()
        self._secure_files()

    def acquire(
        self,
        repo_id: str,
        session_id: str,
        scope: LeaseScope,
        *,
        ttl: timedelta = timedelta(minutes=10),
        enforcement_mode: EnforcementMode | None = None,
    ) -> LeaseResult:
        _identity(repo_id, session_id)
        scope = LeaseScope.model_validate(scope)
        ttl_seconds = ttl.total_seconds()
        if not 1 <= ttl_seconds <= 24 * 60 * 60:
            raise ValueError("lease TTL must be between one second and 24 hours")
        now = _aware_utc(self._clock())
        now_epoch = now.timestamp()
        requested_expiry = now_epoch + ttl_seconds
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now_epoch,))
                conflict = connection.execute(
                    """
                    SELECT * FROM leases
                    WHERE repo_id = ? AND path = ? AND owner_session_id != ?
                      AND expires_at > ?
                      AND (scope_kind = 'file' OR ? = 'file' OR symbol_name = ?)
                    ORDER BY CASE scope_kind WHEN 'file' THEN 0 ELSE 1 END,
                             acquired_at, owner_session_id, lease_id
                    LIMIT 1
                    """,
                    (
                        repo_id,
                        scope.path,
                        session_id,
                        now_epoch,
                        scope.kind,
                        scope.symbol_name or "",
                    ),
                ).fetchone()
                if conflict is not None:
                    result = _result(conflict, status="conflict")
                    connection.execute("COMMIT")
                    return result
                existing = connection.execute(
                    """
                    SELECT * FROM leases
                    WHERE repo_id = ? AND owner_session_id = ? AND scope_kind = ?
                      AND path = ? AND symbol_name = ?
                    """,
                    (
                        repo_id,
                        session_id,
                        scope.kind,
                        scope.path,
                        scope.symbol_name or "",
                    ),
                ).fetchone()
                if existing is not None:
                    expires_at = max(float(existing["expires_at"]), requested_expiry)
                    mode = enforcement_mode or str(existing["enforcement_mode"])
                    connection.execute(
                        "UPDATE leases SET expires_at = ?, enforcement_mode = ? "
                        "WHERE lease_id = ?",
                        (expires_at, mode, existing["lease_id"]),
                    )
                    renewed = connection.execute(
                        "SELECT * FROM leases WHERE lease_id = ?",
                        (existing["lease_id"],),
                    ).fetchone()
                    assert renewed is not None
                    result = _result(renewed, status="renewed")
                else:
                    lease_id = uuid4().hex
                    mode = enforcement_mode or "advisory"
                    connection.execute(
                        """
                        INSERT INTO leases(
                            lease_id, repo_id, owner_session_id, scope_kind, path,
                            symbol_name, acquired_at, expires_at, enforcement_mode
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            lease_id,
                            repo_id,
                            session_id,
                            scope.kind,
                            scope.path,
                            scope.symbol_name or "",
                            now_epoch,
                            requested_expiry,
                            mode,
                        ),
                    )
                    acquired = connection.execute(
                        "SELECT * FROM leases WHERE lease_id = ?", (lease_id,)
                    ).fetchone()
                    assert acquired is not None
                    result = _result(acquired, status="acquired")
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
            return result

    def release(
        self,
        repo_id: str,
        session_id: str,
        *,
        scopes: Sequence[LeaseScope] | None = None,
    ) -> int:
        _identity(repo_id, session_id)
        normalized = [LeaseScope.model_validate(scope) for scope in scopes or ()]
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                if scopes is None:
                    cursor = connection.execute(
                        "DELETE FROM leases WHERE repo_id = ? AND owner_session_id = ?",
                        (repo_id, session_id),
                    )
                    count = cursor.rowcount
                else:
                    count = 0
                    for scope in normalized:
                        cursor = connection.execute(
                            """
                            DELETE FROM leases
                            WHERE repo_id = ? AND owner_session_id = ? AND scope_kind = ?
                              AND path = ? AND symbol_name = ?
                            """,
                            (
                                repo_id,
                                session_id,
                                scope.kind,
                                scope.path,
                                scope.symbol_name or "",
                            ),
                        )
                        count += cursor.rowcount
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
            return count

    def purge_expired(self, repo_id: str | None = None) -> int:
        if repo_id is not None and not repo_id.strip():
            raise ValueError("repository identity must not be empty")
        now_epoch = _aware_utc(self._clock()).timestamp()
        with self._lock:
            connection = self._require_open()
            if repo_id is None:
                cursor = connection.execute(
                    "DELETE FROM leases WHERE expires_at <= ?", (now_epoch,)
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM leases WHERE repo_id = ? AND expires_at <= ?",
                    (repo_id, now_epoch),
                )
            return cursor.rowcount

    def active(self, repo_id: str, *, session_id: str | None = None) -> list[LeaseResult]:
        if not repo_id.strip() or (session_id is not None and not session_id.strip()):
            raise ValueError("lease query identity must not be empty")
        now_epoch = _aware_utc(self._clock()).timestamp()
        with self._lock:
            connection = self._require_open()
            connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now_epoch,))
            if session_id is None:
                rows = connection.execute(
                    "SELECT * FROM leases WHERE repo_id = ? "
                    "ORDER BY path, scope_kind, symbol_name, owner_session_id",
                    (repo_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM leases WHERE repo_id = ? AND owner_session_id = ? "
                    "ORDER BY path, scope_kind, symbol_name",
                    (repo_id, session_id),
                ).fetchall()
            return [_result(row, status="acquired") for row in rows]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            self._secure_files()

    def __enter__(self) -> LeaseManager:
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
            raise ValueError("lease manager is closed")
        return self._connection

    def _validate_schema(self) -> None:
        connection = self._require_open()
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        table = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'leases'"
        ).fetchone()
        if integrity != "ok" or table is None:
            raise ValueError("lease database schema or integrity check failed")

    def _secure_files(self) -> None:
        if os.name != "posix":
            return
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.is_symlink():
                raise ValueError("lease database state cannot be a symlink")
            if path.exists():
                path.chmod(0o600)


def _normalized_symbol(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized or len(normalized) > 1024 or "\x00" in normalized:
        raise ValueError("lease symbol must be bounded and non-empty")
    return normalized


def _identity(repo_id: str, session_id: str) -> None:
    if (
        not repo_id.strip()
        or not session_id.strip()
        or len(repo_id) > 1024
        or len(session_id) > 1024
        or "\x00" in repo_id
        or "\x00" in session_id
    ):
        raise ValueError("lease identity must be bounded and non-empty")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lease clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _result(row: sqlite3.Row, *, status: LeaseStatus) -> LeaseResult:
    kind = str(row["scope_kind"])
    scope = (
        LeaseScope.file(str(row["path"]))
        if kind == "file"
        else LeaseScope.symbol(str(row["path"]), str(row["symbol_name"]))
    )
    return LeaseResult(
        status=status,
        lease_id=str(row["lease_id"]),
        repo_id=str(row["repo_id"]),
        owner_session_id=str(row["owner_session_id"]),
        scope=scope,
        acquired_at=datetime.fromtimestamp(float(row["acquired_at"]), tz=timezone.utc),
        expires_at=datetime.fromtimestamp(float(row["expires_at"]), tz=timezone.utc),
        enforcement_mode=str(row["enforcement_mode"]),
    )
