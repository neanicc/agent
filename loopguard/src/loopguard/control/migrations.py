from __future__ import annotations

import sqlite3


CURRENT_SCHEMA_VERSION = 1

_EXPECTED_COLUMNS = {
    "store_metadata": ("singleton", "key_id"),
    "repo_sequences": ("repo_id", "last_seq"),
    "session_sequences": ("session_id", "last_seq"),
    "events": (
        "local_log_seq",
        "event_id",
        "repo_id",
        "session_id",
        "repo_seq",
        "session_seq",
        "schema_version",
        "key_id",
        "nonce",
        "ciphertext",
        "created_at",
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
    for table, expected_columns in _EXPECTED_COLUMNS.items():
        columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        if columns != expected_columns:
            raise MigrationError(
                f"schema does not match version {CURRENT_SCHEMA_VERSION}: {table} columns differ"
            )

    required_primary_keys = {
        "store_metadata": "singleton",
        "repo_sequences": "repo_id",
        "session_sequences": "session_id",
        "events": "local_log_seq",
    }
    for table, primary_key in required_primary_keys.items():
        table_info = {row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")}
        if table_info[primary_key][5] != 1:
            raise MigrationError(
                f"schema does not match version 1: {table}.{primary_key} is not primary"
            )

    events_sql = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'events'"
    ).fetchone()[0]
    if "AUTOINCREMENT" not in events_sql.upper():
        raise MigrationError("schema does not match version 1: local cursor is not AUTOINCREMENT")

    unique_indexes: set[tuple[str, ...]] = set()
    for index in connection.execute("PRAGMA index_list(events)"):
        if not index[2]:
            continue
        quoted_name = str(index[1]).replace('"', '""')
        unique_indexes.add(
            tuple(
                row[2]
                for row in connection.execute(f'PRAGMA index_info("{quoted_name}")')
            )
        )
    required_unique_indexes = {
        ("event_id",),
        ("repo_id", "repo_seq"),
        ("session_id", "session_seq"),
    }
    if not required_unique_indexes.issubset(unique_indexes):
        raise MigrationError("schema does not match version 1: event uniqueness differs")

    foreign_keys = {
        (row[3], row[2], row[4])
        for row in connection.execute("PRAGMA foreign_key_list(events)")
    }
    required_foreign_keys = {
        ("repo_id", "repo_sequences", "repo_id"),
        ("session_id", "session_sequences", "session_id"),
    }
    if foreign_keys != required_foreign_keys:
        raise MigrationError("schema does not match version 1: cursor foreign keys differ")
