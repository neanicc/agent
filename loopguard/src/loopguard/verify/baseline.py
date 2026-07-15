from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import Baseline, CheckPhase, ProofContract

if TYPE_CHECKING:
    from .runner import CheckExecution, ExecutionAuthorization


class BaselineRunner(Protocol):
    async def run(
        self,
        spec,
        *,
        authorization: ExecutionAuthorization,
    ) -> CheckExecution: ...


class BaselineCaptureError(RuntimeError):
    """A repository baseline could not be captured honestly."""


class RepositorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_sha: str
    worktree_hash: str
    dirty_manifest: list[str] = Field(default_factory=list)
    untracked_manifest: list[str] = Field(default_factory=list)


class BaselineService:
    def __init__(
        self,
        runner: BaselineRunner,
        *,
        snapshotter: Callable[[Path], RepositorySnapshot] | None = None,
        authorization_factory: Callable[[str], ExecutionAuthorization] | None = None,
    ) -> None:
        self.runner = runner
        self.snapshotter = snapshotter or capture_repository_snapshot
        if authorization_factory is None:
            from .runner import ExecutionAuthorization

            def deny_unapproved(_check_id: str) -> ExecutionAuthorization:
                return ExecutionAuthorization(approved=False)

            authorization_factory = deny_unapproved
        self.authorization_factory = authorization_factory

    async def capture(
        self,
        contract: ProofContract,
        *,
        repository: Path,
        owning_session_id: str,
        captured_before_first_mutation: bool = True,
    ) -> Baseline:
        captured_at = datetime.now(timezone.utc)
        snapshot = self.snapshotter(repository)
        checks = [
            check
            for check in contract.checks
            if check.phase is CheckPhase.BASELINE
            or (
                check.required
                and check.phase in {CheckPhase.COMPLETION, CheckPhase.PR}
            )
        ]
        results = []
        for check in checks:
            execution = await self.runner.run(
                check,
                authorization=self.authorization_factory(check.id),
            )
            results.append(execution.result)
        final_snapshot = self.snapshotter(repository)
        if final_snapshot.worktree_hash != snapshot.worktree_hash:
            raise BaselineCaptureError("baseline checks mutated the worktree")
        baseline_seed = (
            f"{contract.task_id}\0{contract.source_hash}\0{snapshot.repository_sha}\0"
            f"{snapshot.worktree_hash}\0{captured_at.isoformat()}"
        )
        baseline_id = "baseline:" + hashlib.sha256(baseline_seed.encode()).hexdigest()
        return Baseline(
            baseline_id=baseline_id,
            captured_at=captured_at,
            repository_sha=snapshot.repository_sha,
            worktree_hash=snapshot.worktree_hash,
            dirty_manifest=snapshot.dirty_manifest,
            untracked_manifest=snapshot.untracked_manifest,
            owning_session_id=owning_session_id,
            source_prompt_event_id=contract.source_event_id,
            captured_before_first_mutation=captured_before_first_mutation,
            results=results,
        )


def capture_repository_snapshot(repository: Path) -> RepositorySnapshot:
    try:
        repo = repository.expanduser().resolve(strict=True)
    except OSError as exc:
        raise BaselineCaptureError("repository is unavailable") from exc
    git = shutil.which("git", path=os.defpath)
    if git is None or not (repo / ".git").exists():
        raise BaselineCaptureError("trusted baseline requires a Git worktree")
    environment = {"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"}
    repository_sha = _git_output(
        git,
        repo,
        ["rev-parse", "--verify", "HEAD"],
        environment,
    ).decode().strip()
    if len(repository_sha) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in repository_sha
    ):
        raise BaselineCaptureError("Git returned an invalid repository SHA")
    status = _git_output(
        git,
        repo,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        environment,
    )
    manifest_text = _git_output(
        git,
        repo,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        environment,
    ).decode("utf-8", errors="surrogateescape")
    lines = [line for line in manifest_text.splitlines() if line]
    dirty = [line for line in lines if not line.startswith("?? ")]
    untracked = [line[3:] for line in lines if line.startswith("?? ")]
    digest = hashlib.sha256(
        b"loopguard-git-worktree-v1\0" + repository_sha.encode() + b"\0" + status
    )
    entries = status.split(b"\0")
    for entry in entries:
        if not entry:
            continue
        raw_path = entry[3:] if len(entry) >= 4 and entry[2:3] == b" " else entry
        relative = Path(os.fsdecode(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise BaselineCaptureError("Git status returned an unsafe path")
        path = repo / relative
        digest.update(os.fsencode(relative))
        if path.is_symlink():
            digest.update(b"symlink\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        elif not path.exists():
            digest.update(b"deleted")
    return RepositorySnapshot(
        repository_sha=repository_sha,
        worktree_hash=digest.hexdigest(),
        dirty_manifest=dirty,
        untracked_manifest=untracked,
    )


def _git_output(
    executable: str,
    repository: Path,
    arguments: list[str],
    environment: dict[str, str],
) -> bytes:
    try:
        result = subprocess.run(
            [executable, "-C", str(repository), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineCaptureError("Git baseline command failed") from exc
    if result.returncode != 0:
        raise BaselineCaptureError("Git baseline command failed")
    if len(result.stdout) > 64 * 1024 * 1024:
        raise BaselineCaptureError("Git baseline output exceeded its limit")
    return result.stdout
