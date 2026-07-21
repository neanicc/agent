from __future__ import annotations

import getpass
import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from loopguard.control.paths import ensure_private_home

from .models import Identifier, NonEmptyText, Sha256


_SCHEMA_VERSION = 1
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_EXPECTED_COLUMNS = {
    "decisions": {
        "row_id",
        "tenant_id",
        "user_id",
        "repository_id",
        "decision_id",
        "choice",
        "context_json",
        "context_hash",
        "artifact_hash",
        "actor_json",
        "payload_hash",
        "created_at",
    },
    "revocations": {
        "tenant_id",
        "user_id",
        "repository_id",
        "decision_id",
        "payload_hash",
        "actor_json",
        "reason",
        "revoked_at",
    },
    "promotions": {
        "tenant_id",
        "user_id",
        "repository_id",
        "rule_id",
        "severity",
        "actor_json",
        "reason",
        "promoted_at",
    },
    "negative_examples": {
        "negative_id",
        "tenant_id",
        "user_id",
        "repository_id",
        "rule_id",
        "context_hash",
        "choice_hash",
        "actor_json",
        "reason",
        "created_at",
    },
}
_SCHEMA = """
CREATE TABLE decisions (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    choice TEXT NOT NULL,
    context_json TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    artifact_hash TEXT,
    actor_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id, repository_id, decision_id)
);
CREATE INDEX decisions_context
ON decisions(tenant_id, user_id, repository_id, context_hash, row_id);
CREATE TABLE revocations (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    revoked_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, user_id, repository_id, decision_id)
);
CREATE TABLE promotions (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('warn', 'block')),
    actor_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, user_id, repository_id, rule_id)
);
CREATE TABLE negative_examples (
    negative_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    choice_hash TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX negative_examples_rule
ON negative_examples(tenant_id, user_id, repository_id, rule_id);
PRAGMA user_version = 1;
"""


class PreferenceLearningError(ValueError):
    """A preference decision violates storage, scope, or audit invariants."""


class PreferenceScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tenant_id: Identifier
    user_id: Identifier
    repository_id: Identifier

    @classmethod
    def local(cls) -> PreferenceScope:
        return cls(tenant_id="local", user_id="local", repository_id="local")


class PreferenceActor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    actor_id: Identifier
    authentication: Literal["local-os", "oidc", "service-token"]

    @classmethod
    def local(cls) -> PreferenceActor:
        uid = os.getuid() if hasattr(os, "getuid") else 0
        username = "".join(
            character
            for character in getpass.getuser().lower()
            if character.isascii() and character.isalnum()
        )
        return cls(actor_id=f"os-{username or 'user'}-{uid}", authentication="local-os")


class PreferenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    row_id: int = Field(gt=0)
    scope: PreferenceScope
    decision_id: Identifier
    choice: NonEmptyText = Field(max_length=512)
    context: dict[str, str | int | float | bool | None]
    context_hash: Sha256
    artifact_hash: Sha256 | None = None
    actor: PreferenceActor
    payload_hash: Sha256
    created_at: AwareDatetime


class PreferenceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().absolute()
        ensure_private_home(self.path.parent)
        identity = self._prepare_path()
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA journal_mode=DELETE")
            self._connection.execute("PRAGMA synchronous=FULL")
            opened = os.lstat(self.path)
            if (opened.st_dev, opened.st_ino) != identity:
                raise PreferenceLearningError("preference database changed while opening")
            self._initialize_schema()
        except PreferenceLearningError:
            if hasattr(self, "_connection"):
                self._connection.close()
            raise
        except sqlite3.Error as exc:
            if hasattr(self, "_connection"):
                self._connection.close()
            raise PreferenceLearningError("preference database could not be opened") from exc

    def record(
        self,
        scope: PreferenceScope,
        actor: PreferenceActor,
        decision_id: str,
        choice: str,
        context: dict[str, Any],
        artifact_hash: str | None,
    ) -> PreferenceRecord:
        normalized_context = validate_context(context)
        try:
            validated_id = _IDENTIFIER_ADAPTER.validate_python(decision_id)
        except ValidationError as exc:
            raise PreferenceLearningError("decision ID is invalid") from exc
        normalized_choice = choice.strip() if isinstance(choice, str) else ""
        if not normalized_choice or len(normalized_choice) > 512:
            raise PreferenceLearningError("choice must be a bounded non-empty string")
        if artifact_hash is not None and not _is_sha256(artifact_hash):
            raise PreferenceLearningError("artifact hash must be a lowercase SHA-256 digest")
        context_json = _canonical_text(normalized_context)
        context_hash = hashlib.sha256(context_json.encode()).hexdigest()
        actor_json = actor.model_dump_json()
        payload_hash = hashlib.sha256(
            _canonical_bytes(
                {
                    "choice": normalized_choice,
                    "context": normalized_context,
                    "artifact_hash": artifact_hash,
                    "actor": actor.model_dump(mode="json"),
                }
            )
        ).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        key = _scope_key(scope)
        with self._transaction():
            revoked = self._connection.execute(
                "SELECT 1 FROM revocations WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "AND decision_id=?",
                (*key, validated_id),
            ).fetchone()
            if revoked is not None:
                raise PreferenceLearningError("revoked decision ID cannot be reused")
            existing = self._connection.execute(
                "SELECT * FROM decisions WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "AND decision_id=?",
                (*key, validated_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise PreferenceLearningError("decision replay has a different payload")
                return _record(existing)
            cursor = self._connection.execute(
                "INSERT INTO decisions(tenant_id,user_id,repository_id,decision_id,choice,"
                "context_json,context_hash,artifact_hash,actor_json,payload_hash,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    *key,
                    validated_id,
                    normalized_choice,
                    context_json,
                    context_hash,
                    artifact_hash,
                    actor_json,
                    payload_hash,
                    now,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM decisions WHERE row_id=?", (cursor.lastrowid,)
            ).fetchone()
        assert row is not None
        return _record(row)

    def decision(self, scope: PreferenceScope, decision_id: str) -> PreferenceRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM decisions WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "AND decision_id=?",
                (*_scope_key(scope), decision_id),
            ).fetchone()
        return None if row is None else _record(row)

    def decisions(self, scope: PreferenceScope) -> list[PreferenceRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM decisions WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "ORDER BY row_id",
                _scope_key(scope),
            ).fetchall()
        return [_record(row) for row in rows]

    def revoke(
        self,
        scope: PreferenceScope,
        actor: PreferenceActor,
        decision_id: str,
        reason: str,
    ) -> None:
        normalized_reason = _reason(reason)
        key = _scope_key(scope)
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM decisions WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "AND decision_id=?",
                (*key, decision_id),
            ).fetchone()
            if row is None:
                if self._connection.execute(
                    "SELECT 1 FROM revocations WHERE tenant_id=? AND user_id=? AND repository_id=? "
                    "AND decision_id=?",
                    (*key, decision_id),
                ).fetchone():
                    return
                raise PreferenceLearningError("decision does not exist")
            self._connection.execute(
                "INSERT INTO revocations VALUES (?,?,?,?,?,?,?,?)",
                (
                    *key,
                    decision_id,
                    row["payload_hash"],
                    actor.model_dump_json(),
                    normalized_reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._connection.execute("DELETE FROM decisions WHERE row_id=?", (row["row_id"],))

    def promote(
        self,
        scope: PreferenceScope,
        actor: PreferenceActor,
        rule_id: str,
        severity: Literal["warn", "block"],
        reason: str,
    ) -> None:
        normalized_reason = _reason(reason)
        if severity not in {"warn", "block"}:
            raise PreferenceLearningError("promotion severity must be warn or block")
        with self._transaction():
            self._connection.execute(
                "INSERT INTO promotions(tenant_id,user_id,repository_id,rule_id,severity,"
                "actor_json,reason,promoted_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(tenant_id,user_id,repository_id,rule_id) DO UPDATE SET "
                "severity=excluded.severity,actor_json=excluded.actor_json,reason=excluded.reason,"
                "promoted_at=excluded.promoted_at",
                (
                    *_scope_key(scope),
                    rule_id,
                    severity,
                    actor.model_dump_json(),
                    normalized_reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def promotions(self, scope: PreferenceScope) -> dict[str, str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT rule_id,severity FROM promotions WHERE tenant_id=? AND user_id=? "
                "AND repository_id=? ORDER BY rule_id",
                _scope_key(scope),
            ).fetchall()
        return {str(row["rule_id"]): str(row["severity"]) for row in rows}

    def delete_candidate(
        self,
        scope: PreferenceScope,
        actor: PreferenceActor,
        *,
        rule_id: str,
        context_hash: str,
        choice: str,
        reason: str,
    ) -> None:
        normalized_reason = _reason(reason)
        key = _scope_key(scope)
        with self._transaction():
            rows = self._connection.execute(
                "SELECT decision_id,payload_hash FROM decisions WHERE tenant_id=? AND user_id=? "
                "AND repository_id=? AND context_hash=?",
                (*key, context_hash),
            ).fetchall()
            revoked_at = datetime.now(timezone.utc).isoformat()
            for row in rows:
                self._connection.execute(
                    "INSERT OR IGNORE INTO revocations VALUES (?,?,?,?,?,?,?,?)",
                    (
                        *key,
                        row["decision_id"],
                        row["payload_hash"],
                        actor.model_dump_json(),
                        normalized_reason,
                        revoked_at,
                    ),
                )
            self._connection.execute(
                "DELETE FROM decisions WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "AND context_hash=?",
                (*key, context_hash),
            )
            self._connection.execute(
                "DELETE FROM promotions WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "AND rule_id=?",
                (*key, rule_id),
            )
            self._connection.execute(
                "INSERT INTO negative_examples(tenant_id,user_id,repository_id,rule_id,"
                "context_hash,choice_hash,actor_json,reason,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    *key,
                    rule_id,
                    context_hash,
                    hashlib.sha256(choice.encode()).hexdigest(),
                    actor.model_dump_json(),
                    normalized_reason,
                    revoked_at,
                ),
            )

    def negative_count(self, scope: PreferenceScope, rule_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM negative_examples WHERE tenant_id=? AND user_id=? "
                "AND repository_id=? AND rule_id=?",
                (*_scope_key(scope), rule_id),
            ).fetchone()
        return 0 if row is None else int(row["count"])

    def negative_counts(self, scope: PreferenceScope) -> dict[tuple[str, str], int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT context_hash,choice_hash,COUNT(*) AS count FROM negative_examples "
                "WHERE tenant_id=? AND user_id=? AND repository_id=? "
                "GROUP BY context_hash,choice_hash",
                _scope_key(scope),
            ).fetchall()
        return {
            (str(row["context_hash"]), str(row["choice_hash"])): int(row["count"]) for row in rows
        }

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def _prepare_path(self) -> tuple[int, int]:
        if self.path.is_symlink():
            raise PreferenceLearningError("preference database cannot be a symlink")
        try:
            status = os.lstat(self.path)
        except FileNotFoundError:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except OSError as exc:
                raise PreferenceLearningError("preference database could not be created") from exc
            os.close(descriptor)
            status = os.lstat(self.path)
        if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
            raise PreferenceLearningError("preference database path is unsafe")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise PreferenceLearningError("preference database must be owner-only")
        return status.st_dev, status.st_ino

    def _initialize_schema(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if version == 0 and not tables:
            self._connection.executescript(_SCHEMA)
            self._connection.commit()
            return
        if version != _SCHEMA_VERSION or tables != {
            "decisions",
            "revocations",
            "promotions",
            "negative_examples",
        }:
            raise PreferenceLearningError("preference database schema is incompatible")
        for table, expected in _EXPECTED_COLUMNS.items():
            columns = {
                str(row[1])
                for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if columns != expected:
                raise PreferenceLearningError("preference database schema is incompatible")

    def _transaction(self):
        return _Transaction(self)


class _Transaction:
    def __init__(self, store: PreferenceStore) -> None:
        self.store = store

    def __enter__(self) -> None:
        self.store._lock.acquire()
        try:
            self.store._connection.execute("BEGIN IMMEDIATE")
        except Exception:
            self.store._lock.release()
            raise

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        try:
            if exc_type is None:
                self.store._connection.commit()
            else:
                self.store._connection.rollback()
        finally:
            self.store._lock.release()


def validate_context(value: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    if not isinstance(value, dict) or len(value) > 64:
        raise PreferenceLearningError("context must be a bounded object of flat scalar dimensions")
    result: dict[str, str | int | float | bool | None] = {}
    for key, item in value.items():
        try:
            normalized_key = _IDENTIFIER_ADAPTER.validate_python(key)
        except ValidationError as exc:
            raise PreferenceLearningError("context dimension name is invalid") from exc
        if isinstance(item, str):
            if not item.strip() or len(item) > 512:
                raise PreferenceLearningError("context string dimensions must be bounded")
            result[normalized_key] = item.strip()
        elif item is None or isinstance(item, (bool, int)):
            result[normalized_key] = item
        elif isinstance(item, float) and math.isfinite(item):
            result[normalized_key] = item
        else:
            raise PreferenceLearningError("context must contain only flat scalar dimensions")
    if len(_canonical_bytes(result)) > 32 * 1024:
        raise PreferenceLearningError("context exceeds 32 KiB")
    return result


def _record(row: sqlite3.Row) -> PreferenceRecord:
    return PreferenceRecord(
        row_id=row["row_id"],
        scope=PreferenceScope(
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            repository_id=row["repository_id"],
        ),
        decision_id=row["decision_id"],
        choice=row["choice"],
        context=json.loads(row["context_json"]),
        context_hash=row["context_hash"],
        artifact_hash=row["artifact_hash"],
        actor=PreferenceActor.model_validate_json(row["actor_json"]),
        payload_hash=row["payload_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _scope_key(scope: PreferenceScope) -> tuple[str, str, str]:
    return scope.tenant_id, scope.user_id, scope.repository_id


def _reason(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > 4_096:
        raise PreferenceLearningError("audit reason must be a bounded non-empty string")
    return normalized


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical_text(value: Any) -> str:
    return _canonical_bytes(value).decode()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
