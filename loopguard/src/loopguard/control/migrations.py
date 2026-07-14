from __future__ import annotations

import re
import sqlite3


CURRENT_SCHEMA_VERSION = 1

_EXPECTED_TABLE_XINFO = {
    "store_metadata": (
        ("singleton", "INTEGER", 0, None, 1, 0),
        ("key_id", "TEXT", 1, None, 0, 0),
    ),
    "repo_sequences": (
        ("repo_id", "TEXT", 0, None, 1, 0),
        ("last_seq", "INTEGER", 1, None, 0, 0),
    ),
    "session_sequences": (
        ("session_id", "TEXT", 0, None, 1, 0),
        ("last_seq", "INTEGER", 1, None, 0, 0),
    ),
    "events": (
        ("local_log_seq", "INTEGER", 0, None, 1, 0),
        ("event_id", "TEXT", 1, None, 0, 0),
        ("repo_id", "TEXT", 1, None, 0, 0),
        ("session_id", "TEXT", 1, None, 0, 0),
        ("repo_seq", "INTEGER", 1, None, 0, 0),
        ("session_seq", "INTEGER", 1, None, 0, 0),
        ("schema_version", "INTEGER", 1, None, 0, 0),
        ("key_id", "TEXT", 1, None, 0, 0),
        ("nonce", "BLOB", 1, None, 0, 0),
        ("ciphertext", "BLOB", 1, None, 0, 0),
        ("created_at", "TEXT", 1, None, 0, 0),
    ),
}


class MigrationError(Exception):
    """The event-store schema could not be migrated safely."""


class UnsupportedMigrationError(MigrationError):
    """The database schema cannot be opened by this LoopGuard version."""


_MIGRATION_1 = """
CREATE TABLE store_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    key_id TEXT NOT NULL
);

CREATE TABLE repo_sequences (
    repo_id TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL CHECK (last_seq > 0)
);

CREATE TABLE session_sequences (
    session_id TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL CHECK (last_seq > 0)
);

CREATE TABLE events (
    local_log_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    repo_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    repo_seq INTEGER NOT NULL,
    session_seq INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    key_id TEXT NOT NULL,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(repo_id, repo_seq),
    UNIQUE(session_id, session_seq),
    FOREIGN KEY(repo_id) REFERENCES repo_sequences(repo_id),
    FOREIGN KEY(session_id) REFERENCES session_sequences(session_id)
);

CREATE INDEX events_repo_replay ON events(repo_id, repo_seq);
CREATE INDEX events_session_replay ON events(session_id, session_seq);
"""


def migrate(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA user_version").fetchone()
    version = int(row[0])
    if version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedMigrationError(
            f"database schema version {version} is newer than supported version "
            f"{CURRENT_SCHEMA_VERSION}"
        )
    if version == CURRENT_SCHEMA_VERSION:
        _validate_schema(connection)
        return

    connection.execute("BEGIN EXCLUSIVE")
    try:
        if version == 0:
            for statement in _MIGRATION_1.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        connection.execute("COMMIT")
        _validate_schema(connection)
    except Exception as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(
            f"failed to migrate event store from schema version {version}"
        ) from exc


def _validate_schema(connection: sqlite3.Connection) -> None:
    for table, expected_shape in _EXPECTED_TABLE_XINFO.items():
        quoted_table = table.replace('"', '""')
        actual_shape = tuple(
            (row[1], row[2], row[3], row[4], row[5], row[6])
            for row in connection.execute(f'PRAGMA table_xinfo("{quoted_table}")')
        )
        if actual_shape != expected_shape:
            raise MigrationError(
                f"schema does not match version {CURRENT_SCHEMA_VERSION}: "
                f"{table} table shape differs"
            )

    required_checks = {
        "store_metadata": "check(singleton=1)",
        "repo_sequences": "check(last_seq>0)",
        "session_sequences": "check(last_seq>0)",
    }
    for table, required_check in required_checks.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        normalized_sql = _normalize_schema_sql(
            _strip_sql_comments(row[0] if row is not None else "")
        )
        if required_check not in normalized_sql:
            raise MigrationError(
                f"schema does not match version 1: {table} CHECK constraint differs"
            )

    events_row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'events'"
    ).fetchone()
    events_sql = _normalize_schema_sql(
        _strip_sql_comments(events_row[0] if events_row is not None else "")
    )
    if "local_log_seqintegerprimarykeyautoincrement" not in events_sql:
        raise MigrationError("schema does not match version 1: local cursor is not AUTOINCREMENT")

    unique_indexes: set[tuple[str, ...]] = set()
    replay_indexes: dict[str, tuple[str, ...]] = {}
    for index in connection.execute("PRAGMA index_list(events)"):
        quoted_name = str(index[1]).replace('"', '""')
        columns = tuple(
            row[2]
            for row in connection.execute(f'PRAGMA index_info("{quoted_name}")')
        )
        is_unique = bool(index[2])
        is_partial = bool(index[4])
        if is_unique:
            if is_partial:
                raise MigrationError(
                    "schema does not match version 1: event uniqueness must not be partial"
                )
            unique_indexes.add(columns)
        elif str(index[3]) == "c":
            if is_partial:
                raise MigrationError(
                    "schema does not match version 1: replay indexes must not be partial"
                )
            replay_indexes[str(index[1])] = columns
    required_unique_indexes = {
        ("event_id",),
        ("repo_id", "repo_seq"),
        ("session_id", "session_seq"),
    }
    if unique_indexes != required_unique_indexes:
        raise MigrationError("schema does not match version 1: event uniqueness differs")
    required_replay_indexes = {
        "events_repo_replay": ("repo_id", "repo_seq"),
        "events_session_replay": ("session_id", "session_seq"),
    }
    if replay_indexes != required_replay_indexes:
        raise MigrationError("schema does not match version 1: replay indexes differ")

    foreign_keys = {
        (row[3], row[2], row[4], row[5], row[6], row[7])
        for row in connection.execute("PRAGMA foreign_key_list(events)")
    }
    required_foreign_keys = {
        ("repo_id", "repo_sequences", "repo_id", "NO ACTION", "NO ACTION", "NONE"),
        ("session_id", "session_sequences", "session_id", "NO ACTION", "NO ACTION", "NONE"),
    }
    if foreign_keys != required_foreign_keys:
        raise MigrationError("schema does not match version 1: cursor foreign keys differ")


def _normalize_schema_sql(sql: str) -> str:
    return re.sub(r'[\s"`\[\]]+', "", sql).lower()


def _strip_sql_comments(sql: str) -> str:
    stripped: list[str] = []
    index = 0
    quote_end: str | None = None
    while index < len(sql):
        character = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if quote_end is not None:
            stripped.append(character)
            if character == quote_end:
                if quote_end != "]" and following == quote_end:
                    stripped.append(following)
                    index += 2
                    continue
                quote_end = None
            index += 1
            continue

        if character in ("'", '"', "`"):
            quote_end = character
            stripped.append(character)
            index += 1
            continue
        if character == "[":
            quote_end = "]"
            stripped.append(character)
            index += 1
            continue
        if character == "-" and following == "-":
            index += 2
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            continue
        if character == "/" and following == "*":
            index += 2
            while index + 1 < len(sql) and sql[index : index + 2] != "*/":
                index += 1
            if index + 1 < len(sql):
                index += 2
            stripped.append(" ")
            continue
        stripped.append(character)
        index += 1
    return "".join(stripped)
