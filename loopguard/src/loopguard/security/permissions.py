from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PermissionResult:
    code: str
    detail: str | None = None

    @property
    def safe(self) -> bool:
        return self.code == "safe"


def validate_private_file(path: Path) -> PermissionResult:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return PermissionResult("missing")
    if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
        return PermissionResult("unsafe_type", "private state must be a regular file")
    if os.name == "posix":
        if status.st_uid != os.getuid():
            return PermissionResult("unsafe_owner", "private state belongs to another user")
        if stat.S_IMODE(status.st_mode) != 0o600:
            return PermissionResult("unsafe_permissions", "private files require mode 0600")
    return PermissionResult("safe")


def validate_private_directory(path: Path) -> PermissionResult:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return PermissionResult("missing")
    if not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode):
        return PermissionResult("unsafe_type", "private state must be a real directory")
    if os.name == "posix":
        if status.st_uid != os.getuid():
            return PermissionResult("unsafe_owner", "private state belongs to another user")
        if stat.S_IMODE(status.st_mode) != 0o700:
            return PermissionResult("unsafe_permissions", "private directories require mode 0700")
    return PermissionResult("safe")


def create_private_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
