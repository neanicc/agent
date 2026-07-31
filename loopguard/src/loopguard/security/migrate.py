from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from loopguard.control.migrations import CURRENT_SCHEMA_VERSION, migrate

from .permissions import create_private_file, validate_private_file


_BACKUP_MAGIC = b"LGBACKUP1"


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    code: str
    current_schema: int
    target_schema: int
    required_steps: tuple[str, ...]
    backup_path: Path | None = None
    restore_command: str | None = None


class LocalMigrationService:
    def __init__(self, backup_key: bytes) -> None:
        if len(backup_key) != 32:
            raise ValueError("backup key must contain 32 bytes")
        self.backup_key = bytes(backup_key)

    def check(self, database: Path) -> MigrationStatus:
        if not database.exists():
            return MigrationStatus(
                "not_initialized",
                0,
                CURRENT_SCHEMA_VERSION,
                ("initialize encrypted event store",),
            )
        permission = validate_private_file(database)
        if not permission.safe:
            return MigrationStatus(
                permission.code,
                -1,
                CURRENT_SCHEMA_VERSION,
                (),
            )
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if integrity != "ok":
            return MigrationStatus("integrity_failed", current, CURRENT_SCHEMA_VERSION, ())
        if current > CURRENT_SCHEMA_VERSION:
            return MigrationStatus("newer_schema", current, CURRENT_SCHEMA_VERSION, ())
        steps = tuple(
            f"apply schema {version}"
            for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1)
        )
        return MigrationStatus(
            "migration_required" if steps else "current",
            current,
            CURRENT_SCHEMA_VERSION,
            steps,
        )

    def apply(
        self,
        database: Path,
        *,
        backup_directory: Path | None = None,
    ) -> MigrationStatus:
        status = self.check(database)
        if status.code == "current":
            return status
        if status.code != "migration_required":
            return status
        required_space = max(database.stat().st_size * 3, 1_048_576)
        destination = backup_directory or database.parent
        if shutil.disk_usage(destination).free < required_space:
            return MigrationStatus(
                "insufficient_space",
                status.current_schema,
                status.target_schema,
                status.required_steps,
            )
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(destination, 0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = destination / f"{database.name}.{stamp}.lgbak"
        with sqlite3.connect(database) as source, sqlite3.connect(":memory:") as snapshot:
            source.backup(snapshot)
            snapshot_bytes = snapshot.serialize()
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.backup_key).encrypt(
            nonce,
            snapshot_bytes,
            database.name.encode("utf-8"),
        )
        create_private_file(backup, _BACKUP_MAGIC + nonce + ciphertext)
        self._verify_backup(backup, database.name)
        try:
            with sqlite3.connect(database) as connection:
                migrate(connection)
                if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                    raise sqlite3.DatabaseError("post-migration integrity check failed")
        except Exception:
            return MigrationStatus(
                "migration_failed",
                status.current_schema,
                status.target_schema,
                status.required_steps,
                backup,
                self.restore_command(backup, database),
            )
        complete = self.check(database)
        return MigrationStatus(
            complete.code,
            complete.current_schema,
            complete.target_schema,
            (),
            backup,
            self.restore_command(backup, database),
        )

    def restore(self, backup: Path, database: Path) -> None:
        plaintext = self._verify_backup(backup, database.name)
        temporary = database.with_suffix(database.suffix + ".restore")
        create_private_file(temporary, plaintext)
        with sqlite3.connect(f"file:{temporary}?mode=ro", uri=True) as connection:
            if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                temporary.unlink(missing_ok=True)
                raise ValueError("restored database failed integrity check")
        os.replace(temporary, database)
        if os.name == "posix":
            os.chmod(database, 0o600)

    def restore_command(self, backup: Path, database: Path) -> str:
        return (
            "loopguard migrate restore "
            f"--backup {backup.as_posix()} --database {database.as_posix()}"
        )

    def _verify_backup(self, backup: Path, database_name: str) -> bytes:
        if not validate_private_file(backup).safe:
            raise PermissionError("backup is not owner-only")
        payload = backup.read_bytes()
        if len(payload) < len(_BACKUP_MAGIC) + 12 + 16 or not payload.startswith(
            _BACKUP_MAGIC
        ):
            raise ValueError("backup is malformed")
        offset = len(_BACKUP_MAGIC)
        return AESGCM(self.backup_key).decrypt(
            payload[offset : offset + 12],
            payload[offset + 12 :],
            database_name.encode("utf-8"),
        )
