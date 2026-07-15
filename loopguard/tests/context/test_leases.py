from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

from loopguard.context.leases import LeaseManager, LeaseScope


class ManualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 15, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def test_overlapping_file_leases_warn_other_session(tmp_path: Path) -> None:
    clock = ManualClock()
    manager = LeaseManager(tmp_path / "leases.db", clock=clock)
    manager.acquire("r", "s1", LeaseScope.file("src/auth.py"), ttl=timedelta(minutes=10))

    result = manager.acquire(
        "r",
        "s2",
        LeaseScope.symbol("src/auth.py", "login"),
        ttl=timedelta(minutes=10),
    )

    assert result.status == "conflict"
    assert result.owner_session_id == "s1"
    assert result.scope == LeaseScope.file("src/auth.py")


def test_expired_lease_does_not_conflict(tmp_path: Path) -> None:
    clock = ManualClock()
    manager = LeaseManager(tmp_path / "leases.db", clock=clock)
    manager.acquire("r", "s1", LeaseScope.file("src/a.py"), ttl=timedelta(seconds=1))
    clock.advance(seconds=2)

    result = manager.acquire("r", "s2", LeaseScope.file("src/a.py"))

    assert result.status == "acquired"
    assert result.owner_session_id == "s2"
    assert manager.purge_expired("r") == 0


def test_symbol_leases_only_overlap_same_normalized_symbol(tmp_path: Path) -> None:
    manager = LeaseManager(tmp_path / "leases.db")
    first = manager.acquire("r", "s1", LeaseScope.symbol("src/auth.py", "login"))
    other = manager.acquire("r", "s2", LeaseScope.symbol("src/auth.py", "logout"))
    conflict = manager.acquire("r", "s3", LeaseScope.symbol("src/auth.py", " login "))

    assert first.status == other.status == "acquired"
    assert conflict.status == "conflict"
    assert conflict.owner_session_id == "s1"


def test_exact_owner_renews_and_release_is_scoped_and_restart_safe(tmp_path: Path) -> None:
    database = tmp_path / "leases.db"
    clock = ManualClock()
    scope = LeaseScope.symbol("src/auth.py", "login")
    with LeaseManager(database, clock=clock) as manager:
        acquired = manager.acquire("r", "s1", scope, enforcement_mode="enforced")
        clock.advance(seconds=5)
        renewed = manager.acquire("r", "s1", scope, ttl=timedelta(minutes=20))
        manager.acquire("r", "s1", LeaseScope.file("src/other.py"))

        assert renewed.status == "renewed"
        assert renewed.lease_id == acquired.lease_id
        assert renewed.acquired_at == acquired.acquired_at
        assert renewed.expires_at > acquired.expires_at
        assert renewed.enforcement_mode == "enforced"
        assert manager.release("r", "s1", scopes=[scope]) == 1

    with LeaseManager(database, clock=clock) as reopened:
        active = reopened.active("r", session_id="s1")
        assert [lease.scope for lease in active] == [LeaseScope.file("src/other.py")]
        assert reopened.release("r", "s1") == 1
        assert reopened.active("r") == []


def test_two_contenders_never_both_acquire_the_same_scope(tmp_path: Path) -> None:
    database = tmp_path / "leases.db"
    LeaseManager(database).close()
    barrier = Barrier(2)

    def contend(session_id: str) -> tuple[str, str | None]:
        with LeaseManager(database) as manager:
            barrier.wait()
            result = manager.acquire("r", session_id, LeaseScope.file("src/shared.py"))
            return result.status, result.owner_session_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contend, ["s1", "s2"]))

    assert sorted(status for status, _owner in results) == ["acquired", "conflict"]
    winner = next(owner for status, owner in results if status == "acquired")
    loser_view = next(owner for status, owner in results if status == "conflict")
    assert winner == loser_view
