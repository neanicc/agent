from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GitRuntime:
    executable: str
    environment: dict[str, str]


def resolve_git_runtime(*, platform: str | None = None) -> GitRuntime | None:
    """Resolve Git while retaining only the environment it needs to run."""
    active_platform = os.name if platform is None else platform
    search_path = (
        os.environ.get("PATH", os.defpath) if active_platform == "nt" else os.defpath
    )
    executable = shutil.which("git", path=search_path)
    if executable is None:
        return None
    environment = {"PATH": search_path, "LC_ALL": "C", "LANG": "C"}
    if active_platform == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
    return GitRuntime(executable=executable, environment=environment)
