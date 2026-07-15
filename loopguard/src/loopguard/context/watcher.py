from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .git_state import GitStateScanner
from .hashing import ContextHasher, FileChangedDuringHash, content_fingerprint
from .models import ChangeObservation, ContextCheckpoint


class PendingChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str
    source_event_id: str
    repo_id: str
    worktree_id: str
    path: str
    original_path: str | None = None
    actor: str
    before_hash: str | None = None
    after_hash: str | None = None
    content_fingerprint: str
    observed_at: AwareDatetime
    recovery_reason: str | None = None
    history_complete: bool = True

    def to_observation(
        self,
        *,
        repo_seq: int,
        reconciliation_id: str | None = None,
    ) -> ChangeObservation:
        return ChangeObservation(
            observation_id=self.observation_id,
            control_event_id=self.source_event_id,
            repo_id=self.repo_id,
            repo_seq=repo_seq,
            worktree_id=self.worktree_id,
            path=self.path,
            actor=self.actor,
            before_hash=self.before_hash,
            after_hash=self.after_hash,
            reconciliation_id=reconciliation_id,
            observation_kind=(
                "reconciliation.current_state"
                if self.recovery_reason is not None
                else "change"
            ),
            history_complete=self.history_complete,
            recovery_reason=self.recovery_reason,
            observed_at=self.observed_at,
        )


class ReconciledChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str
    content_fingerprint: str
    path: str
    before_hash: str | None = None
    after_hash: str | None = None
    provenance: list[PendingChange] = Field(min_length=1, max_length=10_000)


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current_state_recovered", "repository_removed", "unstable"]
    observations: list[PendingChange] = Field(default_factory=list, max_length=100_000)
    history_complete: bool = False
    current_state_hash: str | None = None


@dataclass(frozen=True, slots=True)
class DebouncedEvent:
    path: str
    event_id: str
    observed_at: datetime


class FilesystemDebouncer:
    def __init__(self, *, delay_ms: int = 75) -> None:
        if not 1 <= delay_ms <= 10_000:
            raise ValueError("filesystem debounce must be bounded")
        self.delay = timedelta(milliseconds=delay_ms)
        self._pending: dict[str, DebouncedEvent] = {}

    def push(self, path: str, event_id: str, *, observed_at: datetime) -> None:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("filesystem event time must be timezone-aware")
        self._pending[path] = DebouncedEvent(path, event_id, observed_at)

    def ready(self, now: datetime) -> list[DebouncedEvent]:
        ready = [
            event
            for event in self._pending.values()
            if now - event.observed_at >= self.delay
        ]
        for event in ready:
            self._pending.pop(event.path, None)
        return sorted(ready, key=lambda event: event.path)


class ChangeReconciler:
    def __init__(
        self,
        repository: Path,
        *,
        max_file_bytes: int = 4 * 1024 * 1024,
        case_sensitive: bool | None = None,
        ignored_parts: set[str] | None = None,
        binary_extensions: set[str] | None = None,
    ) -> None:
        self.repository = repository.expanduser().resolve(strict=True)
        self.repo_id, self.worktree_id = _repository_identity(self.repository)
        resolved_case = (
            _detect_case_sensitivity(self.repository)
            if case_sensitive is None
            else case_sensitive
        )
        self.reconciliation_window = timedelta(seconds=5)
        self.hasher = ContextHasher(
            self.repository,
            max_file_bytes=max_file_bytes,
            case_sensitive=resolved_case,
            ignored_parts=ignored_parts,
            binary_extensions=binary_extensions,
        )
        self.scanner = GitStateScanner(self.repository)

    def from_hook(
        self,
        path: str,
        *,
        actor: str,
        event_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> PendingChange | None:
        return self._pending(
            path,
            actor=actor,
            source="hook",
            event_id=event_id,
            observed_at=observed_at,
        )

    def from_filesystem(
        self,
        path: str,
        *,
        event_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> PendingChange | None:
        return self._pending(
            path,
            actor="filesystem",
            source="filesystem",
            event_id=event_id,
            observed_at=observed_at,
        )

    def from_filesystem_rename(
        self,
        original_path: str,
        path: str,
        *,
        event_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> PendingChange | None:
        return self._pending(
            path,
            original_path=original_path,
            actor="filesystem",
            source="filesystem",
            event_id=event_id,
            observed_at=observed_at,
        )

    def merge(self, observations: list[PendingChange | None]) -> ReconciledChange:
        values = [observation for observation in observations if observation is not None]
        if not values:
            raise ValueError("reconciliation requires at least one observation")
        if len({value.content_fingerprint for value in values}) != 1:
            raise ValueError("only the same physical transition can be reconciled")
        if len(values) > 10_000:
            raise ValueError("reconciliation provenance limit exceeded")
        by_id: dict[str, PendingChange] = {}
        for value in values:
            existing = by_id.get(value.observation_id)
            if existing is not None and existing != value:
                raise ValueError("observation ID has conflicting semantics")
            by_id[value.observation_id] = value
        provenance = sorted(by_id.values(), key=lambda value: (value.observed_at, value.observation_id))
        if provenance[-1].observed_at - provenance[0].observed_at > self.reconciliation_window:
            raise ValueError("observations are outside the active reconciliation window")
        first = provenance[0]
        seed = "\0".join(value.observation_id for value in provenance)
        return ReconciledChange(
            record_id="change:" + hashlib.sha256(f"reconciled-v1\0{seed}".encode()).hexdigest(),
            content_fingerprint=first.content_fingerprint,
            path=first.path,
            before_hash=first.before_hash,
            after_hash=first.after_hash,
            provenance=provenance,
        )

    def reconcile_current_state(
        self,
        *,
        reason: Literal["overflow", "restart"],
        checkpoint: ContextCheckpoint | None = None,
    ) -> ReconciliationResult:
        if not self.repository.is_dir() or not (self.repository / ".git").exists():
            return ReconciliationResult(status="repository_removed")
        scan_id = uuid.uuid4().hex
        observations: list[PendingChange] = []
        try:
            for entry in self.scanner.scan():
                pending = self._pending(
                    entry.path,
                    original_path=entry.original_path,
                    actor="reconciliation.current_state",
                    source="reconciliation",
                    event_id=f"{reason}:{scan_id}:{entry.path}",
                    observed_at=None,
                    before_hash_override=entry.before_hash,
                    recovery_reason=reason,
                    history_complete=False,
                )
                if pending is not None:
                    observations.append(pending)
        except (FileChangedDuringHash, ValueError):
            return ReconciliationResult(status="unstable")
        except FileNotFoundError:
            return ReconciliationResult(status="repository_removed")
        state_hash = _current_state_hash(self.scanner.head_sha(), observations)
        if checkpoint is not None:
            if checkpoint.repo_id != self.repo_id:
                return ReconciliationResult(status="unstable", current_state_hash=state_hash)
            if checkpoint.worktree_hash == state_hash:
                observations = []
        return ReconciliationResult(
            status="current_state_recovered",
            observations=observations,
            history_complete=False,
            current_state_hash=state_hash,
        )

    def _pending(
        self,
        path: str,
        *,
        actor: str,
        source: str,
        event_id: str | None,
        observed_at: datetime | None,
        original_path: str | None = None,
        recovery_reason: str | None = None,
        history_complete: bool = True,
        before_hash_override: str | None = None,
    ) -> PendingChange | None:
        transition = self.hasher.transition(
            path,
            original_path=original_path,
            before_hash_override=before_hash_override,
        )
        if transition is None:
            return None
        source_id = event_id or uuid.uuid4().hex
        observation_id = f"{source}:" + hashlib.sha256(
            f"loopguard-context-observation-v1\0{source_id}".encode()
        ).hexdigest()
        fingerprint = content_fingerprint(
            self.repo_id,
            self.worktree_id,
            transition.path,
            transition.before_hash,
            transition.after_hash,
        )
        return PendingChange(
            observation_id=observation_id,
            source_event_id=source_id,
            repo_id=self.repo_id,
            worktree_id=self.worktree_id,
            path=transition.path,
            original_path=transition.original_path,
            actor=actor,
            before_hash=transition.before_hash,
            after_hash=transition.after_hash,
            content_fingerprint=fingerprint,
            observed_at=observed_at or datetime.now(timezone.utc),
            recovery_reason=recovery_reason,
            history_complete=history_complete,
        )


def _repository_identity(repository: Path) -> tuple[str, str]:
    git = shutil.which("git", path=os.defpath)
    if git is None:
        raise ValueError("Git is unavailable")
    try:
        result = subprocess.run(
            [git, "-C", str(repository), "rev-parse", "--git-common-dir", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("repository identity is unavailable") from exc
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 2:
        raise ValueError("repository identity is unavailable")
    common = Path(lines[0])
    if not common.is_absolute():
        common = (repository / common).resolve(strict=True)
    root = Path(lines[1]).resolve(strict=True)
    repo_id = "repo:" + hashlib.sha256(str(common).encode()).hexdigest()
    worktree_id = "worktree:" + hashlib.sha256(str(root).encode()).hexdigest()
    return repo_id, worktree_id


def _current_state_hash(head_sha: str, observations: list[PendingChange]) -> str:
    body = "\n".join(
        sorted(
            f"{item.path}\0{item.before_hash}\0{item.after_hash}"
            for item in observations
        )
    )
    return hashlib.sha256(
        f"loopguard-context-current-state-v1\0{head_sha}\0{body}".encode()
    ).hexdigest()


def _detect_case_sensitivity(repository: Path) -> bool:
    git_metadata = _git_metadata_directory(repository)
    if git_metadata is None:
        return os.path.normcase("A") != os.path.normcase("a")
    token = uuid.uuid4().hex
    lower = git_metadata / f"loopguard-case-probe-{token}"
    upper = git_metadata / f"LOOPGUARD-CASE-PROBE-{token.upper()}"
    try:
        descriptor = os.open(
            lower,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
    except OSError:
        return os.path.normcase("A") != os.path.normcase("a")
    try:
        return not upper.exists()
    finally:
        lower.unlink(missing_ok=True)


def _git_metadata_directory(repository: Path) -> Path | None:
    git = shutil.which("git", path=os.defpath)
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(repository), "rev-parse", "--git-dir"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"},
        )
        if result.returncode != 0:
            return None
        path = Path(result.stdout.strip())
        if not path.is_absolute():
            path = repository / path
        resolved = path.resolve(strict=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return resolved if resolved.is_dir() else None


class RepositoryWatcher:
    """Bounded watchfiles adapter; overflow falls back to full Git reconciliation."""

    def __init__(
        self,
        reconciler: ChangeReconciler,
        *,
        debounce_ms: int = 75,
        max_batch_size: int = 10_000,
    ) -> None:
        if max_batch_size <= 0:
            raise ValueError("watcher batch limit must be positive")
        self.reconciler = reconciler
        self.debounce_ms = debounce_ms
        self.max_batch_size = max_batch_size

    async def watch(self, *, stop_event=None):
        try:
            from watchfiles import awatch
        except ImportError as exc:
            raise RuntimeError("watchfiles context extra is not installed") from exc
        async for changes in awatch(
            self.reconciler.repository,
            debounce=self.debounce_ms,
            stop_event=stop_event,
            recursive=True,
        ):
            reconciled = self.process_batch(changes)
            if isinstance(reconciled, ReconciliationResult):
                yield reconciled
            else:
                for observation in reconciled:
                    yield observation

    def process_batch(
        self,
        changes: set[tuple[object, str]],
    ) -> list[PendingChange] | ReconciliationResult:
        if len(changes) > self.max_batch_size:
            return self.reconciler.reconcile_current_state(reason="overflow")
        batch_id = uuid.uuid4().hex
        observations: list[PendingChange] = []
        for index, (_change, raw_path) in enumerate(sorted(changes, key=lambda item: item[1])):
            try:
                relative = (
                    Path(raw_path)
                    .resolve(strict=False)
                    .relative_to(self.reconciler.repository)
                    .as_posix()
                )
                observation = self.reconciler.from_filesystem(
                    relative,
                    event_id=f"watch:{batch_id}:{index}",
                )
            except (FileChangedDuringHash, FileNotFoundError, ValueError):
                return self.reconciler.reconcile_current_state(reason="overflow")
            if observation is not None:
                observations.append(observation)
        return observations
