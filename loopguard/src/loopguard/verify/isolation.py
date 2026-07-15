from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import IsolationLevel


@dataclass(slots=True)
class IsolationPlan:
    level: IsolationLevel
    command_prefix: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    enforces_memory_limit: bool = False
    enforces_process_limit: bool = False
    _temporary: tempfile.TemporaryDirectory[str] | None = field(
        default=None,
        repr=False,
    )

    def cleanup(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


class IsolationProvider(Protocol):
    def prepare(self, worktree: Path, *, network_allowed: bool) -> IsolationPlan: ...


class UnavailableIsolationProvider:
    def prepare(self, worktree: Path, *, network_allowed: bool) -> IsolationPlan:
        del worktree, network_allowed
        return IsolationPlan(level=IsolationLevel.UNSANDBOXED)


class SandboxExecIsolationProvider:
    """macOS Seatbelt profile with read-only host access and bounded writable roots."""

    executable = Path("/usr/bin/sandbox-exec")

    def prepare(self, worktree: Path, *, network_allowed: bool) -> IsolationPlan:
        if not self.executable.is_file():
            return IsolationPlan(level=IsolationLevel.UNSANDBOXED)
        temporary = tempfile.TemporaryDirectory(prefix="loopguard-verify-")
        scratch = Path(temporary.name).resolve(strict=True)
        profile = scratch / "profile.sb"
        clauses = [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow signal)",
            "(allow sysctl-read)",
            "(allow ipc-posix*)",
            "(allow file-read*)",
            f'(deny file-read* (subpath "{_seatbelt_escape(Path.home() / ".ssh")}"))',
            f'(deny file-read* (subpath "{_seatbelt_escape(Path.home() / ".aws")}"))',
            f'(deny file-read* (subpath "{_seatbelt_escape(Path.home() / ".config" / "gcloud")}"))',
            f'(deny file-read* (subpath "{_seatbelt_escape(Path.home() / "Library" / "Keychains")}"))',
            f'(allow file-write* (subpath "{_seatbelt_escape(worktree)}"))',
            f'(allow file-write* (subpath "{_seatbelt_escape(scratch)}"))',
            '(allow file-write-data (literal "/dev/null"))',
        ]
        if network_allowed:
            clauses.append("(allow network*)")
        profile.write_text("\n".join(clauses) + "\n")
        profile.chmod(0o600)
        if not _profile_is_enforceable(self.executable, profile):
            temporary.cleanup()
            return IsolationPlan(level=IsolationLevel.UNSANDBOXED)
        return IsolationPlan(
            level=IsolationLevel.SANDBOXED,
            command_prefix=(str(self.executable), "-f", str(profile), "--"),
            environment={"TMPDIR": str(scratch)},
            _temporary=temporary,
        )


class AutoIsolationProvider:
    def __init__(self) -> None:
        self._provider: IsolationProvider
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            self._provider = SandboxExecIsolationProvider()
        else:
            self._provider = UnavailableIsolationProvider()

    def prepare(self, worktree: Path, *, network_allowed: bool) -> IsolationPlan:
        return self._provider.prepare(worktree, network_allowed=network_allowed)


def _seatbelt_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _profile_is_enforceable(executable: Path, profile: Path) -> bool:
    try:
        result = subprocess.run(
            [str(executable), "-f", str(profile), "--", "/usr/bin/true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
