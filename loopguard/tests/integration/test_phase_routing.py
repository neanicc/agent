from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from loopguard.router import coordinator as coordinator_module
from loopguard.router.coordinator import PhaseCoordinator, PhaseTransitionError
from loopguard.router.policy import RoutingDecision


class Adapter:
    def __init__(self) -> None:
        self.model_changes: list[tuple[str, str]] = []
        self.route: tuple[str, str] | None = None
        self.safe = True
        self.orphaned = False

    async def apply_phase_route(self, _session_id, model, effort):
        self.model_changes.append((model, effort))
        self.route = (model, effort)

    async def current_phase_route(self, _session_id):
        return self.route

    def phase_switch_safe(self, _session_id):
        return self.safe

    def mark_route_orphaned(self, _session_id):
        self.orphaned = True


def decision(phase, model, effort):
    return RoutingDecision(
        policy_version="p1",
        model_id=model,
        effort=effort,
        phase=phase,
        matched_rule=f"{phase}-rule",
        considered_models=[model],
        rejected={},
        automatic=True,
    )


def test_router_does_not_switch_mid_tool_call(tmp_path) -> None:
    async def scenario():
        adapter = Adapter()
        coordinator = PhaseCoordinator(tmp_path / "state" / "phases.db")
        await coordinator.start_phase(
            "session", "implement", decision("implement", "standard", "medium"), adapter
        )
        coordinator.tool_started("session")
        with pytest.raises(PhaseTransitionError, match="active tool"):
            await coordinator.start_phase(
                "session", "verify", decision("verify", "standard", "high"), adapter
            )
        coordinator.tool_completed("session")
        await coordinator.start_phase(
            "session", "verify", decision("verify", "standard", "high"), adapter
        )
        return adapter

    adapter = asyncio.run(scenario())
    assert adapter.model_changes == [("standard", "medium"), ("standard", "high")]


def test_failed_verification_repair_is_bounded(tmp_path) -> None:
    async def scenario():
        adapter = Adapter()
        coordinator = PhaseCoordinator(tmp_path / "state" / "phases.db", max_repairs=1)
        await coordinator.start_phase(
            "s", "implement", decision("implement", "standard", "medium"), adapter
        )
        await coordinator.start_phase(
            "s", "verify", decision("verify", "standard", "high"), adapter
        )
        with pytest.raises(PhaseTransitionError, match="failed evidence"):
            await coordinator.start_phase(
                "s", "implement", decision("implement", "standard", "medium"), adapter
            )
        await coordinator.start_phase(
            "s",
            "implement",
            decision("implement", "standard", "medium"),
            adapter,
            failed_evidence=True,
        )
        await coordinator.start_phase(
            "s", "verify", decision("verify", "standard", "high"), adapter
        )
        with pytest.raises(PhaseTransitionError, match="repair budget"):
            await coordinator.start_phase(
                "s",
                "implement",
                decision("implement", "standard", "medium"),
                adapter,
                failed_evidence=True,
            )

    asyncio.run(scenario())


def test_recovery_compares_route_without_replaying_transition(tmp_path) -> None:
    class Crash(BaseException):
        pass

    async def scenario():
        path = tmp_path / "state" / "phases.db"
        adapter = Adapter()
        coordinator = PhaseCoordinator(path)
        pending = decision("implement", "standard", "medium")
        original = adapter.apply_phase_route

        async def crash_after_apply(session_id, model, effort):
            await original(session_id, model, effort)
            raise Crash

        adapter.apply_phase_route = crash_after_apply
        with pytest.raises(Crash):
            await coordinator.start_phase("s", "implement", pending, adapter)
        restarted = PhaseCoordinator(path)
        state = await restarted.recover("s", adapter)
        return adapter, state

    adapter, state = asyncio.run(scenario())
    assert adapter.model_changes == [("standard", "medium")]
    assert state.status == "active"


def test_recovery_mismatch_orphans_for_human_review(tmp_path) -> None:
    class Crash(BaseException):
        pass

    async def scenario():
        path = tmp_path / "state" / "phases.db"
        adapter = Adapter()
        coordinator = PhaseCoordinator(path)

        async def apply_then_diverge(_session_id, _model, _effort):
            adapter.route = ("other", "low")
            raise Crash

        adapter.apply_phase_route = apply_then_diverge
        with pytest.raises(Crash):
            await coordinator.start_phase(
                "s", "implement", decision("implement", "standard", "high"), adapter
            )
        state = await PhaseCoordinator(path).recover("s", adapter)
        return adapter, state

    adapter, state = asyncio.run(scenario())
    assert state.status == "orphaned"
    assert adapter.orphaned is True


def test_concurrent_coordinators_cannot_launch_the_same_phase_twice(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state" / "phases.db"
    first = PhaseCoordinator(path)
    second = PhaseCoordinator(path)
    adapter = Adapter()
    route = decision("implement", "standard", "medium")
    barrier = threading.Barrier(2)
    original_get = coordinator_module._PhaseStore.get

    def synchronized_get(store, session_id):
        state = original_get(store, session_id)
        if state is None:
            barrier.wait(timeout=5)
        return state

    monkeypatch.setattr(coordinator_module._PhaseStore, "get", synchronized_get)

    def launch(coordinator):
        return asyncio.run(coordinator.start_phase("s", "implement", route, adapter))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launch, coordinator) for coordinator in (first, second)]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(exc)

    assert adapter.model_changes == [("standard", "medium")]
    assert sum(isinstance(result, PhaseTransitionError) for result in results) == 1
