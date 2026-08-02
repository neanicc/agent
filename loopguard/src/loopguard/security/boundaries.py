from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class BoundaryResult:
    code: str
    path: Path | None = None

    @property
    def accepted(self) -> bool:
        return self.code == "accepted"


def validate_hook_artifact(
    payload: Mapping[str, Any],
    *,
    state_directory: Path,
) -> BoundaryResult:
    """Resolve an artifact named by an untrusted hook inside owner-only state.

    Hook payloads never receive a general-purpose filesystem capability. The
    artifact must already exist below the state directory, and neither the
    state root nor any traversed component may be a symlink.
    """

    supplied = payload.get("artifact_path")
    if not isinstance(supplied, str) or not supplied or len(supplied) > 1_024:
        return BoundaryResult("invalid_path")
    normalized = supplied.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (
        relative.is_absolute()
        or PureWindowsPath(supplied).is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return BoundaryResult("invalid_path")

    try:
        root_status = os.lstat(state_directory)
        if (
            not stat.S_ISDIR(root_status.st_mode)
            or stat.S_ISLNK(root_status.st_mode)
            or (os.name == "posix" and root_status.st_uid != os.getuid())
        ):
            return BoundaryResult("unsafe_state_directory")

        root = state_directory.resolve(strict=True)
        candidate = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            status = os.lstat(current)
            if stat.S_ISLNK(status.st_mode):
                return BoundaryResult("invalid_path")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            return BoundaryResult("invalid_path")
    except (FileNotFoundError, OSError, RuntimeError):
        return BoundaryResult("invalid_path")
    return BoundaryResult("accepted", resolved)
