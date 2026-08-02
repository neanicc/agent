from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventIdConflictError, EventStore, StoreClosedError


def _event(
    event_id: str,
    *,
    repo_id: str = "r",
    session_id: str = "s",
    source: str = "test",
    kind: EventKind = EventKind.SESSION_STARTED,
    payload: dict[str, object] | None = None,
) -> ControlEvent:
    return ControlEvent(
        event_id=event_id,
        kind=kind,
        source=source,
        session=SessionRef(host_id="h", repo_id=repo_id, session_id=session_id),
        payload=payload or {},
    )


def test_append_is_idempotent_and_replay_uses_cursor(tmp_path):
    with EventStore.for_test(tmp_path / "events.db") as store:
        assert store.append(_event("evt_1")).local_log_seq == 1
        assert store.append(_event("evt_1")).local_log_seq == 1
        assert store.append(_event("evt_2")).repo_seq == 2

        rows = store.read_repo_after(repo_id="r", repo_seq=1, limit=10)

    assert [row.event.event_id for row in rows] == ["evt_2"]
    assert rows[0].repo_seq == 2


def test_reconstructed_duplicate_may_differ_only_in_created_at(tmp_path):
    original = _event("evt_retry", payload={"attempt": 1})
    reconstructed = original.model_copy(
        update={"created_at": original.created_at + timedelta(minutes=5)}
    )
    with EventStore.for_test(tmp_path / "events.db") as store:
        first = store.append(original)
        retried = store.append(reconstructed)
        next_position = store.append(_event("evt_next"))

    assert retried == first
    assert next_position.local_log_seq == 2
    assert next_position.repo_seq == 2
    assert next_position.session_seq == 2


def test_duplicate_event_id_with_different_payload_conflicts_without_spending_sequences(tmp_path):
    with EventStore.for_test(tmp_path / "events.db") as store:
        store.append(_event("evt_conflict", payload={"value": "original"}))

        with pytest.raises(EventIdConflictError):
            store.append(_event("evt_conflict", payload={"value": "changed"}))

        next_position = store.append(_event("evt_next"))

    assert next_position.local_log_seq == 2
    assert next_position.repo_seq == 2
    assert next_position.session_seq == 2


def test_duplicate_event_id_with_different_session_conflicts_without_creating_domain_cursors(
    tmp_path,
):
    with EventStore.for_test(tmp_path / "events.db") as store:
        store.append(_event("evt_conflict", repo_id="original", session_id="original"))

        with pytest.raises(EventIdConflictError):
            store.append(_event("evt_conflict", repo_id="other", session_id="other"))

        next_position = store.append(_event("evt_next", repo_id="other", session_id="other"))

    assert next_position.local_log_seq == 2
    assert next_position.repo_seq == 1
    assert next_position.session_seq == 1


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("other-source", EventKind.SESSION_STARTED),
        ("test", EventKind.TOOL_CALL),
    ],
)
def test_duplicate_event_id_with_different_source_or_kind_conflicts(tmp_path, source, kind):
    with EventStore.for_test(tmp_path / "events.db") as store:
        store.append(_event("evt_conflict"))

        with pytest.raises(EventIdConflictError):
            store.append(_event("evt_conflict", source=source, kind=kind))

        assert store.append(_event("evt_next")).local_log_seq == 2


def test_interleaved_cursor_domains_are_independent(tmp_path):
    with EventStore.for_test(tmp_path / "events.db") as store:
        a1 = store.append(_event("a1", repo_id="a", session_id="shared"))
        b1 = store.append(_event("b1", repo_id="b", session_id="other"))
        a2 = store.append(_event("a2", repo_id="a", session_id="third"))
        s2 = store.append(_event("s2", repo_id="b", session_id="shared"))

    assert [a1.repo_seq, a2.repo_seq] == [1, 2]
    assert [b1.repo_seq, s2.repo_seq] == [1, 2]
    assert [a1.session_seq, s2.session_seq] == [1, 2]
    assert a2.session_seq == 1
    assert s2.local_log_seq == 4


def test_body_is_encrypted_and_restart_replays_acknowledged_event(tmp_path):
    path = tmp_path / "events.db"
    secret = "fixture-secret-that-must-only-exist-in-ciphertext"
    store = EventStore.for_test(path, key=b"test-key-material")
    position = store.append(_event("evt_secret", payload={"prompt": secret}))
    store.close()

    database_files = [path, path.with_name(f"{path.name}-wal")]
    persisted = b"".join(item.read_bytes() for item in database_files if item.exists())
    assert b"evt_secret" not in persisted
    assert secret.encode() not in persisted

    with EventStore.for_test(path, key=b"test-key-material") as reopened:
        row = reopened.read_local_after(position.local_log_seq - 1, 10)[0]

    assert row.event.event_id == "evt_secret"
    assert row.event.payload == {"prompt": secret}
    assert row.position == position


def test_exact_replay_apis_filter_and_order_their_own_domains(tmp_path):
    with EventStore.for_test(tmp_path / "events.db") as store:
        store.append(_event("a1", repo_id="a", session_id="s1"))
        store.append(_event("b1", repo_id="b", session_id="s1"))
        store.append(_event("a2", repo_id="a", session_id="s2"))
        store.append(_event("a3", repo_id="a", session_id="s1"))

        local = store.read_local_after(local_log_seq=1, limit=2)
        repo = store.read_repo_after(repo_id="a", repo_seq=1, limit=10)
        session = store.read_session_after(session_id="s1", session_seq=1, limit=10)

    assert [item.event.event_id for item in local] == ["b1", "a2"]
    assert [item.repo_seq for item in repo] == [2, 3]
    assert [item.event.event_id for item in repo] == ["a2", "a3"]
    assert [item.session_seq for item in session] == [2, 3]
    assert [item.event.event_id for item in session] == ["b1", "a3"]


def test_concurrent_writers_allocate_unique_contiguous_cursors(tmp_path):
    path = tmp_path / "events.db"
    first = EventStore.for_test(path, key=b"shared-test-key")
    second = EventStore.for_test(path, key=b"shared-test-key")

    try:
        def append(index: int):
            store = first if index % 2 else second
            return store.append(_event(f"evt_{index}", repo_id="r", session_id="s"))

        with ThreadPoolExecutor(max_workers=8) as executor:
            positions = list(executor.map(append, range(1, 41)))
    finally:
        first.close()
        second.close()

    assert sorted(item.local_log_seq for item in positions) == list(range(1, 41))
    assert sorted(item.repo_seq for item in positions) == list(range(1, 41))
    assert sorted(item.session_seq for item in positions) == list(range(1, 41))


def test_close_is_idempotent_and_operations_after_close_are_named_errors(tmp_path):
    store = EventStore.for_test(tmp_path / "events.db")
    store.close()
    store.close()

    with pytest.raises(StoreClosedError):
        store.append(_event("late"))
    with pytest.raises(StoreClosedError):
        store.read_local_after(0, 10)
