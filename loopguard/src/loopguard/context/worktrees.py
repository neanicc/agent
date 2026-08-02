from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


_SAFE_SESSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}\Z")
_SAFE_BASE = re.compile(r"[0-9a-fA-F]{7,64}\Z")
_SCHEMA = """
CREATE TABLE allocations (
    allocation_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    session_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    active_pid INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(repo_id, session_id),
    UNIQUE(repo_id, branch),
    UNIQUE(worktree_path)
);
CREATE INDEX allocations_repo_status ON allocations(repo_id, status, updated_at);
CREATE TABLE attached_sessions (
    repo_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY(repo_id, session_id)
);
CREATE INDEX attached_worktree
ON attached_sessions(repo_id, worktree_id, expires_at, session_id);
PRAGMA user_version = 1;
"""

AllocationStatus = Literal["allocating", "allocated", "quarantined", "released", "failed"]
AttachedPolicy = Literal["warn", "block"]


class WorktreeAllocationError(RuntimeError):
    """A managed worktree could not be created without risking repository state."""


class WorktreeAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allocation_id: str
    repo_id: str
    repository_path: Path
    session_id: str
    branch: str
    path: Path
    base_sha: str
    status: AllocationStatus
    reason: str | None = None
    active_pid: int | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AttachedCollisionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["allow", "warn", "block"]
    collision: bool
    other_session_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str


class WorktreeManager:
    def __init__(
        self,
        *,
        root: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root.expanduser().absolute()
        if self.root.is_symlink():
            raise ValueError("worktree root cannot be a symlink")
        root_existed = self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("worktree root cannot contain a symlink")
        if os.name == "posix":
            status = os.lstat(self.root)
            if status.st_uid != os.getuid():
                raise ValueError("worktree root must be owned by the current user")
            if root_existed and stat.S_IMODE(status.st_mode) != 0o700:
                raise ValueError("existing worktree root must have mode 0700")
            self.root.chmod(0o700)
        self.database_path = self.root / ".loopguard-worktrees.db"
        _create_owner_file(self.database_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.database_path,
            isolation_level=None,
            check_same_thread=False,
            timeout=10,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 10000")
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        objects = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        if version == 0:
            if objects:
                self._connection.close()
                raise ValueError("unversioned worktree database is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            self._connection.close()
            raise ValueError("worktree database schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._validate_schema()
        self._secure_files()

    def allocate(
        self,
        repository: Path,
        *,
        session_id: str,
        base_sha: str | None = None,
    ) -> WorktreeAllocation:
        session_id = _identifier(session_id, label="session identity")
        repository_path, repo_id = _repository(repository)
        if self.root.is_relative_to(repository_path) or repository_path.is_relative_to(self.root):
            raise ValueError("worktree root and source repository must not overlap")
        resolved_base = _base_commit(repository_path, base_sha)
        slug = _session_slug(session_id)
        branch = f"loopguard/{slug}"
        repository_slug = _repository_slug(repository_path, repo_id)
        parent = self.root / repository_slug
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = parent / slug
        _contained(self.root, parent)
        if path.is_symlink():
            raise WorktreeAllocationError("managed worktree path cannot be a symlink")
        allocation_id = hashlib.sha256(f"{repo_id}\0{session_id}".encode()).hexdigest()
        now = _aware_utc(self._clock())
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            failure: Exception | None = None
            try:
                row = connection.execute(
                    "SELECT * FROM allocations WHERE repo_id = ? AND session_id = ?",
                    (repo_id, session_id),
                ).fetchone()
                if row is not None and str(row["status"]) in {"allocated", "quarantined"}:
                    allocation = _allocation(row)
                    if allocation.base_sha != resolved_base:
                        raise ValueError("existing allocation uses a different base commit")
                    if allocation.path.exists():
                        connection.execute("COMMIT")
                        return allocation
                created_at = str(row["created_at"]) if row is not None else now.isoformat()
                connection.execute(
                    """
                    INSERT INTO allocations(
                        allocation_id, repo_id, repository_path, session_id, branch,
                        worktree_path, base_sha, status, reason, active_pid,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'allocating', NULL, NULL, ?, ?)
                    ON CONFLICT(repo_id, session_id) DO UPDATE SET
                        repository_path = excluded.repository_path,
                        branch = excluded.branch,
                        worktree_path = excluded.worktree_path,
                        base_sha = excluded.base_sha,
                        status = 'allocating', reason = NULL, active_pid = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        allocation_id,
                        repo_id,
                        str(repository_path),
                        session_id,
                        branch,
                        str(path),
                        resolved_base,
                        created_at,
                        now.isoformat(),
                    ),
                )
                try:
                    self._materialize(repository_path, path, branch, resolved_base)
                except Exception as exc:
                    failure = exc
                    connection.execute(
                        "UPDATE allocations SET status = 'failed', reason = ?, updated_at = ? "
                        "WHERE allocation_id = ?",
                        (_bounded_reason(exc), now.isoformat(), allocation_id),
                    )
                else:
                    connection.execute(
                        "UPDATE allocations SET status = 'allocated', reason = NULL, "
                        "updated_at = ? WHERE allocation_id = ?",
                        (now.isoformat(), allocation_id),
                    )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
            if failure is not None:
                raise WorktreeAllocationError("managed worktree allocation failed safely") from failure
            row = connection.execute(
                "SELECT * FROM allocations WHERE allocation_id = ?", (allocation_id,)
            ).fetchone()
            assert row is not None
            return _allocation(row)

    def release(self, repository: Path, *, session_id: str) -> WorktreeAllocation:
        session_id = _identifier(session_id, label="session identity")
        repository_path, repo_id = _repository(repository)
        now = _aware_utc(self._clock())
        with self._lock:
            connection = self._require_open()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM allocations WHERE repo_id = ? AND session_id = ?",
                    (repo_id, session_id),
                ).fetchone()
                if row is None:
                    raise ValueError("managed worktree allocation is missing")
                allocation = _allocation(row)
                if allocation.status == "released":
                    connection.execute("COMMIT")
                    return allocation
                reason = self._quarantine_reason(repository_path, allocation)
                if reason is not None:
                    connection.execute(
                        "UPDATE allocations SET status = 'quarantined', reason = ?, "
                        "updated_at = ? WHERE allocation_id = ?",
                        (reason, now.isoformat(), allocation.allocation_id),
                    )
                else:
                    self._remove(repository_path, allocation)
                    connection.execute(
                        "UPDATE allocations SET status = 'released', reason = NULL, "
                        "active_pid = NULL, updated_at = ? WHERE allocation_id = ?",
                        (now.isoformat(), allocation.allocation_id),
                    )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._secure_files()
            updated = connection.execute(
                "SELECT * FROM allocations WHERE repo_id = ? AND session_id = ?",
                (repo_id, session_id),
            ).fetchone()
            assert updated is not None
            return _allocation(updated)

    def mark_process(self, repository: Path, session_id: str, pid: int | None) -> None:
        session_id = _identifier(session_id, label="session identity")
        _repository_path, repo_id = _repository(repository)
        if pid is not None and pid <= 0:
            raise ValueError("managed process PID must be positive")
        with self._lock:
            cursor = self._require_open().execute(
                "UPDATE allocations SET active_pid = ?, updated_at = ? "
                "WHERE repo_id = ? AND session_id = ? AND status != 'released'",
                (pid, _aware_utc(self._clock()).isoformat(), repo_id, session_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("managed worktree allocation is missing")

    def attach(
        self,
        repo_id: str,
        session_id: str,
        worktree_id: str,
        *,
        ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        repo_id = _identifier(repo_id, label="repository identity")
        session_id = _identifier(session_id, label="session identity")
        worktree_id = _identifier(worktree_id, label="worktree identity")
        seconds = ttl.total_seconds()
        if not 1 <= seconds <= 24 * 60 * 60:
            raise ValueError("attached session TTL must be between one second and 24 hours")
        expires = _aware_utc(self._clock()).timestamp() + seconds
        with self._lock:
            self._require_open().execute(
                """
                INSERT INTO attached_sessions(repo_id, session_id, worktree_id, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo_id, session_id) DO UPDATE SET
                    worktree_id = excluded.worktree_id, expires_at = excluded.expires_at
                """,
                (repo_id, session_id, worktree_id, expires),
            )

    def detach(self, repo_id: str, session_id: str) -> bool:
        repo_id = _identifier(repo_id, label="repository identity")
        session_id = _identifier(session_id, label="session identity")
        with self._lock:
            cursor = self._require_open().execute(
                "DELETE FROM attached_sessions WHERE repo_id = ? AND session_id = ?",
                (repo_id, session_id),
            )
            return cursor.rowcount == 1

    def attached_mutation_decision(
        self,
        repo_id: str,
        session_id: str,
        worktree_id: str,
        *,
        policy: AttachedPolicy = "warn",
    ) -> AttachedCollisionDecision:
        if policy not in {"warn", "block"}:
            raise ValueError("attached collision policy is invalid")
        repo_id = _identifier(repo_id, label="repository identity")
        session_id = _identifier(session_id, label="session identity")
        worktree_id = _identifier(worktree_id, label="worktree identity")
        now = _aware_utc(self._clock()).timestamp()
        with self._lock:
            connection = self._require_open()
            connection.execute("DELETE FROM attached_sessions WHERE expires_at <= ?", (now,))
            rows = connection.execute(
                """
                SELECT session_id FROM attached_sessions
                WHERE repo_id = ? AND worktree_id = ? AND session_id != ?
                  AND expires_at > ?
                ORDER BY session_id LIMIT 101
                """,
                (repo_id, worktree_id, session_id, now),
            ).fetchall()
        others = [str(row["session_id"]) for row in rows[:100]]
        if not others:
            return AttachedCollisionDecision(
                action="allow",
                collision=False,
                reason="No other attached session shares this writable worktree.",
            )
        return AttachedCollisionDecision(
            action=policy,
            collision=True,
            other_session_ids=others,
            reason=(
                "Another attached session shares this writable worktree; managed isolation "
                "cannot be guaranteed."
            ),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
            self._secure_files()

    def __enter__(self) -> WorktreeManager:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _materialize(self, repository: Path, path: Path, branch: str, base_sha: str) -> None:
        if path.exists():
            if not path.is_dir() or path.is_symlink():
                raise WorktreeAllocationError("allocation path already exists and is unsafe")
            actual_repo, _repo_id = _repository(path)
            actual_branch = _git(path, "branch", "--show-current").stdout.strip()
            if actual_repo != path.resolve(strict=True) or actual_branch != branch:
                raise WorktreeAllocationError("partial allocation path belongs to other work")
            return
        reference = f"refs/heads/{branch}"
        branch_exists = _git(repository, "show-ref", "--verify", "--quiet", reference, check=False)
        if branch_exists.returncode not in {0, 1}:
            raise WorktreeAllocationError("could not inspect managed branch")
        if branch_exists.returncode == 0:
            branch_sha = _git(repository, "rev-parse", "--verify", f"{reference}^{{commit}}")
            if branch_sha.stdout.strip() != base_sha:
                raise WorktreeAllocationError("managed branch collision requires user review")
            _git(repository, "worktree", "add", str(path), branch)
        else:
            _git(repository, "worktree", "add", "-b", branch, str(path), base_sha)
        actual_repo, actual_repo_id = _repository(path)
        _source_repo, source_repo_id = _repository(repository)
        actual_branch = _git(path, "branch", "--show-current").stdout.strip()
        if actual_repo != path.resolve(strict=True) or actual_repo_id != source_repo_id:
            raise WorktreeAllocationError("created worktree failed repository containment checks")
        if actual_branch != branch:
            raise WorktreeAllocationError("created worktree is on the wrong branch")

    def _quarantine_reason(
        self,
        repository: Path,
        allocation: WorktreeAllocation,
    ) -> str | None:
        _contained(self.root, allocation.path.parent)
        if allocation.active_pid is not None and _pid_active(allocation.active_pid):
            return "active_process"
        if not allocation.path.exists():
            return "worktree_missing"
        status = _git(
            allocation.path,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        if status.stdout:
            return "uncommitted_or_untracked_changes"
        worktree_head = _git(allocation.path, "rev-parse", "HEAD").stdout.strip()
        primary_head = _git(repository, "rev-parse", "HEAD").stdout.strip()
        merged = _git(
            repository,
            "merge-base",
            "--is-ancestor",
            worktree_head,
            primary_head,
            check=False,
        )
        if merged.returncode == 1:
            return "unmerged_branch"
        if merged.returncode != 0:
            return "merge_status_unknown"
        return None

    def _remove(self, repository: Path, allocation: WorktreeAllocation) -> None:
        _contained(self.root, allocation.path.parent)
        _git(repository, "worktree", "remove", str(allocation.path))
        deletion = _git(repository, "branch", "-d", allocation.branch, check=False)
        if deletion.returncode != 0:
            raise WorktreeAllocationError("worktree removed but managed branch needs user review")

    def _require_open(self) -> sqlite3.Connection:
        if self._closed:
            raise ValueError("worktree manager is closed")
        return self._connection

    def _validate_schema(self) -> None:
        connection = self._require_open()
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        rows = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        ).fetchall()
        tables = {str(row["name"]) for row in rows}
        if integrity != "ok" or not {"allocations", "attached_sessions"}.issubset(tables):
            raise ValueError("worktree database schema or integrity check failed")

    def _secure_files(self) -> None:
        if os.name != "posix":
            return
        for path in (
            self.database_path,
            Path(f"{self.database_path}-wal"),
            Path(f"{self.database_path}-shm"),
        ):
            if path.is_symlink():
                raise ValueError("worktree state cannot be a symlink")
            if path.exists():
                path.chmod(0o600)


def _repository(path: Path) -> tuple[Path, str]:
    candidate = path.expanduser().absolute()
    if candidate.is_symlink():
        raise ValueError("repository cannot be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("repository is unavailable") from exc
    if resolved != candidate:
        raise ValueError("repository path cannot contain symlinks")
    top = Path(_git(resolved, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True)
    common_raw = _git(resolved, "rev-parse", "--git-common-dir").stdout.strip()
    common = Path(common_raw)
    if not common.is_absolute():
        common = (resolved / common).resolve(strict=True)
    else:
        common = common.resolve(strict=True)
    if top != resolved:
        raise ValueError("repository path must be the worktree root")
    repo_id = "repo:" + hashlib.sha256(str(common).encode()).hexdigest()
    return top, repo_id


def _base_commit(repository: Path, value: str | None) -> str:
    candidate = "HEAD" if value is None else value
    if value is not None and _SAFE_BASE.fullmatch(value) is None:
        raise ValueError("base commit must be a hexadecimal object ID")
    process = _git(
        repository,
        "rev-parse",
        "--verify",
        f"{candidate}^{{commit}}",
        check=False,
    )
    if process.returncode != 0:
        raise ValueError("base commit does not belong to the bound repository")
    return process.stdout.strip()


def _session_slug(session_id: str) -> str:
    lowered = session_id.lower()
    if _SAFE_SESSION.fullmatch(lowered) and lowered == session_id:
        return lowered
    raw = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip(".-_")[:32] or "session"
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:10]
    return f"{raw}-{digest}"


def _repository_slug(repository: Path, repo_id: str) -> str:
    raw = re.sub(r"[^a-z0-9._-]+", "-", repository.name.lower()).strip(".-_") or "repo"
    return f"{raw[:32]}-{repo_id[-10:]}"


def _identifier(value: str, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 512 or "\x00" in normalized:
        raise ValueError(f"{label} must be bounded and non-empty")
    return normalized


def _contained(root: Path, parent: Path) -> None:
    resolved_root = root.resolve(strict=True)
    resolved_parent = parent.resolve(strict=True)
    if not resolved_parent.is_relative_to(resolved_root):
        raise WorktreeAllocationError("managed worktree path escapes its configured root")


def _git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=check,
            capture_output=True,
            text=True,
            timeout=60,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeAllocationError("Git worktree operation failed") from exc


def _allocation(row: sqlite3.Row) -> WorktreeAllocation:
    return WorktreeAllocation(
        allocation_id=str(row["allocation_id"]),
        repo_id=str(row["repo_id"]),
        repository_path=Path(str(row["repository_path"])),
        session_id=str(row["session_id"]),
        branch=str(row["branch"]),
        path=Path(str(row["worktree_path"])),
        base_sha=str(row["base_sha"]),
        status=str(row["status"]),
        reason=str(row["reason"]) if row["reason"] is not None else None,
        active_pid=int(row["active_pid"]) if row["active_pid"] is not None else None,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _pid_active(pid: int, *, platform: str | None = None) -> bool:
    active_platform = os.name if platform is None else platform
    if active_platform == "nt":
        return _windows_pid_active(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_active(pid: int) -> bool:
    if pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    wait_failed = 0xFFFFFFFF
    access_denied = 5
    invalid_parameter = 87

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == access_denied:
            return True
        if error == invalid_parameter:
            return False
        raise OSError(error, "Windows PID probe failed")
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
    finally:
        kernel32.CloseHandle(handle)
    if result == wait_timeout:
        return True
    if result == wait_object_0:
        return False
    if result == wait_failed:
        raise OSError(ctypes.get_last_error(), "Windows PID wait failed")
    return False


def _bounded_reason(error: Exception) -> str:
    return type(error).__name__[:128]


def _create_owner_file(path: Path) -> None:
    if path.exists():
        status = os.lstat(path)
        if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
            raise ValueError("worktree database must be a regular file")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise ValueError("worktree database must be owner-only")
        return
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    os.close(descriptor)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("worktree clock must return an aware datetime")
    return value.astimezone(timezone.utc)
