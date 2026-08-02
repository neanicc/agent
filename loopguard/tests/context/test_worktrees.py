from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

import loopguard.context.worktrees as worktrees_module
from loopguard.context.worktrees import WorktreeAllocationError, WorktreeManager
from loopguard.control.daemon import DaemonServices
from loopguard.control.decisions import ActionTarget, PolicyDecision, TargetKind
from loopguard.control.events import ControlEvent, EventKind, SessionRef

from .conftest import commit_all, make_git_repo, write


def _repository(tmp_path: Path) -> Path:
    repository = make_git_repo(tmp_path / "repo")
    write(repository / "README.md", "base\n")
    commit_all(repository)
    return repository


def test_windows_pid_liveness_uses_a_non_destructive_process_probe(monkeypatch) -> None:
    observed: list[int] = []
    monkeypatch.setattr(
        worktrees_module,
        "_windows_pid_active",
        lambda pid: bool(observed.append(pid)),
    )

    assert worktrees_module._pid_active(42, platform="nt") is False
    assert observed == [42]


def test_concurrent_sessions_receive_different_worktrees(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(root=tmp_path / "worktrees")

    first = manager.allocate(repository, session_id="s1")
    second = manager.allocate(repository, session_id="s2")

    assert first.path != second.path
    assert first.branch == "loopguard/s1"
    assert second.branch == "loopguard/s2"
    assert first.path.is_dir() and second.path.is_dir()
    assert manager.allocate(repository, session_id="s1") == first


def test_allocation_sanitizes_names_recovers_partial_branch_and_validates_base(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    root = tmp_path / "worktrees"
    manager = WorktreeManager(root=root)
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repository), "branch", "loopguard/recovered", head],
        check=True,
    )

    recovered = manager.allocate(repository, session_id="recovered", base_sha=head)
    sanitized = manager.allocate(repository, session_id="../Unsafe Session")

    assert recovered.branch == "loopguard/recovered"
    assert ".." not in sanitized.branch and " " not in sanitized.branch
    assert sanitized.path.resolve().is_relative_to(root.resolve())
    with pytest.raises(ValueError, match="base commit"):
        manager.allocate(repository, session_id="bad-base", base_sha="f" * 40)


def test_allocation_survives_restart_and_recovers_a_resolved_branch_collision(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    root = tmp_path / "worktrees"
    first_manager = WorktreeManager(root=root)
    first = first_manager.allocate(repository, session_id="restart")
    first_manager.close()

    with WorktreeManager(root=root) as reopened:
        assert reopened.allocate(repository, session_id="restart") == first

        old_head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        write(repository / "README.md", "new base\n")
        commit_all(repository, "new base")
        subprocess.run(
            ["git", "-C", str(repository), "branch", "loopguard/collision", old_head],
            check=True,
        )
        with pytest.raises(WorktreeAllocationError, match="allocation failed safely"):
            reopened.allocate(repository, session_id="collision")
        subprocess.run(
            ["git", "-C", str(repository), "branch", "-D", "loopguard/collision"],
            check=True,
            capture_output=True,
        )
        recovered = reopened.allocate(repository, session_id="collision")
        assert recovered.status == "allocated"


def test_dirty_active_and_unmerged_worktrees_are_quarantined(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(root=tmp_path / "worktrees")

    dirty = manager.allocate(repository, session_id="dirty")
    write(dirty.path / "untracked.txt", "review me\n")
    dirty_result = manager.release(repository, session_id="dirty")
    assert dirty_result.status == "quarantined" and dirty.path.exists()

    active = manager.allocate(repository, session_id="active")
    manager.mark_process(repository, "active", os.getpid())
    active_result = manager.release(repository, session_id="active")
    assert active_result.status == "quarantined" and active.path.exists()

    unmerged = manager.allocate(repository, session_id="unmerged")
    write(unmerged.path / "feature.txt", "feature\n")
    subprocess.run(["git", "-C", str(unmerged.path), "add", "feature.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(unmerged.path),
            "-c",
            "user.name=LoopGuard Tests",
            "-c",
            "user.email=tests@loopguard.invalid",
            "commit",
            "--quiet",
            "-m",
            "feature",
        ],
        check=True,
    )
    unmerged_result = manager.release(repository, session_id="unmerged")
    assert unmerged_result.status == "quarantined" and unmerged.path.exists()


def test_clean_merged_worktree_release_is_non_destructive_and_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(root=tmp_path / "worktrees")
    allocation = manager.allocate(repository, session_id="clean")

    released = manager.release(repository, session_id="clean")
    repeated = manager.release(repository, session_id="clean")

    assert released.status == repeated.status == "released"
    assert not allocation.path.exists()


def test_attached_shared_tree_returns_explicit_warn_or_block(tmp_path: Path) -> None:
    manager = WorktreeManager(root=tmp_path / "worktrees")
    manager.attach("repo", "s1", "worktree:primary")
    manager.attach("repo", "s2", "worktree:primary")

    warning = manager.attached_mutation_decision(
        "repo", "s2", "worktree:primary", policy="warn"
    )
    blocked = manager.attached_mutation_decision(
        "repo", "s2", "worktree:primary", policy="block"
    )

    assert warning.action == "warn" and warning.other_session_ids == ["s1"]
    assert blocked.action == "block" and blocked.collision is True
    manager.detach("repo", "s1")
    assert (
        manager.attached_mutation_decision(
            "repo", "s2", "worktree:primary", policy="block"
        ).action
        == "allow"
    )


def test_daemon_applies_configured_attached_collision_policy(tmp_path: Path) -> None:
    manager = WorktreeManager(root=tmp_path / "worktrees")
    manager.attach("repo", "s1", "worktree:primary")
    event = ControlEvent(
        event_id="event-1",
        kind=EventKind.TOOL_CALL,
        source="codex-hooks",
        session=SessionRef(
            host_id="host",
            repo_id="repo",
            session_id="s2",
            worktree_id="worktree:primary",
        ),
        payload={"tool_name": "write"},
    )
    core = PolicyDecision(
        decision_id="core",
        action="allow",
        reason="No loop detected.",
        target=ActionTarget(kind=TargetKind.SESSION, target_id="s2"),
        state_version=1,
        state_hash="state",
    )

    warned = asyncio.run(
        DaemonServices(
            worktree_manager=manager,
            attached_collision_policy="warn",
        ).apply_policy(event, core)
    )
    blocked = asyncio.run(
        DaemonServices(
            worktree_manager=manager,
            attached_collision_policy="block",
        ).apply_policy(event, core)
    )

    assert warned.action == "warn"
    assert blocked.action == "pause"
    assert blocked.metadata["worktree_collision"]["collision"] is True
