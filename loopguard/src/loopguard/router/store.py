from __future__ import annotations

import os
import sqlite3
import stat
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from loopguard.control.paths import ensure_private_home

if TYPE_CHECKING:
    from .outcomes import RoutingOutcome


_SCHEMA = """
CREATE TABLE routing_outcomes (
    routing_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    outcome_json TEXT NOT NULL
);
CREATE INDEX routing_outcomes_created ON routing_outcomes(created_at DESC, routing_id DESC);
PRAGMA user_version = 1;
"""


class OutcomeStoreError(RuntimeError):
    """Routing outcomes could not be stored or read safely."""


class OutcomeStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        ensure_private_home(self.path.parent)
        _prepare_database_file(self.path)
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                self.path,
                isolation_level=None,
                check_same_thread=False,
                timeout=5,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA journal_mode = DELETE")
            self._connection.execute("PRAGMA synchronous = FULL")
            version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            objects = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
            )
            if version == 0 and objects == 0:
                self._connection.executescript(_SCHEMA)
            elif version != 1:
                self._connection.close()
                raise OutcomeStoreError("routing outcome database schema is unsupported")
            self._validate_schema()
            _validate_private_file(self.path)
        except sqlite3.Error as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise OutcomeStoreError("routing outcome database initialization failed") from exc

    def insert(self, outcome: RoutingOutcome) -> None:
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO routing_outcomes(routing_id, created_at, outcome_json) VALUES (?, ?, ?)",
                    (
                        outcome.routing_id,
                        outcome.created_at.isoformat(),
                        outcome.model_dump_json(exclude_computed_fields=True),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise OutcomeStoreError(
                    f"routing outcome {outcome.routing_id!r} already exists"
                ) from exc
            except sqlite3.Error as exc:
                raise OutcomeStoreError("routing outcome insert failed") from exc

    def get(self, routing_id: str) -> RoutingOutcome:
        if not isinstance(routing_id, str) or not routing_id.strip():
            raise ValueError("routing ID must not be empty")
        with self._lock:
            row = self._connection.execute(
                "SELECT outcome_json FROM routing_outcomes WHERE routing_id = ?", (routing_id,)
            ).fetchone()
        if row is None:
            raise KeyError(routing_id)
        return _parse_outcome(str(row["outcome_json"]))

    def list(self, *, limit: int) -> list[RoutingOutcome]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10_000:
            raise ValueError("outcome list limit must be between 1 and 10000")
        with self._lock:
            rows = self._connection.execute(
                "SELECT outcome_json FROM routing_outcomes "
                "ORDER BY created_at DESC, routing_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_parse_outcome(str(row["outcome_json"])) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _validate_schema(self) -> None:
        columns = self._connection.execute("PRAGMA table_info(routing_outcomes)").fetchall()
        actual = [(row[1], row[2], row[3], row[5]) for row in columns]
        expected = [
            ("routing_id", "TEXT", 0, 1),
            ("created_at", "TEXT", 1, 0),
            ("outcome_json", "TEXT", 1, 0),
        ]
        if actual != expected:
            self._connection.close()
            raise OutcomeStoreError("routing outcome database schema validation failed")


def _parse_outcome(payload: str) -> RoutingOutcome:
    from .outcomes import RoutingOutcome

    try:
        return RoutingOutcome.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise OutcomeStoreError("stored routing outcome is invalid") from exc


def _prepare_database_file(path: Path) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        os.close(descriptor)
        status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode):
        raise OutcomeStoreError("routing outcome database cannot be a symlink")
    if not stat.S_ISREG(status.st_mode):
        raise OutcomeStoreError("routing outcome database must be a regular file")
    _validate_private_status(status)


def _validate_private_file(path: Path) -> None:
    status = os.lstat(path)
    if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
        raise OutcomeStoreError("routing outcome database path is unsafe")
    _validate_private_status(status)


def _validate_private_status(status: os.stat_result) -> None:
    if os.name == "posix" and (
        status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
    ):
        raise OutcomeStoreError("routing outcome database must be owner-only")
