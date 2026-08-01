from __future__ import annotations

import asyncio
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from loopguard.context.journal import (
    ChangeJournal,
    ChangeJournalError,
    ObservationConflictError,
)
from loopguard.context.models import ChangeObservation, ContextCheckpoint
from loopguard.adapters.normalize_hook import RepositoryIdentity, normalize_hook
from loopguard.control.dispatch import EventDispatcher
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.lifecycle import production_handlers
from loopguard.control.store import EventStore

from tests.context.conftest import observation


def test_journal_orders_changes_and_preserves_actor(tmp_path) -> None:
    journal = ChangeJournal(tmp_path / "context.db")
    first = journal.record(
        observation("obs-1", repo_seq=1, path="src/a.py", actor="codex:s1")
    )
    second = journal.record(
        observation("obs-2", repo_seq=2, path="src/b.py", actor="claude:s2")
    )

    assert first.repo_seq == 1 and second.repo_seq == 2
    assert journal.since("repo", repo_seq=1)[0].actor == "claude:s2"
    assert journal.latest_cursor("repo") == 2


def test_observation_replay_is_idempotent_but_conflicting_reuse_is_rejected(tmp_path) -> None:
    journal = ChangeJournal(tmp_path / "context.db")
    original = observation("obs-1", repo_seq=1)

    first = journal.record(original)
    replay = journal.record(original)

    assert replay == first
    assert journal.count("repo") == 1
    with pytest.raises(ObservationConflictError, match="different semantics"):
        journal.record(original.model_copy(update={"after_hash": "different"}))


def test_only_same_explicit_reconciliation_window_merges_provenance(tmp_path) -> None:
    journal = ChangeJournal(tmp_path / "context.db")
    first = journal.record(
        observation(
            "hook",
            repo_seq=1,
            actor="codex:s1",
            reconciliation_id="window-1",
        )
    )
    merged = journal.record(
        observation(
            "filesystem",
            repo_seq=2,
            actor="filesystem",
            reconciliation_id="window-1",
        )
    )
    repeated_later = journal.record(
        observation("later", repo_seq=3, actor="codex:s1")
    )

    assert merged.record_id == first.record_id
    assert merged.repo_seq == 2
    assert {item.actor for item in merged.provenance} == {"codex:s1", "filesystem"}
    assert repeated_later.record_id != first.record_id
    assert journal.count("repo") == 2


def test_reused_reconciliation_id_outside_active_window_starts_new_history(
    tmp_path,
) -> None:
    journal = ChangeJournal(tmp_path / "context.db", reconciliation_window_seconds=1)
    first = observation("first", repo_seq=1, reconciliation_id="window")
    later = observation("later", repo_seq=2, reconciliation_id="window").model_copy(
        update={"observed_at": first.observed_at + timedelta(seconds=2)}
    )

    original = journal.record(first)
    repeated = journal.record(later)

    assert repeated.record_id != original.record_id
    assert journal.count("repo") == 2


def test_models_reject_unsafe_paths_unbounded_payloads_and_unknown_fields() -> None:
    valid = observation("obs", repo_seq=1)
    with pytest.raises(ValidationError):
        ChangeObservation.model_validate(
            {**valid.model_dump(mode="json"), "path": "../outside.py"}
        )
    with pytest.raises(ValidationError):
        ChangeObservation.model_validate(
            {**valid.model_dump(mode="json"), "patch": "x" * 1_000_001}
        )
    with pytest.raises(ValidationError):
        ChangeObservation.model_validate(
            {**valid.model_dump(mode="json"), "invented": True}
        )


def test_native_file_change_is_normalized_to_repository_relative_path(tmp_path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    normalized = normalize_hook(
        "claude",
        "FileChanged",
        {
            "session_id": "vendor-session",
            "cwd": str(repository),
            "file_path": str(repository / "src" / "app.py"),
            "event": "change",
        },
        identity_resolver=lambda _cwd: RepositoryIdentity(
            root=repository,
            repo_id="repo",
            worktree_id="worktree",
        ),
        host_id="host",
    )

    assert normalized.event.payload["path"] == "src/app.py"


def test_database_is_private_and_symlink_paths_are_rejected(tmp_path) -> None:
    path = tmp_path / "context.db"
    journal = ChangeJournal(path)
    journal.record(observation("obs", repo_seq=1))
    journal.close()

    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    link = tmp_path / "linked.db"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        ChangeJournal(link)


def test_checkpoint_round_trip_is_cursor_ordered_and_immutable(tmp_path) -> None:
    journal = ChangeJournal(tmp_path / "context.db")
    first = ContextCheckpoint(
        repo_id="repo",
        repo_seq=1,
        commit_sha="a" * 40,
        worktree_hash="b" * 64,
    )
    latest = ContextCheckpoint(
        repo_id="repo",
        repo_seq=3,
        commit_sha="c" * 40,
        worktree_hash="d" * 64,
    )

    journal.save_checkpoint(latest)
    journal.save_checkpoint(first)

    assert journal.latest_checkpoint("repo") == latest
    assert journal.save_checkpoint(first) == first
    with pytest.raises(ChangeJournalError, match="different semantics"):
        journal.save_checkpoint(first.model_copy(update={"worktree_hash": "e" * 64}))


def test_file_changed_handler_consumes_event_store_cursor_once_after_crash_replay(
    tmp_path,
) -> None:
    async def scenario():
        store = EventStore.for_test(tmp_path / "events.db")
        journal = ChangeJournal(tmp_path / "context.db")
        event = ControlEvent(
            event_id="file-event",
            kind=EventKind.FILE_CHANGED,
            source="claude-hooks",
            session=SessionRef(
                host_id="host",
                repo_id="repo",
                session_id="session",
                worktree_id="worktree",
            ),
            payload={
                "agent": "claude-code",
                "path": "src/app.py",
                "before_hash": "old",
                "after_hash": "new",
            },
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        position = store.append(event)
        registry_dispatcher = EventDispatcher(
            store=store,
            handlers=production_handlers(journal),
            handler_retry_delay=0,
        )
        await registry_dispatcher.start()
        await registry_dispatcher.wait_for_handlers()
        await registry_dispatcher.close()
        assert store.handler_statuses(position.local_log_seq)["context"]["status"] == "succeeded"

        # Recreate an interrupted delivery marker and prove handler replay stays idempotent.
        store._connection.execute(
            "UPDATE handler_dispatch SET status = 'running' WHERE handler_name = 'context'"
        )
        restarted = EventDispatcher(
            store=store,
            handlers=production_handlers(journal),
            handler_retry_delay=0,
        )
        await restarted.start()
        await restarted.wait_for_handlers()
        await restarted.close()
        records = journal.since("repo", repo_seq=0)
        journal.close()
        store.close()
        return records

    records = asyncio.run(scenario())

    assert len(records) == 1
    assert records[0].repo_seq == 1
    assert records[0].provenance[0].control_event_id == "file-event"
