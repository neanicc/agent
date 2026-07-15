from __future__ import annotations

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
