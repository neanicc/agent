from __future__ import annotations

import os
import sqlite3
import stat
import threading
from pathlib import Path
from types import TracebackType

from .symbols import SymbolSnapshot


_SCHEMA = """
CREATE TABLE symbol_snapshots (
    repo_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY(repo_id, worktree_id, path)
);
CREATE TABLE symbol_paths (
    repo_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    path TEXT NOT NULL,
    symbol TEXT NOT NULL,
    PRIMARY KEY(repo_id, worktree_id, path, symbol),
    FOREIGN KEY(repo_id, worktree_id, path)
      REFERENCES symbol_snapshots(repo_id, worktree_id, path) ON DELETE CASCADE
);
CREATE TABLE import_edges (
    repo_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    import_name TEXT NOT NULL,
    PRIMARY KEY(repo_id, worktree_id, source_path, import_name),
    FOREIGN KEY(repo_id, worktree_id, source_path)
      REFERENCES symbol_snapshots(repo_id, worktree_id, path) ON DELETE CASCADE
);
CREATE INDEX symbol_paths_lookup ON symbol_paths(repo_id, symbol, path);
CREATE INDEX import_edges_lookup ON import_edges(repo_id, import_name, source_path);
PRAGMA user_version = 1;
"""


class SymbolIndex:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        sidecars = (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"))
        if any(candidate.is_symlink() for candidate in sidecars):
            raise ValueError("symbol index cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.resolve(strict=True) != self.path.parent:
            raise ValueError("symbol index parent cannot contain a symlink")
        if not self.path.exists():
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
        elif os.name == "posix":
            status = os.lstat(self.path)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.getuid()
                or stat.S_IMODE(status.st_mode) != 0o600
            ):
                raise ValueError("symbol index must be an owner-only regular file")
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
                raise ValueError("unversioned symbol index is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            raise ValueError("symbol index schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._validate_schema()
        self._secure_files()

    def update(
        self,
        repo_id: str,
        worktree_id: str,
        snapshot: SymbolSnapshot,
        *,
        content_hash: str,
    ) -> bool:
        _identity(repo_id, worktree_id)
        _content_hash(content_hash)
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT content_hash, version FROM symbol_snapshots "
                    "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                    (repo_id, worktree_id, snapshot.path),
                ).fetchone()
                if row is not None and row["content_hash"] == content_hash:
                    connection.execute("COMMIT")
                    return False
                version = int(row["version"]) + 1 if row is not None else 1
                if row is not None:
                    self._delete_projection(repo_id, worktree_id, snapshot.path)
                    connection.execute(
                        "DELETE FROM symbol_snapshots "
                        "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                        (repo_id, worktree_id, snapshot.path),
                    )
                self._insert(repo_id, worktree_id, snapshot, content_hash, version)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
            return True

    def delete(self, repo_id: str, worktree_id: str, path: str) -> bool:
        _identity(repo_id, worktree_id)
        normalized = SymbolSnapshot(path=path, language="unknown").path
        with self._lock:
            connection = self._require_open()
            cursor = connection.execute(
                "DELETE FROM symbol_snapshots "
                "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                (repo_id, worktree_id, normalized),
            )
            return cursor.rowcount == 1

    def rename(
        self,
        repo_id: str,
        worktree_id: str,
        old_path: str,
        new_path: str,
    ) -> None:
        _identity(repo_id, worktree_id)
        old = SymbolSnapshot(path=old_path, language="unknown").path
        new = SymbolSnapshot(path=new_path, language="unknown").path
        if old == new:
            return
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT content_hash, version, snapshot_json FROM symbol_snapshots "
                    "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                    (repo_id, worktree_id, old),
                ).fetchone()
                if row is None:
                    raise ValueError("renamed symbol snapshot is missing")
                collision = connection.execute(
                    "SELECT 1 FROM symbol_snapshots "
                    "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                    (repo_id, worktree_id, new),
                ).fetchone()
                if collision is not None:
                    raise ValueError("renamed symbol snapshot already exists")
                snapshot = SymbolSnapshot.model_validate_json(row["snapshot_json"])
                self._delete_projection(repo_id, worktree_id, old)
                connection.execute(
                    "DELETE FROM symbol_snapshots "
                    "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                    (repo_id, worktree_id, old),
                )
                self._insert(
                    repo_id,
                    worktree_id,
                    snapshot.model_copy(update={"path": new}),
                    str(row["content_hash"]),
                    int(row["version"]) + 1,
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()

    def snapshot(
        self,
        repo_id: str,
        worktree_id: str,
        path: str,
    ) -> SymbolSnapshot | None:
        _identity(repo_id, worktree_id)
        normalized = SymbolSnapshot(path=path, language="unknown").path
        with self._lock:
            row = self._require_open().execute(
                "SELECT snapshot_json FROM symbol_snapshots "
                "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                (repo_id, worktree_id, normalized),
            ).fetchone()
            return (
                SymbolSnapshot.model_validate_json(row["snapshot_json"])
                if row is not None
                else None
            )

    def version(self, repo_id: str, worktree_id: str, path: str) -> int | None:
        _identity(repo_id, worktree_id)
        normalized = SymbolSnapshot(path=path, language="unknown").path
        with self._lock:
            row = self._require_open().execute(
                "SELECT version FROM symbol_snapshots "
                "WHERE repo_id = ? AND worktree_id = ? AND path = ?",
                (repo_id, worktree_id, normalized),
            ).fetchone()
            return int(row["version"]) if row is not None else None

    def paths_for_symbol(
        self,
        repo_id: str,
        symbol: str,
        *,
        worktree_id: str | None = None,
    ) -> list[str]:
        if not repo_id.strip() or not symbol.strip():
            raise ValueError("symbol lookup identity must not be empty")
        if worktree_id is not None and not worktree_id.strip():
            raise ValueError("worktree identity must not be empty")
        with self._lock:
            if worktree_id is None:
                rows = self._require_open().execute(
                    "SELECT DISTINCT path FROM symbol_paths "
                    "WHERE repo_id = ? AND symbol = ? ORDER BY path",
                    (repo_id, symbol),
                ).fetchall()
            else:
                rows = self._require_open().execute(
                    "SELECT path FROM symbol_paths "
                    "WHERE repo_id = ? AND worktree_id = ? AND symbol = ? ORDER BY path",
                    (repo_id, worktree_id, symbol),
                ).fetchall()
            return [str(row["path"]) for row in rows]

    def imports_for_path(
        self,
        repo_id: str,
        path: str,
        *,
        worktree_id: str | None = None,
    ) -> list[str]:
        if not repo_id.strip():
            raise ValueError("repository identity must not be empty")
        if worktree_id is not None and not worktree_id.strip():
            raise ValueError("worktree identity must not be empty")
        normalized = SymbolSnapshot(path=path, language="unknown").path
        with self._lock:
            if worktree_id is None:
                rows = self._require_open().execute(
                    "SELECT DISTINCT import_name FROM import_edges "
                    "WHERE repo_id = ? AND source_path = ? ORDER BY import_name",
                    (repo_id, normalized),
                ).fetchall()
            else:
                rows = self._require_open().execute(
                    "SELECT import_name FROM import_edges "
                    "WHERE repo_id = ? AND worktree_id = ? AND source_path = ? "
                    "ORDER BY import_name",
                    (repo_id, worktree_id, normalized),
                ).fetchall()
            return [str(row["import_name"]) for row in rows]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            self._secure_files()

    def __enter__(self) -> SymbolIndex:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _insert(
        self,
        repo_id: str,
        worktree_id: str,
        snapshot: SymbolSnapshot,
        content_hash: str,
        version: int,
    ) -> None:
        connection = self._require_open()
        connection.execute(
            "INSERT INTO symbol_snapshots("
            "repo_id, worktree_id, path, content_hash, version, snapshot_json"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                repo_id,
                worktree_id,
                snapshot.path,
                content_hash,
                version,
                snapshot.model_dump_json(),
            ),
        )
        for symbol in snapshot.definitions:
            connection.execute(
                "INSERT INTO symbol_paths(repo_id, worktree_id, path, symbol) "
                "VALUES (?, ?, ?, ?)",
                (repo_id, worktree_id, snapshot.path, symbol),
            )
        for import_name in snapshot.imports:
            connection.execute(
                "INSERT INTO import_edges(repo_id, worktree_id, source_path, import_name) "
                "VALUES (?, ?, ?, ?)",
                (repo_id, worktree_id, snapshot.path, import_name),
            )

    def _delete_projection(self, repo_id: str, worktree_id: str, path: str) -> None:
        connection = self._require_open()
        connection.execute(
            "DELETE FROM symbol_paths WHERE repo_id = ? AND worktree_id = ? AND path = ?",
            (repo_id, worktree_id, path),
        )
        connection.execute(
            "DELETE FROM import_edges WHERE repo_id = ? AND worktree_id = ? AND source_path = ?",
            (repo_id, worktree_id, path),
        )

    def _require_open(self) -> sqlite3.Connection:
        if self._closed:
            raise ValueError("symbol index is closed")
        return self._connection

    def _validate_schema(self) -> None:
        connection = self._require_open()
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError("symbol index integrity check failed")
        required = {"symbol_snapshots", "symbol_paths", "import_edges"}
        rows = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        ).fetchall()
        if not required.issubset({str(row["name"]) for row in rows}):
            raise ValueError("symbol index schema is incomplete")

    def _secure_files(self) -> None:
        if os.name != "posix":
            return
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.is_symlink():
                raise ValueError("symbol index state cannot be a symlink")
            if path.exists():
                path.chmod(0o600)


def _identity(repo_id: str, worktree_id: str) -> None:
    if not repo_id.strip() or not worktree_id.strip():
        raise ValueError("symbol index identity must not be empty")


def _content_hash(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("symbol content hash must be a SHA-256 digest")
