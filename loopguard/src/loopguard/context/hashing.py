from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_IGNORED_PARTS = {
    ".git",
    ".loopguard",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_BINARY_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".dmg",
    ".dll",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".webp",
    ".zip",
}


class FileChangedDuringHash(RuntimeError):
    """A stable content identity could not be captured for a changing file."""


@dataclass(frozen=True, slots=True)
class FileTransition:
    path: str
    before_hash: str | None
    after_hash: str | None
    original_path: str | None = None
    tracked: bool = False


class ContextHasher:
    def __init__(
        self,
        repository: Path,
        *,
        max_file_bytes: int = 4 * 1024 * 1024,
        case_sensitive: bool = True,
        ignored_parts: set[str] | None = None,
        binary_extensions: set[str] | None = None,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("context file limit must be positive")
        self.repository = repository
        self.max_file_bytes = max_file_bytes
        self.case_sensitive = case_sensitive
        self.ignored_parts = _IGNORED_PARTS | set(ignored_parts or set())
        self.binary_extensions = _BINARY_EXTENSIONS | {
            suffix.lower() for suffix in (binary_extensions or set())
        }

    def transition(
        self,
        path: str,
        *,
        original_path: str | None = None,
        before_hash_override: str | None = None,
    ) -> FileTransition | None:
        repo = self._repository_root()
        canonical = self.canonical_path(path)
        original = self.canonical_path(original_path) if original_path is not None else None
        if self._ignored(canonical) or (original is not None and self._ignored(original)):
            return None
        before_path = original or canonical
        before_hash = before_hash_override or self._head_blob(before_path)
        tracked = before_hash is not None or self._index_blob(canonical) is not None
        candidate = repo.joinpath(*PurePosixPath(canonical).parts)
        if candidate.is_symlink():
            return None
        if not candidate.exists():
            after_hash = None
        elif not candidate.is_file():
            return None
        else:
            data = self._stable_read(candidate)
            if data is None:
                return None
            after_hash = (
                self._git_blob_hash(data)
                if tracked
                else hashlib.sha256(data).hexdigest()
            )
        if before_hash == after_hash and original is None:
            return None
        return FileTransition(
            path=canonical,
            before_hash=before_hash,
            after_hash=after_hash,
            original_path=original,
            tracked=tracked,
        )

    def canonical_path(self, value: str) -> str:
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise ValueError("context path must be repository-relative POSIX form")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("context path escapes the repository")
        normalized = path.as_posix()
        if self.case_sensitive:
            return normalized
        tracked = self._tracked_paths()
        canonical = {item.casefold(): item for item in tracked}.get(normalized.casefold())
        if canonical is not None:
            return canonical
        return normalized.casefold()

    def _repository_root(self) -> Path:
        try:
            repo = self.repository.resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError("repository is unavailable") from exc
        if not repo.is_dir() or not (repo / ".git").exists():
            raise FileNotFoundError("repository is unavailable")
        return repo

    def _ignored(self, path: str) -> bool:
        pure = PurePosixPath(path)
        return (
            any(part.lower() in self.ignored_parts for part in pure.parts)
            or pure.suffix.lower() in self.binary_extensions
        )

    def _tracked_paths(self) -> list[str]:
        output = self._git(["ls-files", "-z"])
        return [os.fsdecode(item) for item in output.split(b"\0") if item]

    def _index_blob(self, path: str) -> str | None:
        output = self._git(["ls-files", "--stage", "-z", "--", path])
        entry = next((item for item in output.split(b"\0") if item), None)
        if entry is None:
            return None
        try:
            metadata, _raw_path = entry.split(b"\t", 1)
            _mode, object_id, stage = metadata.split(b" ", 2)
            decoded = object_id.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Git returned invalid index metadata") from exc
        if stage != b"0" or len(decoded) not in {40, 64}:
            raise ValueError("Git index entry is not a stable stage-zero blob")
        return decoded

    def _head_blob(self, path: str) -> str | None:
        git = shutil.which("git", path=os.defpath)
        if git is None:
            raise FileNotFoundError("Git is unavailable")
        try:
            result = subprocess.run(
                [git, "-C", str(self.repository), "rev-parse", "--verify", f"HEAD:{path}"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
                env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FileNotFoundError("Git object lookup failed") from exc
        if result.returncode != 0:
            return None
        value = result.stdout.decode("ascii", errors="strict").strip()
        if len(value) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Git returned invalid HEAD object identity")
        return value

    def _git_blob_hash(self, data: bytes) -> str:
        value = self._git(["hash-object", "--stdin"], input_bytes=data).decode().strip()
        if len(value) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Git returned invalid blob identity")
        return value

    def _stable_read(self, path: Path) -> bytes | None:
        before = path.stat(follow_symlinks=False)
        if before.st_size > self.max_file_bytes:
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise FileChangedDuringHash("file became unavailable during hashing") from exc
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            remaining = self.max_file_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if len(data) > self.max_file_bytes:
            return None
        identity_before = (before.st_ino, before.st_size, before.st_mtime_ns)
        identity_opened = (opened.st_ino, opened.st_size, opened.st_mtime_ns)
        identity_after = (after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_opened or identity_opened != identity_after:
            raise FileChangedDuringHash("file changed during hashing")
        try:
            final_path = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise FileChangedDuringHash("file changed during hashing") from exc
        identity_path = (final_path.st_ino, final_path.st_size, final_path.st_mtime_ns)
        if identity_path != identity_after:
            raise FileChangedDuringHash("file was replaced during hashing")
        if b"\x00" in data[:8192]:
            return None
        return data

    def _git(self, arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
        git = shutil.which("git", path=os.defpath)
        if git is None:
            raise FileNotFoundError("Git is unavailable")
        try:
            result = subprocess.run(
                [git, "-C", str(self.repository), *arguments],
                input=input_bytes,
                stdin=None if input_bytes is not None else subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
                env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FileNotFoundError("Git context command failed") from exc
        if result.returncode != 0 or len(result.stdout) > 64 * 1024 * 1024:
            raise FileNotFoundError("Git context command failed")
        return result.stdout


def content_fingerprint(
    repo_id: str,
    worktree_id: str,
    path: str,
    before_hash: str | None,
    after_hash: str | None,
) -> str:
    body = f"{repo_id}\0{worktree_id}\0{path}\0{before_hash}\0{after_hash}"
    return hashlib.sha256(body.encode()).hexdigest()
