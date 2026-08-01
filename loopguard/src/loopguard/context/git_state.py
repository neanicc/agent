from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loopguard.context.git_runtime import resolve_git_runtime


@dataclass(frozen=True, slots=True)
class GitStatusEntry:
    path: str
    status: str
    original_path: str | None = None
    before_hash: str | None = None


class GitStateScanner:
    def __init__(self, repository: Path) -> None:
        self.repository = repository

    def scan(self) -> list[GitStatusEntry]:
        if not self.repository.is_dir() or not (self.repository / ".git").exists():
            raise FileNotFoundError("repository is unavailable")
        runtime = resolve_git_runtime()
        if runtime is None:
            raise FileNotFoundError("Git is unavailable")
        try:
            result = subprocess.run(
                [
                    runtime.executable,
                    "-C",
                    str(self.repository),
                    "status",
                    "--porcelain=v2",
                    "-z",
                    "--untracked-files=all",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
                env=runtime.environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FileNotFoundError("Git status reconciliation failed") from exc
        if result.returncode != 0 or len(result.stdout) > 64 * 1024 * 1024:
            raise FileNotFoundError("Git status reconciliation failed")
        entries = _parse_porcelain_v2(result.stdout)
        if len(entries) > 100_000:
            raise ValueError("Git status exceeded the reconciliation entry limit")
        return entries

    def head_sha(self) -> str:
        runtime = resolve_git_runtime()
        if runtime is None:
            raise FileNotFoundError("Git is unavailable")
        try:
            result = subprocess.run(
                [
                    runtime.executable,
                    "-C",
                    str(self.repository),
                    "rev-parse",
                    "--verify",
                    "HEAD",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
                env=runtime.environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FileNotFoundError("Git HEAD lookup failed") from exc
        if result.returncode != 0:
            return "unborn"
        value = result.stdout.decode("ascii", errors="strict").strip()
        if len(value) not in {40, 64}:
            raise ValueError("Git returned invalid HEAD identity")
        return value


def _parse_porcelain_v2(raw: bytes) -> list[GitStatusEntry]:
    items = raw.split(b"\0")
    entries: list[GitStatusEntry] = []
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if not item:
            continue
        prefix = item[:1]
        if prefix == b"1":
            fields = item.split(b" ", 8)
            if len(fields) != 9:
                raise ValueError("Git returned invalid ordinary status")
            entries.append(
                GitStatusEntry(
                    path=os.fsdecode(fields[8]),
                    status=_status(fields[1]),
                    before_hash=_object_id(fields[6]),
                )
            )
        elif prefix == b"2":
            fields = item.split(b" ", 9)
            if len(fields) != 10 or index >= len(items) or not items[index]:
                raise ValueError("Git returned invalid rename status")
            original = os.fsdecode(items[index])
            index += 1
            entries.append(
                GitStatusEntry(
                    path=os.fsdecode(fields[9]),
                    status="renamed",
                    original_path=original,
                    before_hash=_object_id(fields[6]),
                )
            )
        elif prefix == b"?":
            if not item.startswith(b"? "):
                raise ValueError("Git returned invalid untracked status")
            entries.append(GitStatusEntry(path=os.fsdecode(item[2:]), status="created"))
        elif prefix == b"!":
            continue
        elif prefix == b"#":
            continue
        else:
            raise ValueError("Git returned unsupported status metadata")
    return sorted(entries, key=lambda entry: (entry.path, entry.original_path or ""))


def _status(raw: bytes) -> str:
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Git returned invalid status code") from exc
    if "D" in value:
        return "deleted"
    if "A" in value:
        return "created"
    return "modified"


def _object_id(raw: bytes) -> str | None:
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Git returned invalid object identity") from exc
    if set(value) == {"0"}:
        return None
    if len(value) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("Git returned invalid object identity")
    return value
