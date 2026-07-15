from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

from loopguard.context.hashing import FileChangedDuringHash
from loopguard.context.journal import ChangeJournal
from loopguard.context.models import ContextCheckpoint
from loopguard.context.watcher import (
    ChangeReconciler,
    FilesystemDebouncer,
    ReconciliationResult,
    RepositoryWatcher,
)
from tests.context.conftest import commit_all, make_git_repo, write


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _tracked_repo(tmp_path):
    repository = make_git_repo(tmp_path / "repo")
    write(repository / "src/a.py", "value = 1\n")
    commit_all(repository)
    return repository


def test_hook_and_filesystem_event_become_one_change(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository)
    write(repository / "src/a.py", "value = 2\n")

    hook = reconciler.from_hook("src/a.py", actor="codex:s1", event_id="hook-1")
    filesystem = reconciler.from_filesystem("src/a.py", event_id="fs-1")
    record = reconciler.merge([hook, filesystem])

    assert hook is not None and filesystem is not None
    assert hook.observation_id != filesystem.observation_id
    assert hook.content_fingerprint == filesystem.content_fingerprint
    assert {item.actor for item in record.provenance} == {"codex:s1", "filesystem"}

    journal = ChangeJournal(tmp_path / "context.db")
    persisted_hook = journal.record(
        hook.to_observation(repo_seq=1, reconciliation_id=record.record_id)
    )
    persisted = journal.record(
        filesystem.to_observation(repo_seq=2, reconciliation_id=record.record_id)
    )
    assert persisted.record_id == persisted_hook.record_id
    assert persisted.content_fingerprint == hook.content_fingerprint


def test_create_delete_and_rename_preserve_exact_transition(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository)
    write(repository / "src/new.py", "new = True\n")
    created = reconciler.from_filesystem("src/new.py", event_id="create")
    (repository / "src/a.py").unlink()
    deleted = reconciler.from_filesystem("src/a.py", event_id="delete")
    write(repository / "src/old.py", "renamed = True\n")
    commit_all(repository, "rename base")
    (repository / "src/old.py").rename(repository / "src/renamed.py")
    renamed = reconciler.from_filesystem_rename(
        "src/old.py",
        "src/renamed.py",
        event_id="rename",
    )

    assert created is not None and created.before_hash is None and created.after_hash
    assert deleted is not None and deleted.before_hash and deleted.after_hash is None
    assert renamed is not None
    assert renamed.original_path == "src/old.py"
    assert renamed.before_hash and renamed.after_hash


def test_repeated_identical_transition_at_different_times_keeps_history(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository)
    write(repository / "src/a.py", "value = 2\n")

    first = reconciler.from_hook("src/a.py", actor="codex:s1", event_id="one")
    second = reconciler.from_hook("src/a.py", actor="codex:s1", event_id="two")
    first_record = reconciler.merge([first])
    second_record = reconciler.merge([second])

    assert first is not None and second is not None
    assert first.content_fingerprint == second.content_fingerprint
    assert first_record.record_id != second_record.record_id


def test_binary_build_output_oversized_files_and_symlinks_are_ignored(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository, max_file_bytes=8)
    write(repository / "image.bin", b"\x00binary")
    write(repository / "dist/bundle.js", "ignored")
    write(repository / "large.txt", "x" * 9)
    link = repository / "linked.py"
    link.symlink_to(repository / "src/a.py")

    assert reconciler.from_filesystem("image.bin", event_id="binary") is None
    assert reconciler.from_filesystem("dist/bundle.js", event_id="build") is None
    assert reconciler.from_filesystem("large.txt", event_id="large") is None
    assert reconciler.from_filesystem("linked.py", event_id="link") is None


def test_overflow_and_restart_report_current_state_not_complete_history(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    write(repository / "src/a.py", "value = 3\n")
    reconciler = ChangeReconciler(repository)

    overflow = reconciler.reconcile_current_state(reason="overflow")
    restart = reconciler.reconcile_current_state(reason="restart")

    assert overflow.status == restart.status == "current_state_recovered"
    assert overflow.history_complete is restart.history_complete is False
    assert overflow.observations[0].actor == "reconciliation.current_state"
    assert overflow.observations[0].recovery_reason == "overflow"
    assert overflow.current_state_hash
    checkpoint = ContextCheckpoint(
        repo_id=reconciler.repo_id,
        repo_seq=1,
        worktree_hash=overflow.current_state_hash,
    )
    unchanged_restart = reconciler.reconcile_current_state(
        reason="restart",
        checkpoint=checkpoint,
    )
    assert unchanged_restart.observations == []


def test_atomic_save_rename_is_reported_as_target_modification(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository)
    temporary = repository / "src/.a.py.tmp"
    write(temporary, "value = 4\n")
    os.replace(temporary, repository / "src/a.py")

    observation = reconciler.from_filesystem("src/a.py", event_id="atomic-save")

    assert observation is not None
    assert observation.path == "src/a.py"
    assert observation.before_hash != observation.after_hash


def test_staged_git_rename_reconciliation_keeps_head_blob_as_before_hash(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository)
    os.rename(repository / "src/a.py", repository / "src/renamed.py")
    subprocess.run(
        ["git", "-C", str(repository), "add", "-A"],
        check=True,
    )

    result = reconciler.reconcile_current_state(reason="overflow")

    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.original_path == "src/a.py"
    assert observation.path == "src/renamed.py"
    assert observation.before_hash == observation.after_hash


def test_case_folded_paths_share_canonical_repository_identity(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository, case_sensitive=False)
    write(repository / "src/a.py", "value = 5\n")

    lower = reconciler.from_hook("src/a.py", actor="codex:s1", event_id="lower")
    upper = reconciler.from_filesystem("SRC/A.PY", event_id="upper")

    assert lower is not None and upper is not None
    assert lower.path == upper.path == "src/a.py"
    assert lower.content_fingerprint == upper.content_fingerprint


def test_repository_removal_is_explicit_and_never_claims_recovery(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    reconciler = ChangeReconciler(repository)
    shutil.rmtree(repository)

    result = reconciler.reconcile_current_state(reason="restart")

    assert result.status == "repository_removed"
    assert result.observations == []
    assert result.history_complete is False


def test_file_changing_during_reconciliation_is_inconclusive(tmp_path, monkeypatch) -> None:
    repository = _tracked_repo(tmp_path)
    write(repository / "src/a.py", "value = 6\n")
    reconciler = ChangeReconciler(repository)

    def unstable(*args, **kwargs):
        raise FileChangedDuringHash("changed during read")

    monkeypatch.setattr(reconciler.hasher, "transition", unstable)
    result = reconciler.reconcile_current_state(reason="overflow")

    assert result.status == "unstable"
    assert result.observations == []
    assert result.history_complete is False


def test_filesystem_debounce_coalesces_only_matching_path_window() -> None:
    debounce = FilesystemDebouncer(delay_ms=75)
    debounce.push("src/a.py", "one", observed_at=NOW)
    debounce.push("src/a.py", "two", observed_at=NOW + timedelta(milliseconds=50))
    debounce.push("src/b.py", "three", observed_at=NOW + timedelta(milliseconds=50))

    assert debounce.ready(NOW + timedelta(milliseconds=100)) == []
    ready = debounce.ready(NOW + timedelta(milliseconds=126))

    assert [(item.path, item.event_id) for item in ready] == [
        ("src/a.py", "two"),
        ("src/b.py", "three"),
    ]


def test_watcher_batch_overflow_uses_full_reconciliation(tmp_path) -> None:
    repository = _tracked_repo(tmp_path)
    write(repository / "src/a.py", "value = 7\n")
    watcher = RepositoryWatcher(ChangeReconciler(repository), max_batch_size=1)

    result = watcher.process_batch(
        {
            (object(), str(repository / "src/a.py")),
            (object(), str(repository / "src/b.py")),
        }
    )

    assert isinstance(result, ReconciliationResult)
    assert result.status == "current_state_recovered"
    assert result.history_complete is False
