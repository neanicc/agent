from __future__ import annotations

import subprocess
from pathlib import Path

from loopguard.context.models import ChangeObservation


def observation(
    observation_id: str,
    *,
    repo_seq: int,
    path: str = "src/app.py",
    actor: str = "codex:session-1",
    before_hash: str | None = "old",
    after_hash: str | None = "new",
    reconciliation_id: str | None = None,
) -> ChangeObservation:
    return ChangeObservation(
        observation_id=observation_id,
        control_event_id=f"event-{observation_id}",
        repo_id="repo",
        repo_seq=repo_seq,
        worktree_id="worktree",
        path=path,
        actor=actor,
        before_hash=before_hash,
        after_hash=after_hash,
        reconciliation_id=reconciliation_id,
    )


def make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "LoopGuard Tests"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "tests@loopguard.invalid"],
        check=True,
    )
    return path.resolve()


def commit_all(repository: Path, message: str = "checkpoint") -> None:
    subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "--quiet", "-m", message],
        check=True,
    )


def write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
