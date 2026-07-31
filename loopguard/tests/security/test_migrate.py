from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from loopguard.control.migrations import CURRENT_SCHEMA_VERSION, _MIGRATION_1
from loopguard.security.migrate import LocalMigrationService
from loopguard.security.permissions import validate_private_file


def version_one_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(_MIGRATION_1)
        connection.execute("PRAGMA user_version = 1")
    if os.name == "posix":
        path.chmod(0o600)


def test_migration_check_is_read_only_and_machine_readable(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    version_one_database(database)
    before = database.read_bytes()

    status = LocalMigrationService(b"b" * 32).check(database)

    assert status.code == "migration_required"
    assert status.current_schema == 1
    assert status.target_schema == CURRENT_SCHEMA_VERSION
    assert status.required_steps == ("apply schema 2", "apply schema 3")
    assert database.read_bytes() == before


def test_apply_creates_verified_encrypted_backup_then_migrates(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    version_one_database(database)
    service = LocalMigrationService(b"b" * 32)

    result = service.apply(database)

    assert result.code == "current"
    assert result.current_schema == CURRENT_SCHEMA_VERSION
    assert result.backup_path is not None
    assert validate_private_file(result.backup_path).safe
    assert b"SQLite format 3" not in result.backup_path.read_bytes()
    assert result.restore_command is not None
    assert "loopguard migrate restore" in result.restore_command


def test_encrypted_backup_can_restore_the_pre_migration_database(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    version_one_database(database)
    service = LocalMigrationService(b"b" * 32)
    result = service.apply(database)
    assert result.backup_path is not None

    service.restore(result.backup_path, database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_migration_refuses_unsafe_database_permissions(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    version_one_database(database)
    database.chmod(0o644)

    assert LocalMigrationService(b"b" * 32).apply(database).code == "unsafe_permissions"
