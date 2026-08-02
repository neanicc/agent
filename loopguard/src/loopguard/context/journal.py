from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
from datetime import datetime
from pathlib import Path
from types import TracebackType

from loopguard.control.dispatch import HandlerDelivery
from loopguard.control.events import EventKind

from .hashing import content_fingerprint
from .models import ChangeObservation, ChangeRecord, ContextCheckpoint


_SCHEMA = """
CREATE TABLE change_records (
    record_id TEXT PRIMARY KEY,
    content_fingerprint TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    repo_seq INTEGER NOT NULL CHECK(repo_seq >= 1),
    worktree_id TEXT NOT NULL,
    path TEXT NOT NULL,
    actor TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    patch TEXT,
    symbols_json TEXT NOT NULL,
    reconciliation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE change_observations (
    observation_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    control_event_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    repo_seq INTEGER NOT NULL CHECK(repo_seq >= 1),
    actor TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    FOREIGN KEY(record_id) REFERENCES change_records(record_id) ON DELETE CASCADE
);
CREATE TABLE change_verifications (
    record_id TEXT NOT NULL,
    verification_id TEXT NOT NULL,
    PRIMARY KEY(record_id, verification_id),
    FOREIGN KEY(record_id) REFERENCES change_records(record_id) ON DELETE CASCADE
);
CREATE TABLE context_checkpoints (
    repo_id TEXT NOT NULL,
    repo_seq INTEGER NOT NULL CHECK(repo_seq >= 0),
    checkpoint_json TEXT NOT NULL,
    PRIMARY KEY(repo_id, repo_seq)
);
CREATE INDEX change_records_repo_cursor ON change_records(repo_id, repo_seq);
CREATE INDEX change_records_repo_path ON change_records(repo_id, path);
CREATE INDEX change_records_fingerprint ON change_records(content_fingerprint);
CREATE INDEX change_records_reconciliation
ON change_records(reconciliation_id, content_fingerprint);
CREATE INDEX change_observations_control_event
ON change_observations(control_event_id);
CREATE INDEX change_observations_actor ON change_observations(actor);
CREATE INDEX change_observations_record_cursor
ON change_observations(record_id, repo_seq, observation_id);
CREATE INDEX change_verifications_id ON change_verifications(verification_id);
PRAGMA user_version = 1;
"""


class ChangeJournalError(RuntimeError):
    """The durable change journal could not preserve its cursor contract."""


class ObservationConflictError(ChangeJournalError):
    """An observation ID was reused for different immutable semantics."""


class ChangeJournal:
    def __init__(
        self,
        path: Path,
        *,
        reconciliation_window_seconds: float = 5.0,
    ) -> None:
        if not 0 < reconciliation_window_seconds <= 300:
            raise ValueError("reconciliation window must be between zero and 300 seconds")
        self.reconciliation_window_seconds = reconciliation_window_seconds
        self.path = path.expanduser().absolute()
        sidecars = (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
        if any(candidate.is_symlink() for candidate in sidecars):
            raise ValueError("change journal database cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.resolve(strict=True) != self.path.parent:
            raise ValueError("change journal parent cannot contain a symlink")
        if self.path.exists():
            status = os.lstat(self.path)
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("change journal database must be a regular file")
            if os.name == "posix" and (
                status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
            ):
                raise ValueError("change journal database must be owner-only")
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
        self._connection.execute("PRAGMA foreign_keys = ON")
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
                raise ChangeJournalError("unversioned change journal is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            self._connection.close()
            raise ChangeJournalError("change journal schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._validate_schema()
        self._secure_files()

    def record(self, observation: ChangeObservation) -> ChangeRecord:
        observation = ChangeObservation.model_validate(observation)
        encoded = observation.model_dump_json()
        fingerprint = _content_fingerprint(observation)
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT record_id, observation_json FROM change_observations "
                    "WHERE observation_id = ?",
                    (observation.observation_id,),
                ).fetchone()
                if existing is not None:
                    if existing["observation_json"] != encoded:
                        raise ObservationConflictError(
                            "observation ID was reused with different semantics"
                        )
                    record_id = str(existing["record_id"])
                    connection.execute("COMMIT")
                    return self._get(record_id)
                record_id = self._merge_target(observation, fingerprint)
                if record_id is None:
                    record_id = _record_id(observation, fingerprint)
                    connection.execute(
                        """
                        INSERT INTO change_records(
                            record_id, content_fingerprint, repo_id, repo_seq,
                            worktree_id, path, actor, before_hash, after_hash, patch,
                            symbols_json, reconciliation_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record_id,
                            fingerprint,
                            observation.repo_id,
                            observation.repo_seq,
                            observation.worktree_id,
                            observation.path,
                            observation.actor,
                            observation.before_hash,
                            observation.after_hash,
                            observation.patch,
                            json.dumps(observation.symbols, separators=(",", ":")),
                            observation.reconciliation_id,
                            observation.observed_at.isoformat(),
                            observation.observed_at.isoformat(),
                        ),
                    )
                else:
                    aggregate = self._merge_projection(record_id, observation)
                    connection.execute(
                        """
                        UPDATE change_records
                        SET repo_seq = MAX(repo_seq, ?), updated_at = ?, patch = ?,
                            symbols_json = ?
                        WHERE record_id = ?
                        """,
                        (
                            observation.repo_seq,
                            observation.observed_at.isoformat(),
                            aggregate["patch"],
                            json.dumps(aggregate["symbols"], separators=(",", ":")),
                            record_id,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO change_observations(
                        observation_id, record_id, control_event_id, repo_id,
                        repo_seq, actor, observation_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation.observation_id,
                        record_id,
                        observation.control_event_id,
                        observation.repo_id,
                        observation.repo_seq,
                        observation.actor,
                        encoded,
                    ),
                )
                for verification_id in observation.verification_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO change_verifications(record_id, verification_id) "
                        "VALUES (?, ?)",
                        (record_id, verification_id),
                    )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
            return self._get(record_id)

    def since(
        self,
        repo_id: str,
        *,
        repo_seq: int,
        limit: int = 1000,
    ) -> list[ChangeRecord]:
        if not repo_id.strip() or repo_seq < 0 or not 1 <= limit <= 10_000:
            raise ValueError("invalid journal cursor query")
        with self._lock:
            rows = self._require_open().execute(
                """
                SELECT record_id FROM change_records
                WHERE repo_id = ? AND repo_seq > ?
                ORDER BY repo_seq, record_id
                LIMIT ?
                """,
                (repo_id, repo_seq, limit),
            ).fetchall()
            return [self._get(str(row["record_id"])) for row in rows]

    def latest_cursor(self, repo_id: str) -> int:
        if not repo_id.strip():
            raise ValueError("repository ID must not be empty")
        with self._lock:
            row = self._require_open().execute(
                "SELECT COALESCE(MAX(repo_seq), 0) AS cursor "
                "FROM change_observations WHERE repo_id = ?",
                (repo_id,),
            ).fetchone()
            return int(row["cursor"])

    def count(self, repo_id: str | None = None) -> int:
        with self._lock:
            if repo_id is None:
                row = self._require_open().execute(
                    "SELECT COUNT(*) AS count FROM change_records"
                ).fetchone()
            else:
                row = self._require_open().execute(
                    "SELECT COUNT(*) AS count FROM change_records WHERE repo_id = ?",
                    (repo_id,),
                ).fetchone()
            return int(row["count"])

    def save_checkpoint(self, checkpoint: ContextCheckpoint) -> ContextCheckpoint:
        checkpoint = ContextCheckpoint.model_validate(checkpoint)
        encoded = checkpoint.model_dump_json()
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT checkpoint_json FROM context_checkpoints "
                    "WHERE repo_id = ? AND repo_seq = ?",
                    (checkpoint.repo_id, checkpoint.repo_seq),
                ).fetchone()
                if existing is not None:
                    if existing["checkpoint_json"] != encoded:
                        raise ChangeJournalError(
                            "checkpoint cursor was reused with different semantics"
                        )
                else:
                    connection.execute(
                        "INSERT INTO context_checkpoints(repo_id, repo_seq, checkpoint_json) "
                        "VALUES (?, ?, ?)",
                        (checkpoint.repo_id, checkpoint.repo_seq, encoded),
                    )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
            return checkpoint

    def latest_checkpoint(self, repo_id: str) -> ContextCheckpoint | None:
        if not repo_id.strip():
            raise ValueError("repository ID must not be empty")
        with self._lock:
            row = self._require_open().execute(
                "SELECT checkpoint_json FROM context_checkpoints "
                "WHERE repo_id = ? ORDER BY repo_seq DESC LIMIT 1",
                (repo_id,),
            ).fetchone()
            return (
                ContextCheckpoint.model_validate_json(row["checkpoint_json"])
                if row is not None
                else None
            )

    async def handle_delivery(self, delivery: HandlerDelivery) -> None:
        event = delivery.event
        if event.kind is not EventKind.FILE_CHANGED:
            return
        payload = event.payload
        path = payload.get("path")
        if not isinstance(path, str):
            raise ValueError("file-changed event is missing a path")
        worktree_id = event.session.worktree_id
        if not worktree_id:
            raise ValueError("file-changed event is missing a worktree identity")
        agent = payload.get("agent")
        actor = payload.get("actor")
        if not isinstance(actor, str):
            actor = f"{agent if isinstance(agent, str) else event.source}:{event.session.session_id}"
        self.record(
            ChangeObservation(
                observation_id=f"control:{event.event_id}",
                control_event_id=event.event_id,
                repo_id=event.session.repo_id,
                repo_seq=delivery.repo_seq,
                worktree_id=worktree_id,
                path=path,
                actor=actor,
                before_hash=_optional_string(payload.get("before_hash")),
                after_hash=_optional_string(payload.get("after_hash")),
                patch=_optional_string(payload.get("patch")),
                symbols=_string_list(payload.get("symbols")),
                verification_ids=_string_list(payload.get("verification_ids")),
                reconciliation_id=_optional_string(payload.get("reconciliation_id")),
                observed_at=event.created_at,
            )
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            self._secure_files()

    def __enter__(self) -> ChangeJournal:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _merge_target(
        self,
        observation: ChangeObservation,
        fingerprint: str,
    ) -> str | None:
        if observation.reconciliation_id is None:
            return None
        row = self._require_open().execute(
            """
            SELECT record_id, updated_at FROM change_records
            WHERE reconciliation_id = ? AND content_fingerprint = ?
              AND repo_id = ? AND worktree_id = ? AND path = ?
            ORDER BY repo_seq DESC LIMIT 1
            """,
            (
                observation.reconciliation_id,
                fingerprint,
                observation.repo_id,
                observation.worktree_id,
                observation.path,
            ),
        ).fetchone()
        if row is None:
            return None
        try:
            updated_at = datetime.fromisoformat(str(row["updated_at"]))
        except ValueError as exc:
            raise ChangeJournalError("change record timestamp is invalid") from exc
        age = abs((observation.observed_at - updated_at).total_seconds())
        return (
            str(row["record_id"])
            if age <= self.reconciliation_window_seconds
            else None
        )

    def _merge_projection(
        self,
        record_id: str,
        observation: ChangeObservation,
    ) -> dict[str, object]:
        connection = self._require_open()
        row = connection.execute(
            "SELECT patch, symbols_json FROM change_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise ChangeJournalError("merge target is missing")
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM change_observations WHERE record_id = ?",
                (record_id,),
            ).fetchone()[0]
        )
        if count >= 10_000:
            raise ChangeJournalError("reconciliation provenance limit exceeded")
        symbols = sorted(set(json.loads(row["symbols_json"])) | set(observation.symbols))
        if len(symbols) > 4096:
            raise ChangeJournalError("reconciliation symbol limit exceeded")
        existing_verifications = {
            str(item[0])
            for item in connection.execute(
                "SELECT verification_id FROM change_verifications WHERE record_id = ?",
                (record_id,),
            ).fetchall()
        }
        if len(existing_verifications | set(observation.verification_ids)) > 4096:
            raise ChangeJournalError("reconciliation verification limit exceeded")
        return {
            "patch": row["patch"] if row["patch"] is not None else observation.patch,
            "symbols": symbols,
        }

    def _get(self, record_id: str) -> ChangeRecord:
        connection = self._require_open()
        row = connection.execute(
            "SELECT * FROM change_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise ChangeJournalError("change record is missing")
        observation_rows = connection.execute(
            """
            SELECT observation_json FROM change_observations
            WHERE record_id = ? ORDER BY repo_seq, observation_id
            """,
            (record_id,),
        ).fetchall()
        verification_rows = connection.execute(
            "SELECT verification_id FROM change_verifications "
            "WHERE record_id = ? ORDER BY verification_id",
            (record_id,),
        ).fetchall()
        return ChangeRecord(
            record_id=row["record_id"],
            content_fingerprint=row["content_fingerprint"],
            repo_id=row["repo_id"],
            repo_seq=row["repo_seq"],
            worktree_id=row["worktree_id"],
            path=row["path"],
            actor=row["actor"],
            before_hash=row["before_hash"],
            after_hash=row["after_hash"],
            patch=row["patch"],
            symbols=json.loads(row["symbols_json"]),
            verification_ids=[str(item["verification_id"]) for item in verification_rows],
            provenance=[
                ChangeObservation.model_validate_json(item["observation_json"])
                for item in observation_rows
            ],
        )

    def _validate_schema(self) -> None:
        required = {
            "change_records",
            "change_observations",
            "change_verifications",
            "context_checkpoints",
        }
        rows = self._connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        ).fetchall()
        if not required.issubset({str(row["name"]) for row in rows}):
            self._connection.close()
            raise ChangeJournalError("change journal schema is incomplete")
        quick_check = self._connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            self._connection.close()
            raise ChangeJournalError("change journal integrity check failed")

    def _require_open(self) -> sqlite3.Connection:
        if self._closed:
            raise ChangeJournalError("change journal is closed")
        return self._connection

    def _secure_files(self) -> None:
        if os.name != "posix":
            return
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.is_symlink():
                raise ValueError("change journal state cannot be a symlink")
            if path.exists():
                path.chmod(0o600)


def _content_fingerprint(observation: ChangeObservation) -> str:
    return content_fingerprint(
        observation.repo_id,
        observation.worktree_id,
        observation.path,
        observation.before_hash,
        observation.after_hash,
    )


def _record_id(observation: ChangeObservation, fingerprint: str) -> str:
    body = (
        f"loopguard-change-record-v1\0{observation.observation_id}\0"
        f"{observation.repo_seq}\0{fingerprint}"
    )
    return "change:" + hashlib.sha256(body.encode()).hexdigest()


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("change metadata must be a string list")
    return value
