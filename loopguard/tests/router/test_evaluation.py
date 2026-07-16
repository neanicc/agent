from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from loopguard.router.evaluation import (
    EvaluationReport,
    ShadowDecision,
    assign_experiment,
    evaluate_outcomes,
)
from loopguard.router.outcomes import RoutingOutcome


def test_holdout_assignment_is_stable_by_repository_and_task() -> None:
    first = assign_experiment("repo_1", "task_9", percentage=10)
    second = assign_experiment("repo_1", "task_9", percentage=10)

    assert first == second
    assert first in {"control", "routed"}


def test_assignment_is_keyed_bounded_and_requires_opt_in() -> None:
    assert assign_experiment("repo", "task", percentage=0) == "control"
    assert assign_experiment("repo", "task", percentage=100) == "routed"
    with pytest.raises(ValueError, match="opt-in"):
        assign_experiment("repo", "task", percentage=10, opted_in=False)
    with pytest.raises(ValueError, match="percentage"):
        assign_experiment("repo", "task", percentage=101)
    assignments = {
        assign_experiment("repo", f"task-{index}", percentage=50, assignment_key=b"other-key")
        for index in range(100)
    }
    assert assignments == {"control", "routed"}


def test_shadow_decision_never_changes_active_model() -> None:
    shadow = ShadowDecision.record(
        current_model="current",
        current_effort="medium",
        proposed_model="deep",
        proposed_effort="high",
        routing_id="route-shadow",
    )

    assert shadow.applied is False
    assert shadow.actual_model == "current"
    assert shadow.proposed_model == "deep"
    assert shadow.mode == "shadow"


def test_report_requires_samples_and_displays_confidence_intervals() -> None:
    outcomes = [
        _outcome(index, group="control" if index < 40 else "routed", verified=index % 5 != 0)
        for index in range(80)
    ]

    report = evaluate_outcomes(outcomes, minimum_sample_size=30)

    assert isinstance(report, EvaluationReport)
    assert report.control.sample_size == report.routed.sample_size == 40
    assert report.control.verified_success.confidence_interval is not None
    assert report.routed.regression_rate.confidence_interval is not None
    assert report.minimum_sample_met is True
    assert report.recommendation in {"enable_candidate", "keep_shadow", "insufficient_evidence"}
    assert report.control.observed_total_cost == Decimal("4.0")
    assert report.estimated_counterfactual_savings is None


def test_partial_costs_are_not_summed_as_observed_totals() -> None:
    outcomes = [_outcome(index, group="control", verified=True) for index in range(2)]
    outcomes.append(
        _outcome(3, group="routed", verified=True).model_copy(update={"agent_cost": None})
    )

    report = evaluate_outcomes(outcomes, minimum_sample_size=2)

    assert report.routed.observed_total_cost is None
    assert report.minimum_sample_met is False
    assert report.recommendation == "insufficient_evidence"


def _outcome(index: int, *, group: str, verified: bool) -> RoutingOutcome:
    return RoutingOutcome(
        routing_id=f"route-{group}-{index}",
        experiment_group=group,
        routing_mode="holdout",
        verified=verified,
        verification_verdict="passed" if verified else "regression",
        regressions=() if verified else (f"regression-{index}",),
        agent_cost=Decimal("0.08"),
        guard_cost=Decimal("0.01"),
        verification_cost=Decimal("0.01"),
        repair_cost=Decimal("0"),
        interruptions=index % 2,
        repairs=0,
        wall_time_ms=1_000 + index,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=index),
    )
