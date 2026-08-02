from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class UnsafeStatePathError(Exception):
    """LoopGuard state is not an owner-controlled real directory or file."""


@dataclass(frozen=True, slots=True)
class ControlPaths:
    home: Path
    events_db: Path
    socket: Path
    pid: Path
    config: Path
    integration_trust: Path

    @classmethod
    def from_home(cls, home: str | Path | None = None) -> ControlPaths:
        resolved = loopguard_home(home)
        return cls(
            home=resolved,
            events_db=resolved / "events.db",
            socket=resolved / "loopguard.sock",
            pid=resolved / "loopguard.pid",
            config=resolved / "config.json",
            integration_trust=resolved / "integration-trust.json",
        )


def loopguard_home(home: str | Path | None = None) -> Path:
    configured = home
    if configured is None:
        configured = os.environ.get("LOOPGUARD_HOME") or Path.home() / ".loopguard"
    return Path(configured).expanduser()


def ensure_private_home(path: Path) -> None:
    created = False
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True)
        created = True
        status = os.lstat(path)
    if not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode):
        raise UnsafeStatePathError("LoopGuard home is not a real directory")
    if os.name == "posix":
        if status.st_uid != os.getuid():
            raise UnsafeStatePathError("LoopGuard home is owned by another user")
        if created:
            os.chmod(path, 0o700)
            status = os.lstat(path)
        if stat.S_IMODE(status.st_mode) != 0o700:
            raise UnsafeStatePathError("LoopGuard home must have mode 0700")


def write_pid_file(path: Path, pid: int) -> None:
    if pid <= 0:
        raise ValueError("pid must be positive")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise UnsafeStatePathError("LoopGuard daemon PID file already exists") from exc
    try:
        os.write(descriptor, f"{pid}\n".encode("ascii"))
        os.fsync(descriptor)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def remove_owned_pid_file(path: Path) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
        raise UnsafeStatePathError("LoopGuard daemon PID path is unsafe")
    if os.name == "posix" and (
        status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
    ):
        raise UnsafeStatePathError("LoopGuard daemon PID file is not owner-only")
    path.unlink()
