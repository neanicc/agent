from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from loopguard.control import store as store_module
from loopguard.control import migrations as migrations_module
from loopguard.control.crypto import CorruptKeyError, InMemoryKeyStore, IntegrityError, MissingKeyError
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.migrations import MigrationError, UnsupportedMigrationError
from loopguard.control.store import (
    DatabaseCorruptionError,
    EventStore,
    OrphanedSidecarError,
    StoreBusyError,
    StoreConfigurationError,
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


def _create_version_one_schema(
    path: Path,
    *,
    ciphertext_declaration: str = "BLOB NOT NULL",
    repo_check: bool = True,
    replay_session_index: bool = True,
    event_id_uniqueness: str = "constraint",
    comment_substitution: str | None = None,
    quoted_token_substitution: str | None = None,
) -> None:
    event_id_constraint = "UNIQUE(event_id)," if event_id_uniqueness == "constraint" else ""
    unique_substitute = ""
    if event_id_uniqueness == "partial":
        unique_substitute = (
            "CREATE UNIQUE INDEX event_id_partial ON events(event_id) WHERE event_id <> '';"
        )
    elif event_id_uniqueness == "substitute":
        unique_substitute = (
            "CREATE UNIQUE INDEX event_id_substitute ON events(event_id, repo_id);"
        )
    session_index = (
        "CREATE INDEX events_session_replay ON events(session_id, session_seq);"
        if replay_session_index
        else ""
    )
    singleton_declaration = "INTEGER PRIMARY KEY CHECK (singleton = 1)"
    repo_last_seq_declaration = (
        "INTEGER NOT NULL CHECK (last_seq > 0)" if repo_check else "INTEGER NOT NULL"
    )
    session_last_seq_declaration = "INTEGER NOT NULL CHECK (last_seq > 0)"
    local_log_declaration = "INTEGER PRIMARY KEY AUTOINCREMENT"
    if comment_substitution == "store_metadata_check":
        singleton_declaration = "INTEGER PRIMARY KEY /* CHECK (singleton = 1) */"
    elif comment_substitution == "repo_sequence_check":
        repo_last_seq_declaration = "INTEGER NOT NULL -- CHECK (last_seq > 0)\n"
    elif comment_substitution == "session_sequence_check":
        session_last_seq_declaration = "INTEGER NOT NULL /* CHECK (last_seq > 0) */"
    elif comment_substitution == "events_autoincrement":
        local_log_declaration = (
            "INTEGER PRIMARY KEY -- local_log_seq INTEGER PRIMARY KEY AUTOINCREMENT\n"
        )
    if quoted_token_substitution == "store_metadata_check":
        singleton_declaration = 'INTEGER CONSTRAINT "CHECK(singleton=1)" PRIMARY KEY'
    elif quoted_token_substitution == "repo_sequence_check":
        repo_last_seq_declaration = (
            'INTEGER NOT NULL CONSTRAINT "CHECK(last_seq>0)" CHECK(last_seq >= 0)'
        )
    elif quoted_token_substitution == "session_sequence_check":
        session_last_seq_declaration = (
            'INTEGER NOT NULL CONSTRAINT "CHECK(last_seq>0)" CHECK(last_seq >= 0)'
        )
    elif quoted_token_substitution == "events_autoincrement":
        local_log_declaration = (
            'INTEGER CONSTRAINT "local_log_seq INTEGER PRIMARY KEY AUTOINCREMENT" PRIMARY KEY'
        )
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""
        CREATE TABLE store_metadata (
            singleton {singleton_declaration},
            key_id TEXT NOT NULL
        );
        CREATE TABLE repo_sequences (
            repo_id TEXT PRIMARY KEY,
            last_seq {repo_last_seq_declaration}
        );
        CREATE TABLE session_sequences (
            session_id TEXT PRIMARY KEY,
            last_seq {session_last_seq_declaration}
        );
        CREATE TABLE events (
            local_log_seq {local_log_declaration},
            event_id TEXT NOT NULL,
            repo_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            repo_seq INTEGER NOT NULL,
            session_seq INTEGER NOT NULL,
            schema_version INTEGER NOT NULL,
            key_id TEXT NOT NULL,
            nonce BLOB NOT NULL,
            ciphertext {ciphertext_declaration},
            created_at TEXT NOT NULL,
            {event_id_constraint}
            UNIQUE(repo_id, repo_seq),
            UNIQUE(session_id, session_seq),
            FOREIGN KEY(repo_id) REFERENCES repo_sequences(repo_id),
            FOREIGN KEY(session_id) REFERENCES session_sequences(session_id)
        );
        CREATE INDEX events_repo_replay ON events(repo_id, repo_seq);
        {session_index}
        {unique_substitute}
        PRAGMA user_version = 1;
        """
    )
    connection.close()
    path.chmod(0o600)


class _MismatchedPragmaConnection(sqlite3.Connection):
    mismatch: str

    def execute(self, sql, parameters=(), /):
        normalized = " ".join(sql.lower().split())
        if normalized == f"pragma {self.mismatch}":
            mismatched_values = {
                "foreign_keys": 0,
                "journal_mode": "delete",
                "synchronous": 1,
                "busy_timeout": 123,
            }
            return super().execute("SELECT ?", (mismatched_values[self.mismatch],))
        return super().execute(sql, parameters)


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


def test_acknowledged_wal_with_deleted_main_database_is_preserved_and_rejected(tmp_path):
    path = tmp_path / "events.db"
    wal_path = path.with_name(f"{path.name}-wal")
    source_root = Path(__file__).parents[2] / "src"
    code = """
import os
import sys
from pathlib import Path
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore

store = EventStore.for_test(Path(sys.argv[1]), key=b"orphaned-wal-key")
store.append(ControlEvent(
    event_id="acknowledged-before-main-loss",
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
    wal_before = wal_path.read_bytes()
    path.unlink()

    with pytest.raises(OrphanedSidecarError):
        EventStore.for_test(path, key=b"orphaned-wal-key")

    assert not path.exists()
    assert wal_path.read_bytes() == wal_before


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_orphaned_sidecar_is_rejected_before_main_database_creation(tmp_path, suffix):
    path = tmp_path / "events.db"
    sidecar = path.with_name(f"{path.name}{suffix}")
    sidecar.write_bytes(b"preserve-orphaned-sidecar")
    sidecar.chmod(0o600)
    before = sidecar.read_bytes()

    with pytest.raises(OrphanedSidecarError):
        EventStore.for_test(path, key=b"orphaned-sidecar-key")

    assert not path.exists()
    assert sidecar.read_bytes() == before


def test_acknowledged_event_recovers_with_incomplete_trailing_wal_frame(tmp_path):
    path = tmp_path / "events.db"
    wal_path = path.with_name(f"{path.name}-wal")
    source_root = Path(__file__).parents[2] / "src"
    code = """
import os
import sys
from pathlib import Path
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore

store = EventStore.for_test(Path(sys.argv[1]), key=b"incomplete-tail-key")
store.append(ControlEvent(
    event_id="acknowledged-before-torn-tail",
    kind=EventKind.SESSION_STARTED,
    source="subprocess",
    session=SessionRef(host_id="h", repo_id="r", session_id="s"),
))
os._exit(0)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    subprocess.run([sys.executable, "-c", code, str(path)], env=env, check=True, timeout=10)
    with wal_path.open("ab") as wal:
        wal.write(b"incomplete-frame-tail")

    with EventStore.for_test(path, key=b"incomplete-tail-key") as reopened:
        replay = reopened.read_local_after(0, 10)

    assert [item.event.event_id for item in replay] == ["acknowledged-before-torn-tail"]


def test_nonempty_truncated_wal_header_fails_closed_and_preserves_state(tmp_path):
    path = tmp_path / "events.db"
    wal_path = path.with_name(f"{path.name}-wal")
    source_root = Path(__file__).parents[2] / "src"
    with EventStore.for_test(path, key=b"truncated-header-key") as store:
        store.append(_event("older-checkpointed-event"))

    code = """
import os
import sys
from pathlib import Path
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore

store = EventStore.for_test(Path(sys.argv[1]), key=b"truncated-header-key")
store.append(ControlEvent(
    event_id="newly-acknowledged-wal-event",
    kind=EventKind.SESSION_STARTED,
    source="subprocess",
    session=SessionRef(host_id="h", repo_id="r", session_id="s"),
))
os._exit(0)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    subprocess.run([sys.executable, "-c", code, str(path)], env=env, check=True, timeout=10)
    wal_contents = wal_path.read_bytes()
    assert len(wal_contents) > 32
    wal_path.write_bytes(wal_contents[:16])
    wal_path.chmod(0o600)
    preserved = {
        candidate: candidate.read_bytes()
        for candidate in (path, wal_path, path.with_name(f"{path.name}-shm"))
        if candidate.exists()
    }

    with pytest.raises(DatabaseCorruptionError, match="WAL header is truncated"):
        with EventStore.for_test(path, key=b"truncated-header-key"):
            pass

    assert {
        candidate: candidate.read_bytes()
        for candidate in preserved
        if candidate.exists()
    } == preserved


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


@pytest.mark.parametrize(
    "malformed_nonce",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"x" * 13, id="oversized"),
        pytest.param("not-a-blob", id="text"),
        pytest.param(12345, id="integer"),
    ],
)
def test_malformed_stored_nonce_is_normalized_to_integrity_error(tmp_path, malformed_nonce):
    path = tmp_path / "events.db"
    with EventStore.for_test(path, key=b"integrity-key") as store:
        store.append(_event("protected"))

    connection = sqlite3.connect(path)
    connection.execute("UPDATE events SET nonce = ? WHERE local_log_seq = 1", (malformed_nonce,))
    connection.commit()
    connection.close()

    with pytest.raises(IntegrityError, match="nonce"):
        EventStore.for_test(path, key=b"integrity-key")


@pytest.mark.parametrize(
    "malformed_ciphertext",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"x" * 15, id="shorter-than-gcm-tag"),
        pytest.param("not-a-blob", id="text"),
        pytest.param(12345, id="integer"),
    ],
)
def test_malformed_stored_ciphertext_is_normalized_to_integrity_error(
    tmp_path, malformed_ciphertext
):
    path = tmp_path / "events.db"
    with EventStore.for_test(path, key=b"integrity-key") as store:
        store.append(_event("protected"))

    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE events SET ciphertext = ? WHERE local_log_seq = 1",
        (malformed_ciphertext,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(IntegrityError, match="ciphertext"):
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


@pytest.mark.parametrize(
    "schema_options",
    [
        pytest.param({"ciphertext_declaration": "TEXT NOT NULL"}, id="altered-type"),
        pytest.param({"ciphertext_declaration": "BLOB"}, id="missing-not-null"),
        pytest.param({"repo_check": False}, id="missing-check"),
        pytest.param({"replay_session_index": False}, id="missing-replay-index"),
        pytest.param({"event_id_uniqueness": "partial"}, id="partial-unique-substitute"),
    ],
)
def test_declared_current_schema_rejects_owned_shape_or_constraint_drift(
    tmp_path, schema_options
):
    path = tmp_path / "events.db"
    _create_version_one_schema(path, **schema_options)

    with pytest.raises(MigrationError, match="schema does not match"):
        EventStore.for_test(path)


@pytest.mark.parametrize(
    "comment_substitution",
    [
        "store_metadata_check",
        "repo_sequence_check",
        "session_sequence_check",
        "events_autoincrement",
    ],
)
def test_declared_current_schema_rejects_constraints_present_only_in_comments(
    tmp_path, comment_substitution
):
    path = tmp_path / "events.db"
    _create_version_one_schema(path, comment_substitution=comment_substitution)
    connection = sqlite3.connect(path)
    commented_sql = "\n".join(
        row[0]
        for row in connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type = 'table' AND (sql LIKE '%/*%' OR sql LIKE '%--%')"
        )
    )
    connection.close()
    assert "/*" in commented_sql or "--" in commented_sql

    with pytest.raises(MigrationError, match="schema does not match"):
        EventStore.for_test(path)


def test_sql_comment_stripping_preserves_markers_inside_quoted_sql():
    sql = """
    SELECT '--single-quoted', '/* single-quoted */',
           "--double-quoted", `/* backtick-quoted */`, [--bracket-quoted]
    -- removable line comment
    /* removable block comment */
    """

    stripped = migrations_module._strip_sql_comments(sql)

    assert "'--single-quoted'" in stripped
    assert "'/* single-quoted */'" in stripped
    assert '"--double-quoted"' in stripped
    assert "`/* backtick-quoted */`" in stripped
    assert "[--bracket-quoted]" in stripped
    assert "removable line comment" not in stripped
    assert "removable block comment" not in stripped


@pytest.mark.parametrize(
    "quoted_token_substitution",
    [
        "store_metadata_check",
        "repo_sequence_check",
        "session_sequence_check",
        "events_autoincrement",
    ],
)
def test_declared_current_schema_rejects_constraints_present_only_in_quoted_names(
    tmp_path, quoted_token_substitution
):
    path = tmp_path / "events.db"
    _create_version_one_schema(path, quoted_token_substitution=quoted_token_substitution)

    with pytest.raises(MigrationError, match="schema does not match"):
        EventStore.for_test(path)


def test_sql_constraint_masking_removes_quoted_tokens_but_keeps_real_ddl():
    sql = """
    CREATE TABLE "CHECK(fake=1)" (
        real INTEGER CHECK(real = 1),
        note TEXT DEFAULT 'AUTOINCREMENT -- still quoted',
        `PRIMARY KEY AUTOINCREMENT` TEXT,
        [CHECK(bracketed=1)] TEXT,
        escaped TEXT CONSTRAINT "CHECK""(escaped=1)" UNIQUE
    )
    """

    masked = migrations_module._mask_sql_quoted_content(sql)

    assert "CHECK(real = 1)" in masked
    assert "CHECK(fake=1)" not in masked
    assert "AUTOINCREMENT -- still quoted" not in masked
    assert "PRIMARY KEY AUTOINCREMENT" not in masked
    assert "CHECK(bracketed=1)" not in masked
    assert "CHECK(escaped=1)" not in masked


@pytest.mark.parametrize(
    "pragma_name",
    ["foreign_keys", "journal_mode", "synchronous", "busy_timeout"],
)
def test_sqlite_configuration_mismatch_is_named_and_closes_connection(
    tmp_path, monkeypatch, pragma_name
):
    real_connect = sqlite3.connect
    opened_connections: list[sqlite3.Connection] = []
    _MismatchedPragmaConnection.mismatch = pragma_name

    def mismatched_connect(*args, **kwargs):
        connection = real_connect(*args, factory=_MismatchedPragmaConnection, **kwargs)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(store_module.sqlite3, "connect", mismatched_connect)

    with pytest.raises(StoreConfigurationError, match=pragma_name):
        EventStore.for_test(tmp_path / "events.db", busy_timeout_ms=4_321)

    assert opened_connections
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened_connections[0].execute("SELECT 1")


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem contract")
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_database_files_reject_symlinks_without_touching_targets(tmp_path, suffix):
    path = tmp_path / "events.db"
    if suffix:
        with EventStore.for_test(path):
            pass
    candidate = path.with_name(f"{path.name}{suffix}")
    if candidate.exists():
        candidate.unlink()
    target = tmp_path / f"target{suffix or '-db'}"
    target.write_bytes(b"target-must-remain-untouched")
    target.chmod(0o600)
    candidate.symlink_to(target)

    with pytest.raises(UnsafePermissionsError, match="regular file"):
        EventStore.for_test(path)

    assert target.read_bytes() == b"target-must-remain-untouched"


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem contract")
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_database_files_reject_non_regular_files(tmp_path, suffix):
    path = tmp_path / "events.db"
    if suffix:
        with EventStore.for_test(path):
            pass
    candidate = path.with_name(f"{path.name}{suffix}")
    if candidate.exists():
        candidate.unlink()
    candidate.mkdir(mode=0o700)
    candidate.chmod(0o600)

    try:
        with pytest.raises(UnsafePermissionsError, match="regular file"):
            EventStore.for_test(path)
    finally:
        candidate.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_new_home_and_database_modes_are_independent_of_umask(tmp_path):
    home = tmp_path / "umask-home"
    path = home / "events.db"
    previous_umask = os.umask(0o777)
    try:
        with EventStore.for_test(path):
            pass
    finally:
        os.umask(previous_umask)
        if home.exists():
            home.chmod(0o700)

    assert home.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


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
