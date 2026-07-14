from __future__ import annotations

import sqlite3

import pytest

from loopguard.control.crypto import IntegrityError
from loopguard.control.decisions import ActionTarget, PolicyDecision, TargetKind
from loopguard.control.store import EventStore

from .daemon_test_support import tool_event


def _decision() -> PolicyDecision:
    return PolicyDecision(
        decision_id="decision-1",
        action="allow",
        reason="allowed",
        target=ActionTarget(kind=TargetKind.SESSION, target_id="session"),
        state_version=1,
        state_hash="a" * 64,
    )


@pytest.mark.parametrize("column", ["dispatched_at", "updated_at"])
def test_reopen_rejects_malformed_dispatch_timestamps(tmp_path, column):
    path = tmp_path / "events.db"
    with EventStore.for_test(path) as store:
        position = store.append(tool_event("evt-1"))
        store.record_core_dispatch(position.local_log_seq, _decision())
        store.ensure_handler_deliveries(position.local_log_seq, ("relay",))

    table = "core_dispatch" if column == "dispatched_at" else "handler_dispatch"
    connection = sqlite3.connect(path)
    connection.execute(f"UPDATE {table} SET {column} = 'not-a-timestamp'")
    connection.commit()
    connection.close()

    with pytest.raises(IntegrityError, match="dispatch"):
        EventStore.for_test(path)


def test_reopen_rejects_orphaned_dispatch_foreign_key(tmp_path):
    path = tmp_path / "events.db"
    with EventStore.for_test(path) as store:
        store.append(tool_event("evt-1"))

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        """
        INSERT INTO handler_dispatch(
            handler_name, local_log_seq, status, attempts, last_error_code, updated_at
        ) VALUES ('relay', 999, 'queued', 0, NULL, '2026-07-14T12:00:00+00:00')
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(IntegrityError, match="foreign key"):
        EventStore.for_test(path)


def test_version_one_store_migrates_without_losing_encrypted_events(tmp_path):
    path = tmp_path / "events.db"
    with EventStore.for_test(path) as store:
        store.append(tool_event("evt-before-migration"))

    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE handler_dispatch")
    connection.execute("DROP TABLE core_dispatch")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    with EventStore.for_test(path) as migrated:
        assert migrated.read_local_after(0, 10)[0].event.event_id == "evt-before-migration"
        assert migrated.get_core_decision(1) is None
