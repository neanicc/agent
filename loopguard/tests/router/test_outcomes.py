from __future__ import annotations

import os
from decimal import Decimal

import pytest

from loopguard.router.outcomes import OutcomeRecorder
from loopguard.router.store import OutcomeStoreError


def test_total_cost_includes_agent_guard_and_repair(tmp_path) -> None:
    recorder = OutcomeRecorder.for_path(tmp_path / "router.db")
    outcome = recorder.record(
        routing_id="route_1",
        verified=True,
        agent_cost=Decimal("0.20"),
        guard_cost=Decimal("0.01"),
        verification_cost=Decimal("0.00"),
        repair_cost=Decimal("0.03"),
        wall_time_ms=1200,
    )

    assert outcome.total_cost == Decimal("0.24")
    assert outcome.known_total == Decimal("0.24")


def test_unknown_component_makes_total_unknown_but_preserves_known_sum(tmp_path) -> None:
    outcome = OutcomeRecorder.for_path(tmp_path / "router.db").record(
        routing_id="route_unknown",
        verified=None,
        agent_cost=None,
        guard_cost=Decimal("0.01"),
        verification_cost=Decimal("0"),
        repair_cost=Decimal("0"),
        wall_time_ms=10,
    )

    assert outcome.total_cost is None
    assert outcome.known_total == Decimal("0.01")
    assert outcome.cost_status == "partial"
    assert outcome.input_tokens is None
    assert outcome.output_tokens is None
    assert outcome.total_tokens is None


def test_reported_estimated_and_reconciled_costs_remain_distinct(tmp_path) -> None:
    outcome = OutcomeRecorder.for_path(tmp_path / "router.db").record(
        routing_id="route_cost_sources",
        selected_model="planned-model",
        actual_model="provider-model-revision",
        effort="high",
        phase="implement",
        policy_version="policy-v1",
        matched_rule="high-risk",
        automatic=True,
        input_tokens=100,
        output_tokens=25,
        provider_reported_cost=Decimal("0.123456"),
        estimated_agent_cost=Decimal("0.120000"),
        agent_cost=Decimal("0.123456"),
        guard_cost=Decimal("0.01"),
        verification_cost=Decimal("0.02"),
        repair_cost=Decimal("0.03"),
        verified=False,
        verification_verdict="regression",
        regressions=["tests/test_checkout.py::test_total"],
        interruptions=2,
        repairs=1,
        wall_time_ms=1500,
    )

    assert outcome.provider_reported_cost == Decimal("0.123456")
    assert outcome.estimated_agent_cost == Decimal("0.120000")
    assert outcome.agent_cost == Decimal("0.123456")
    assert outcome.total_tokens == 125
    assert outcome.selected_model != outcome.actual_model
    assert outcome.total_cost == Decimal("0.183456")
    assert not hasattr(outcome, "avoided_cost")

    restored = OutcomeRecorder.for_path(tmp_path / "router.db").get("route_cost_sources")
    assert restored == outcome
    assert restored.regressions == ("tests/test_checkout.py::test_total",)


def test_explicit_zero_is_known_and_not_confused_with_missing(tmp_path) -> None:
    outcome = OutcomeRecorder.for_path(tmp_path / "router.db").record(
        routing_id="route_zero",
        verified=True,
        agent_cost=Decimal("0"),
        guard_cost=Decimal("0"),
        verification_cost=Decimal("0"),
        repair_cost=Decimal("0"),
        wall_time_ms=0,
    )

    assert outcome.total_cost == Decimal("0")
    assert outcome.cost_status == "complete"


@pytest.mark.parametrize("value", [-1, Decimal("-0.01"), float("nan"), 0.1])
def test_invalid_or_float_cost_input_is_rejected(tmp_path, value) -> None:
    recorder = OutcomeRecorder.for_path(tmp_path / "router.db")

    with pytest.raises((TypeError, ValueError)):
        recorder.record(
            routing_id="route_invalid",
            verified=True,
            agent_cost=value,
            guard_cost=Decimal("0"),
            verification_cost=Decimal("0"),
            repair_cost=Decimal("0"),
            wall_time_ms=1,
        )


def test_duplicate_routing_ids_and_unsafe_paths_fail_closed(tmp_path) -> None:
    path = tmp_path / "private" / "router.db"
    recorder = OutcomeRecorder.for_path(path)
    payload = {
        "routing_id": "route_duplicate",
        "verified": True,
        "agent_cost": Decimal("0"),
        "guard_cost": Decimal("0"),
        "verification_cost": Decimal("0"),
        "repair_cost": Decimal("0"),
        "wall_time_ms": 1,
    }
    recorder.record(**payload)

    with pytest.raises(OutcomeStoreError, match="already exists"):
        recorder.record(**payload)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600

    symlink = tmp_path / "unsafe.db"
    symlink.symlink_to(path)
    with pytest.raises(OutcomeStoreError, match="symlink"):
        OutcomeRecorder.for_path(symlink)

    corrupt_parent = tmp_path / "corrupt"
    corrupt_parent.mkdir(mode=0o700)
    corrupt = corrupt_parent / "router.db"
    corrupt.write_bytes(b"not a sqlite database")
    if os.name == "posix":
        corrupt.chmod(0o600)
    with pytest.raises(OutcomeStoreError, match="initialization"):
        OutcomeRecorder.for_path(corrupt)


def test_list_is_stable_and_bounded(tmp_path) -> None:
    recorder = OutcomeRecorder.for_path(tmp_path / "router.db")
    for index in range(3):
        recorder.record(
            routing_id=f"route_{index}",
            verified=bool(index % 2),
            agent_cost=Decimal("0"),
            guard_cost=Decimal("0"),
            verification_cost=Decimal("0"),
            repair_cost=Decimal("0"),
            wall_time_ms=index,
        )

    assert [outcome.routing_id for outcome in recorder.list(limit=2)] == [
        "route_2",
        "route_1",
    ]
    with pytest.raises(ValueError, match="limit"):
        recorder.list(limit=0)
