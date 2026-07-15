from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import _repository_path


_FORBIDDEN_FIELDS = {"reasoning", "chain_of_thought", "scratchpad"}
_SCHEMA = """
CREATE TABLE handoffs (
    handoff_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    owner_session_id TEXT NOT NULL,
    repo_seq INTEGER NOT NULL CHECK(repo_seq >= 0),
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX handoffs_repo_cursor ON handoffs(repo_id, repo_seq, created_at, handoff_id);
CREATE INDEX handoffs_owner ON handoffs(repo_id, owner_session_id, created_at);
PRAGMA user_version = 1;
"""


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(max_length=16_000)
    accepted_decisions: list[str] = Field(default_factory=list, max_length=1_024)
    changed_paths: list[str] = Field(default_factory=list, max_length=10_000)
    verification_ids: list[str] = Field(default_factory=list, max_length=10_000)
    unresolved: list[str] = Field(default_factory=list, max_length=1_024)
    risks: list[str] = Field(default_factory=list, max_length=1_024)
    repo_seq: int = Field(ge=0)
    commit_sha: str | None = Field(default=None, min_length=7, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def reject_hidden_reasoning(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            forbidden = {
                str(key).strip().lower() for key in value if str(key).strip().lower() in _FORBIDDEN_FIELDS
            }
            if forbidden:
                raise ValueError("handoff cannot contain hidden reasoning fields")
        return value

    @field_validator("goal")
    @classmethod
    def normalize_goal(cls, value: str) -> str:
        return _bounded_text(value, field="handoff goal", limit=16_000)

    @field_validator("accepted_decisions", "unresolved", "risks")
    @classmethod
    def normalize_text_lists(cls, values: list[str]) -> list[str]:
        normalized = [_bounded_text(value, field="handoff entry", limit=8_000) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("handoff entries must be unique")
        return normalized

    @field_validator("changed_paths")
    @classmethod
    def normalize_paths(cls, values: list[str]) -> list[str]:
        normalized = [_repository_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("handoff paths must be unique")
        return sorted(normalized)

    @field_validator("verification_ids")
    @classmethod
    def normalize_verifications(cls, values: list[str]) -> list[str]:
        normalized = [
            _bounded_text(value, field="verification ID", limit=512) for value in values
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError("handoff verification IDs must be unique")
        return sorted(normalized)

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("handoff commit SHA must be hexadecimal")
        return normalized

    @model_validator(mode="after")
    def bound_total_content(self) -> Handoff:
        total = len(self.goal) + sum(
            len(value)
            for values in (
                self.accepted_decisions,
                self.changed_paths,
                self.verification_ids,
                self.unresolved,
                self.risks,
            )
            for value in values
        )
        if total > 1_000_000:
            raise ValueError("handoff exceeds the total content limit")
        return self


class HandoffArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str = Field(min_length=32, max_length=32)
    repo_id: str = Field(min_length=1, max_length=512)
    owner_session_id: str = Field(min_length=1, max_length=512)
    created_at: AwareDatetime
    handoff: Handoff


class HandoffStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        sidecars = (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
        if any(candidate.is_symlink() for candidate in sidecars):
            raise ValueError("handoff database cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.resolve(strict=True) != self.path.parent:
            raise ValueError("handoff database parent cannot contain a symlink")
        if self.path.exists():
            status = os.lstat(self.path)
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("handoff database must be a regular file")
            if os.name == "posix" and (
                status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
            ):
                raise ValueError("handoff database must be owner-only")
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
                raise ValueError("unversioned handoff database is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            self._connection.close()
            raise ValueError("handoff database schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._validate_schema()
        self._secure_files()

    def create(self, repo_id: str, session_id: str, handoff: Handoff) -> HandoffArtifact:
        _identity(repo_id, session_id)
        handoff = Handoff.model_validate(handoff)
        handoff_id = uuid4().hex
        created_at = datetime.now(timezone.utc)
        artifact = HandoffArtifact(
            handoff_id=handoff_id,
            repo_id=repo_id,
            owner_session_id=session_id,
            created_at=created_at,
            handoff=handoff,
        )
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO handoffs(
                        handoff_id, repo_id, owner_session_id, repo_seq,
                        created_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.handoff_id,
                        artifact.repo_id,
                        artifact.owner_session_id,
                        artifact.handoff.repo_seq,
                        artifact.created_at.isoformat(),
                        artifact.handoff.model_dump_json(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
        return artifact

    def read(self, repo_id: str, handoff_id: str) -> HandoffArtifact:
        _identifier(repo_id, field="repository identity", limit=512)
        normalized_id = _identifier(handoff_id, field="handoff ID", limit=64)
        with self._lock:
            row = self._require_open().execute(
                "SELECT * FROM handoffs WHERE repo_id = ? AND handoff_id = ?",
                (repo_id, normalized_id),
            ).fetchone()
        if row is None:
            raise KeyError("handoff not found")
        return HandoffArtifact(
            handoff_id=str(row["handoff_id"]),
            repo_id=str(row["repo_id"]),
            owner_session_id=str(row["owner_session_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            handoff=Handoff.model_validate_json(row["payload_json"]),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            self._secure_files()

    def __enter__(self) -> HandoffStore:
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
            raise ValueError("handoff store is closed")
        return self._connection

    def _validate_schema(self) -> None:
        connection = self._require_open()
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        table = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'handoffs'"
        ).fetchone()
        if integrity != "ok" or table is None:
            raise ValueError("handoff database schema or integrity check failed")

    def _secure_files(self) -> None:
        if os.name != "posix":
            return
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.is_symlink():
                raise ValueError("handoff database state cannot be a symlink")
            if path.exists():
                path.chmod(0o600)


def _bounded_text(value: str, *, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > limit or "\x00" in normalized:
        raise ValueError(f"{field} must be bounded and non-empty")
    return normalized


def _identifier(value: str, *, field: str, limit: int) -> str:
    return _bounded_text(value, field=field, limit=limit)


def _identity(repo_id: str, session_id: str) -> None:
    _identifier(repo_id, field="repository identity", limit=512)
    _identifier(session_id, field="session identity", limit=512)
