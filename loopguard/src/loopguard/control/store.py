from __future__ import annotations

import json
import os
import sqlite3
import stat
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TypeVar

from pydantic import ValidationError

from .crypto import (
    CorruptKeyError,
    IntegrityError,
    KeyStore,
    MissingKeyError,
    PlatformKeyStore,
    decrypt,
    derive_data_key,
    encrypt,
    event_id_token,
    generate_data_key,
    key_id_for,
)
from .events import ControlEvent
from .migrations import MigrationError, migrate


class EventStoreError(Exception):
    """Base class for durable local event-store failures."""


class StoreClosedError(EventStoreError):
    """An operation was attempted after the store was closed."""


class StoreBusyError(EventStoreError):
    """SQLite remained locked after the bounded retry budget was exhausted."""


class DatabaseCorruptionError(EventStoreError):
    """SQLite state failed structural or quick integrity verification."""


class UnsafePermissionsError(EventStoreError):
    """Existing local state is accessible to users other than its owner."""


class WrongKeyError(EventStoreError):
    """Supplied key material does not match the existing event store."""


@dataclass(frozen=True, slots=True)
class StoredPosition:
    local_log_seq: int
    repo_seq: int
    session_seq: int


@dataclass(frozen=True, slots=True)
class StoredEvent:
    event: ControlEvent
    position: StoredPosition

    @property
    def local_log_seq(self) -> int:
        return self.position.local_log_seq

    @property
    def repo_seq(self) -> int:
        return self.position.repo_seq

    @property
    def session_seq(self) -> int:
        return self.position.session_seq


_Result = TypeVar("_Result")


class EventStore:
    def __init__(
        self,
        path: str | Path,
        *,
        key: bytes | None = None,
        key_store: KeyStore | None = None,
        busy_timeout_ms: int = 5_000,
        max_busy_retries: int = 3,
        retry_delay_seconds: float = 0.01,
    ) -> None:
        if key is not None and key_store is not None:
            raise ValueError("provide either injected key material or a key store, not both")
        if busy_timeout_ms < 0 or max_busy_retries < 0 or retry_delay_seconds < 0:
            raise ValueError("SQLite retry settings must not be negative")

        self.path = Path(path)
        self._max_busy_retries = max_busy_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._lock = threading.RLock()
        self._closed = False
        self._connection: sqlite3.Connection | None = None
        self._data_key = b""
        self._key_id = ""
        if key is None and key_store is None:
            key_store = PlatformKeyStore()

        is_new = _prepare_storage_path(self.path)
        _validate_existing_sidecars(self.path)
        _validate_wal_structure(self.path)
        try:
            self._connection = sqlite3.connect(
                self.path,
                timeout=busy_timeout_ms / 1_000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            self._with_busy_retry(self._quick_check)
            self._with_busy_retry(lambda: migrate(self._require_connection()))
            self._with_busy_retry(
                lambda: self._require_connection().execute("PRAGMA journal_mode = WAL")
            )
            self._connection.execute("PRAGMA synchronous = FULL")
            self._load_or_create_key(
                is_new=is_new,
                injected_material=key,
                key_store=key_store,
            )
            self._with_busy_retry(self._verify_all_rows)
            self._secure_database_files()
        except (MissingKeyError, CorruptKeyError, WrongKeyError, IntegrityError):
            self._close_after_failed_startup()
            raise
        except MigrationError:
            self._close_after_failed_startup()
            raise
        except sqlite3.DatabaseError as exc:
            self._close_after_failed_startup()
            if _is_corruption_error(exc):
                raise DatabaseCorruptionError("SQLite database integrity verification failed") from exc
            raise
        except Exception:
            self._close_after_failed_startup()
            raise

    @classmethod
    def open(
        cls,
        *,
        home: str | Path | None = None,
        key_store: KeyStore | None = None,
    ) -> EventStore:
        resolved_home = Path(
            home or os.environ.get("LOOPGUARD_HOME") or Path.home() / ".loopguard"
        )
        return cls(resolved_home / "events.db", key_store=key_store)

    @classmethod
    def for_test(
        cls,
        path: str | Path,
        *,
        key: bytes | None = None,
        key_store: KeyStore | None = None,
        busy_timeout_ms: int = 5_000,
        max_busy_retries: int = 3,
        retry_delay_seconds: float = 0.01,
    ) -> EventStore:
        if key is None and key_store is None:
            key = b"loopguard-test-key"
        return cls(
            path,
            key=key,
            key_store=key_store,
            busy_timeout_ms=busy_timeout_ms,
            max_busy_retries=max_busy_retries,
            retry_delay_seconds=retry_delay_seconds,
        )

    def append(self, event: ControlEvent) -> StoredPosition:
        event = ControlEvent.model_validate(event)

        def operation() -> StoredPosition:
            connection = self._require_connection()
            token = event_id_token(self._data_key, event.event_id)
            connection.execute("BEGIN IMMEDIATE")
            try:
                duplicate = connection.execute(
                    """
                    SELECT local_log_seq, repo_seq, session_seq
                    FROM events
                    WHERE event_id = ?
                    """,
                    (token,),
                ).fetchone()
                if duplicate is not None:
                    position = _position_from_row(duplicate)
                    connection.execute("COMMIT")
                    return position

                repo_seq = self._next_sequence("repo_sequences", "repo_id", event.session.repo_id)
                session_seq = self._next_sequence(
                    "session_sequences", "session_id", event.session.session_id
                )
                created_at = event.created_at.isoformat()
                cursor = connection.execute(
                    """
                    INSERT INTO events(
                        event_id, repo_id, session_id, repo_seq, session_seq,
                        schema_version, key_id, nonce, ciphertext, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token,
                        event.session.repo_id,
                        event.session.session_id,
                        repo_seq,
                        session_seq,
                        event.schema_version,
                        self._key_id,
                        b"",
                        b"",
                        created_at,
                    ),
                )
                local_log_seq = int(cursor.lastrowid)
                row_metadata = _row_metadata(
                    local_log_seq=local_log_seq,
                    event_id=token,
                    repo_id=event.session.repo_id,
                    session_id=event.session.session_id,
                    repo_seq=repo_seq,
                    session_seq=session_seq,
                    schema_version=event.schema_version,
                    key_id=self._key_id,
                    created_at=created_at,
                )
                nonce, ciphertext = encrypt(
                    self._data_key,
                    _serialize_event(event),
                    _aad(row_metadata),
                )
                connection.execute(
                    "UPDATE events SET nonce = ?, ciphertext = ? WHERE local_log_seq = ?",
                    (nonce, ciphertext, local_log_seq),
                )
                connection.execute("COMMIT")
                return StoredPosition(local_log_seq, repo_seq, session_seq)
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

        with self._lock:
            self._require_open()
            position = self._with_busy_retry(operation)
            self._secure_database_files()
            return position

    def read_local_after(self, local_log_seq: int, limit: int) -> list[StoredEvent]:
        return self._read_after(
            "local_log_seq > ?",
            (local_log_seq,),
            "local_log_seq",
            limit,
        )

    def read_repo_after(self, repo_id: str, repo_seq: int, limit: int) -> list[StoredEvent]:
        return self._read_after(
            "repo_id = ? AND repo_seq > ?",
            (repo_id, repo_seq),
            "repo_seq",
            limit,
        )

    def read_session_after(
        self, session_id: str, session_seq: int, limit: int
    ) -> list[StoredEvent]:
        return self._read_after(
            "session_id = ? AND session_seq > ?",
            (session_id, session_seq),
            "session_seq",
            limit,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            connection = self._connection
            self._connection = None
            self._closed = True
            if connection is not None:
                connection.close()
            self._secure_database_files()

    def __enter__(self) -> EventStore:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _quick_check(self) -> None:
        connection = self._require_connection()
        rows = connection.execute("PRAGMA quick_check").fetchall()
        if len(rows) != 1 or rows[0][0] != "ok":
            detail = "; ".join(str(row[0]) for row in rows[:3])
            raise DatabaseCorruptionError(f"SQLite quick_check failed: {detail}")

    def _load_or_create_key(
        self,
        *,
        is_new: bool,
        injected_material: bytes | None,
        key_store: KeyStore | None,
    ) -> None:
        connection = self._require_connection()
        row = self._with_busy_retry(
            lambda: connection.execute(
                "SELECT key_id FROM store_metadata WHERE singleton = 1"
            ).fetchone()
        )
        if row is None:
            if not is_new:
                raise MissingKeyError("existing event store has no key metadata")
            if injected_material is not None:
                data_key = derive_data_key(injected_material)
            else:
                assert key_store is not None
                data_key = generate_data_key()
                key_store.put(key_id_for(data_key), data_key)
            key_id = key_id_for(data_key)

            def insert_metadata() -> None:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "INSERT INTO store_metadata(singleton, key_id) VALUES (1, ?)",
                        (key_id,),
                    )
                    connection.execute("COMMIT")
                except Exception:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise

            self._with_busy_retry(insert_metadata)
            self._data_key = data_key
            self._key_id = key_id
            return

        expected_key_id = str(row["key_id"])
        if injected_material is not None:
            data_key = derive_data_key(injected_material)
        else:
            assert key_store is not None
            data_key = key_store.get(expected_key_id)
        if key_id_for(data_key) != expected_key_id:
            raise WrongKeyError("supplied key does not match the existing event store")
        self._data_key = data_key
        self._key_id = expected_key_id

    def _verify_all_rows(self) -> None:
        connection = self._require_connection()
        connection.execute("BEGIN")
        try:
            rows = connection.execute(
                """
                SELECT local_log_seq, event_id, repo_id, session_id, repo_seq, session_seq,
                       schema_version, key_id, nonce, ciphertext, created_at
                FROM events
                ORDER BY local_log_seq
                """
            ).fetchall()
            for row in rows:
                self._stored_event(row)
            self._verify_sequence_state()
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _verify_sequence_state(self) -> None:
        connection = self._require_connection()
        checks = (
            ("repo_sequences", "repo_id", "repo_id", "repo_seq"),
            ("session_sequences", "session_id", "session_id", "session_seq"),
        )
        for sequence_table, sequence_key, event_key, event_sequence in checks:
            allocated = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    f"SELECT {sequence_key}, last_seq FROM {sequence_table}"
                )
            }
            observed: dict[str, int] = {}
            for row in connection.execute(
                f"""
                SELECT {event_key}, MIN({event_sequence}), MAX({event_sequence}), COUNT(*)
                FROM events
                GROUP BY {event_key}
                """
            ):
                minimum, maximum, count = (int(row[1]), int(row[2]), int(row[3]))
                if minimum != 1 or count != maximum:
                    raise IntegrityError("cursor sequence metadata contains a replay gap")
                observed[str(row[0])] = maximum
            if allocated != observed:
                raise IntegrityError("cursor sequence metadata does not match persisted events")

        local_state = connection.execute(
            "SELECT COALESCE(MAX(local_log_seq), 0), COUNT(*) FROM events"
        ).fetchone()
        local_maximum, local_count = int(local_state[0]), int(local_state[1])
        if local_count != local_maximum:
            raise IntegrityError("cursor sequence metadata contains a replay gap")
        sqlite_sequence = connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'events'"
        ).fetchone()
        allocated_local = int(sqlite_sequence[0]) if sqlite_sequence is not None else 0
        if allocated_local != local_maximum:
            raise IntegrityError("cursor sequence metadata does not match persisted events")

    def _next_sequence(self, table: str, key_column: str, key: str) -> int:
        row = self._require_connection().execute(
            f"""
            INSERT INTO {table}({key_column}, last_seq) VALUES (?, 1)
            ON CONFLICT({key_column}) DO UPDATE SET last_seq = last_seq + 1
            RETURNING last_seq
            """,
            (key,),
        ).fetchone()
        return int(row[0])

    def _read_after(
        self,
        where_clause: str,
        parameters: tuple[object, ...],
        order_column: str,
        limit: int,
    ) -> list[StoredEvent]:
        if limit <= 0:
            return []
        with self._lock:
            connection = self._require_connection()
            rows = self._with_busy_retry(
                lambda: connection.execute(
                    f"""
                    SELECT local_log_seq, event_id, repo_id, session_id, repo_seq, session_seq,
                           schema_version, key_id, nonce, ciphertext, created_at
                    FROM events
                    WHERE {where_clause}
                    ORDER BY {order_column} ASC
                    LIMIT ?
                    """,
                    (*parameters, limit),
                ).fetchall()
            )
            return [self._stored_event(row) for row in rows]

    def _stored_event(self, row: sqlite3.Row) -> StoredEvent:
        row_metadata = _row_metadata(
            local_log_seq=row["local_log_seq"],
            event_id=row["event_id"],
            repo_id=row["repo_id"],
            session_id=row["session_id"],
            repo_seq=row["repo_seq"],
            session_seq=row["session_seq"],
            schema_version=row["schema_version"],
            key_id=row["key_id"],
            created_at=row["created_at"],
        )
        plaintext = decrypt(
            self._data_key,
            bytes(row["nonce"]),
            bytes(row["ciphertext"]),
            _aad(row_metadata),
        )
        try:
            event = ControlEvent.model_validate_json(plaintext)
        except (ValidationError, ValueError, UnicodeDecodeError) as exc:
            raise IntegrityError("encrypted event body is not a valid ControlEvent") from exc
        if (
            event_id_token(self._data_key, event.event_id) != row["event_id"]
            or event.session.repo_id != row["repo_id"]
            or event.session.session_id != row["session_id"]
            or event.schema_version != row["schema_version"]
            or event.created_at.isoformat() != row["created_at"]
            or row["key_id"] != self._key_id
        ):
            raise IntegrityError("encrypted event body does not match authenticated row metadata")
        return StoredEvent(event=event, position=_position_from_row(row))

    def _with_busy_retry(self, operation: Callable[[], _Result]) -> _Result:
        attempts = self._max_busy_retries + 1
        for attempt in range(attempts):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if not _is_busy_error(exc):
                    raise
                if attempt == attempts - 1:
                    raise StoreBusyError(
                        f"SQLite remained locked after {attempts} attempts"
                    ) from exc
                time.sleep(self._retry_delay_seconds)
        raise AssertionError("unreachable")

    def _secure_database_files(self) -> None:
        if os.name != "posix":
            return
        for candidate in _database_files(self.path):
            try:
                candidate.chmod(0o600)
            except FileNotFoundError:
                pass

    def _close_after_failed_startup(self) -> None:
        connection = self._connection
        self._connection = None
        self._closed = True
        if connection is not None:
            connection.close()

    def _require_connection(self) -> sqlite3.Connection:
        self._require_open()
        assert self._connection is not None
        return self._connection

    def _require_open(self) -> None:
        if self._closed:
            raise StoreClosedError("event store is closed")


def _prepare_storage_path(path: Path) -> bool:
    parent = path.parent
    if not parent.exists():
        parent.mkdir(mode=0o700, parents=True)
    if not parent.is_dir() or parent.is_symlink():
        raise UnsafePermissionsError(f"event-store directory is not a real directory: {parent}")
    if os.name == "posix" and stat.S_IMODE(parent.stat().st_mode) != 0o700:
        raise UnsafePermissionsError(f"event-store directory must have mode 0700: {parent}")

    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise UnsafePermissionsError(f"event-store database is not a regular file: {path}")
        if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise UnsafePermissionsError(f"event-store database must have mode 0600: {path}")
        return False

    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(descriptor)
    return True


def _database_files(path: Path) -> tuple[Path, Path, Path]:
    return (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )


def _validate_existing_sidecars(path: Path) -> None:
    if os.name != "posix":
        return
    for candidate in _database_files(path)[1:]:
        try:
            mode = stat.S_IMODE(candidate.stat().st_mode)
        except FileNotFoundError:
            continue
        if mode != 0o600:
            raise UnsafePermissionsError(
                f"event-store database sidecar must have mode 0600: {candidate}"
            )


def _validate_wal_structure(path: Path) -> None:
    wal_path = path.with_name(f"{path.name}-wal")
    for attempt in range(3):
        try:
            wal_bytes = wal_path.read_bytes()
        except FileNotFoundError:
            return
        if not wal_bytes:
            return
        try:
            _validate_wal_bytes(wal_bytes)
            return
        except DatabaseCorruptionError:
            if attempt == 2:
                raise
            time.sleep(0.01)


def _validate_wal_bytes(wal_bytes: bytes) -> None:
    header = wal_bytes[:32]
    if len(header) != 32:
        raise DatabaseCorruptionError("SQLite WAL header is truncated")
    magic, _, page_size = struct.unpack(">III", header[:12])
    if magic not in (0x377F0682, 0x377F0683):
        raise DatabaseCorruptionError("SQLite WAL header has an invalid magic value")
    if page_size == 1:
        page_size = 65_536
    if page_size < 512 or page_size > 65_536 or page_size & (page_size - 1):
        raise DatabaseCorruptionError("SQLite WAL declares an invalid page size")
    payload_size = len(wal_bytes) - 32
    if payload_size % (24 + page_size) != 0:
        raise DatabaseCorruptionError("SQLite WAL contains a truncated frame")

    byte_order = "little" if magic == 0x377F0682 else "big"
    checksum = _wal_checksum(header[:24], byte_order=byte_order)
    if checksum != struct.unpack(">II", header[24:32]):
        raise DatabaseCorruptionError("SQLite WAL header checksum is invalid")

    expected_salts = header[16:24]
    frame_size = 24 + page_size
    for frame_offset in range(32, len(wal_bytes), frame_size):
        frame = wal_bytes[frame_offset : frame_offset + frame_size]
        if frame[8:16] != expected_salts:
            raise DatabaseCorruptionError("SQLite WAL frame salts do not match its header")
        if struct.unpack(">I", frame[:4])[0] == 0:
            raise DatabaseCorruptionError("SQLite WAL frame has an invalid page number")
        checksum = _wal_checksum(
            frame[:8] + frame[24:],
            byte_order=byte_order,
            initial=checksum,
        )
        if checksum != struct.unpack(">II", frame[16:24]):
            raise DatabaseCorruptionError("SQLite WAL frame checksum is invalid")


def _wal_checksum(
    content: bytes,
    *,
    byte_order: str,
    initial: tuple[int, int] = (0, 0),
) -> tuple[int, int]:
    if len(content) % 8:
        raise ValueError("SQLite WAL checksum input must be a multiple of eight bytes")
    first, second = initial
    for offset in range(0, len(content), 8):
        left = int.from_bytes(content[offset : offset + 4], byte_order)
        right = int.from_bytes(content[offset + 4 : offset + 8], byte_order)
        first = (first + left + second) & 0xFFFFFFFF
        second = (second + right + first) & 0xFFFFFFFF
    return first, second


def _serialize_event(event: ControlEvent) -> bytes:
    body = json.dumps(
        event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return body.encode()


def _row_metadata(**values: object) -> dict[str, object]:
    return values


def _aad(row_metadata: dict[str, object]) -> bytes:
    return json.dumps(row_metadata, sort_keys=True, separators=(",", ":")).encode()


def _position_from_row(row: sqlite3.Row) -> StoredPosition:
    return StoredPosition(
        local_log_seq=int(row["local_log_seq"]),
        repo_seq=int(row["repo_seq"]),
        session_seq=int(row["session_seq"]),
    )


def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", 0)
    return error_code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or any(
        text in str(exc).lower() for text in ("locked", "busy")
    )


def _is_corruption_error(exc: sqlite3.DatabaseError) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    if error_code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return True
    message = str(exc).lower()
    return any(
        fragment in message
        for fragment in ("corrupt", "malformed", "not a database")
    )
