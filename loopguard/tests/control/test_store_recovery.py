from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from loopguard.control.crypto import CorruptKeyError, InMemoryKeyStore, IntegrityError, MissingKeyError
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.migrations import MigrationError, UnsupportedMigrationError
from loopguard.control.store import (
    DatabaseCorruptionError,
    EventStore,
    StoreBusyError,
    UnsafePermissionsError,
    WrongKeyError,
)


def _event(event_id: str, *, repo_id: str = "r", session_id: str = "s") -> ControlEvent:
    return ControlEvent(
        event_id=event_id,
        kind=EventKind.SESSION_STARTED,
        source="recovery-test",
        session=SessionRef(host_id="host", repo_id=repo_id, session_id=session_id),
        payload={"private": f"body-{event_id}"},
    )


def test_locked_database_retries_are_bounded_and_leave_sequences_unspent(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore.for_test(
        path,
        busy_timeout_ms=1,
        max_busy_retries=2,
        retry_delay_seconds=0,
    )
    locker = sqlite3.connect(path, isolation_level=None)
    locker.execute("PRAGMA busy_timeout = 1")
    locker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StoreBusyError, match="after 3 attempts"):
            store.append(_event("blocked"))
    finally:
        locker.execute("ROLLBACK")
        locker.close()

    try:
        position = store.append(_event("committed"))
    finally:
        store.close()

    assert position.local_log_seq == 1
    assert position.repo_seq == 1
    assert position.session_seq == 1


def test_abrupt_subprocess_exit_after_commit_replays_acknowledged_event(tmp_path):
    path = tmp_path / "events.db"
    source_root = Path(__file__).parents[2] / "src"
    code = """
import os
import sys
from pathlib import Path
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore

store = EventStore.for_test(Path(sys.argv[1]), key=b"abrupt-exit-key")
store.append(ControlEvent(
    event_id="before-crash",
    kind=EventKind.SESSION_STARTED,
    source="subprocess",
    session=SessionRef(host_id="h", repo_id="r", session_id="s"),
    payload={"private": "committed-before-abrupt-exit"},
))
os._exit(0)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    completed = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        env=env,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0

    with EventStore.for_test(path, key=b"abrupt-exit-key") as reopened:
        replay = reopened.read_local_after(0, 10)

    assert [item.event.event_id for item in replay] == ["before-crash"]
    assert replay[0].event.payload["private"] == "committed-before-abrupt-exit"


def test_corrupt_database_is_a_named_startup_failure(tmp_path):
    path = tmp_path / "events.db"
    with EventStore.for_test(path) as store:
        store.append(_event("durable"))

    with path.open("r+b") as database:
        database.seek(0)
        database.write(b"destroyed sqlite header")

    with pytest.raises(DatabaseCorruptionError):
        EventStore.for_test(path)


def test_corrupt_wal_is_a_named_startup_failure(tmp_path):
    path = tmp_path / "events.db"
    wal_path = path.with_name(f"{path.name}-wal")
    source_root = Path(__file__).parents[2] / "src"
    code = """
import os
import sys
from pathlib import Path
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore

store = EventStore.for_test(Path(sys.argv[1]), key=b"wal-key")
store.append(ControlEvent(
    event_id="wal-event",
    kind=EventKind.SESSION_STARTED,
    source="subprocess",
    session=SessionRef(host_id="h", repo_id="r", session_id="s"),
))
os._exit(0)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    subprocess.run([sys.executable, "-c", code, str(path)], env=env, check=True, timeout=10)
    assert wal_path.exists()
    corrupt_wal = bytearray(wal_path.read_bytes())
    assert len(corrupt_wal) > 80
    corrupt_wal[80] ^= 0xFF
    wal_path.write_bytes(corrupt_wal)
    wal_path.chmod(0o600)

    with pytest.raises(DatabaseCorruptionError):
        EventStore.for_test(path, key=b"wal-key")


def test_missing_key_is_not_silently_regenerated(tmp_path):
    path = tmp_path / "events.db"
    keys = InMemoryKeyStore()
    with EventStore.for_test(path, key_store=keys) as store:
        store.append(_event("durable"))
    key_id = keys.key_ids()[0]
    keys.delete(key_id)

    with pytest.raises(MissingKeyError):
        EventStore.for_test(path, key_store=keys)
    assert keys.key_ids() == []


def test_corrupt_and_wrong_keys_have_distinct_named_failures(tmp_path):
    corrupt_path = tmp_path / "corrupt-key.db"
    keys = InMemoryKeyStore()
    with EventStore.for_test(corrupt_path, key_store=keys):
        pass
    keys.put_raw_for_test(keys.key_ids()[0], b"short")

    with pytest.raises(CorruptKeyError):
        EventStore.for_test(corrupt_path, key_store=keys)

    wrong_path = tmp_path / "wrong-key.db"
    with EventStore.for_test(wrong_path, key=b"right-key"):
        pass
    with pytest.raises(WrongKeyError):
        EventStore.for_test(wrong_path, key=b"wrong-key")


def test_authenticated_cursor_metadata_tampering_fails_during_startup(tmp_path):
    path = tmp_path / "events.db"
    with EventStore.for_test(path, key=b"integrity-key") as store:
        store.append(_event("protected"))

    connection = sqlite3.connect(path)
    connection.execute("UPDATE events SET repo_seq = 99 WHERE local_log_seq = 1")
    connection.commit()
    connection.close()

    with pytest.raises(IntegrityError):
        EventStore.for_test(path, key=b"integrity-key")


def test_sequence_allocator_tampering_fails_during_startup(tmp_path):
    path = tmp_path / "events.db"
    with EventStore.for_test(path, key=b"integrity-key") as store:
        store.append(_event("protected"))

    connection = sqlite3.connect(path)
    connection.execute("UPDATE repo_sequences SET last_seq = 99 WHERE repo_id = 'r'")
    connection.commit()
    connection.close()

    with pytest.raises(IntegrityError, match="cursor sequence metadata"):
        EventStore.for_test(path, key=b"integrity-key")


def test_newer_migration_version_is_rejected_without_modification(tmp_path):
    path = tmp_path / "events.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()
    path.chmod(0o600)
    before = path.read_bytes()

    with pytest.raises(UnsupportedMigrationError):
        EventStore.for_test(path)

    assert path.read_bytes() == before


def test_declared_current_version_with_missing_schema_is_a_migration_failure(tmp_path):
    path = tmp_path / "events.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 1")
    connection.close()
    path.chmod(0o600)

    with pytest.raises(MigrationError, match="schema does not match"):
        EventStore.for_test(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_unsafe_existing_home_or_database_permissions_are_rejected(tmp_path):
    unsafe_home = tmp_path / "unsafe-home"
    unsafe_home.mkdir(mode=0o755)
    with pytest.raises(UnsafePermissionsError, match="directory"):
        EventStore.for_test(unsafe_home / "events.db")

    safe_home = tmp_path / "safe-home"
    safe_home.mkdir(mode=0o700)
    database = safe_home / "events.db"
    with EventStore.for_test(database):
        pass
    database.chmod(0o644)
    with pytest.raises(UnsafePermissionsError, match="database"):
        EventStore.for_test(database)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_new_home_database_and_sidecars_are_owner_only(tmp_path):
    home = tmp_path / "loopguard-home"
    path = home / "events.db"
    with EventStore.for_test(path) as store:
        store.append(_event("permissions"))
        assert path.stat().st_mode & 0o777 == 0o600
        assert home.stat().st_mode & 0o777 == 0o700
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(f"{path.name}{suffix}")
            if sidecar.exists():
                assert sidecar.stat().st_mode & 0o777 == 0o600


def test_restart_preserves_exact_independent_replay_positions(tmp_path):
    path = tmp_path / "events.db"
    with EventStore.for_test(path, key=b"restart-key") as store:
        first = store.append(_event("a1", repo_id="a", session_id="one"))
        store.append(_event("b1", repo_id="b", session_id="one"))
        third = store.append(_event("a2", repo_id="a", session_id="two"))

    with EventStore.for_test(path, key=b"restart-key") as reopened:
        assert reopened.append(_event("a1", repo_id="a", session_id="one")) == first
        repo = reopened.read_repo_after("a", 1, 10)
        session = reopened.read_session_after("one", 0, 10)
        local = reopened.read_local_after(2, 10)

    assert [item.position for item in repo] == [third]
    assert [item.event.event_id for item in session] == ["a1", "b1"]
    assert [item.event.event_id for item in local] == ["a2"]
