from __future__ import annotations

import pytest

from loopguard.heal.metrics import (
    RolloutMetrics,
    RolloutMode,
    RolloutPolicy,
)


def test_generation_disabled_until_reproduction_threshold() -> None:
    metrics = RolloutMetrics()
    policy = RolloutPolicy(mode=RolloutMode.REPRODUCE)

    metrics.record_reproduction(successes=17, attempts=20)
    assert policy.can_generate(metrics.snapshot()).allowed is False

    metrics.record_reproduction(successes=3, attempts=3)
    metrics.record_fixture(safe=23, attempts=23)
    assert policy.can_generate(metrics.snapshot()).allowed is True


def test_promotions_are_explicit_sequential_and_expected_state_bound() -> None:
    metrics = RolloutMetrics()
    metrics.record_intake(accepted=100, duplicates=8)
    policy = RolloutPolicy(mode=RolloutMode.OBSERVE)

    reproduced = policy.promote(
        RolloutMode.REPRODUCE,
        metrics.snapshot(),
        approved_by="policy-admin@example.com",
        expected_mode=RolloutMode.OBSERVE,
    )

    assert reproduced.mode is RolloutMode.REPRODUCE
    with pytest.raises(ValueError, match="expected rollout mode"):
        reproduced.promote(
            RolloutMode.GENERATE,
            metrics.snapshot(),
            approved_by="policy-admin@example.com",
            expected_mode=RolloutMode.OBSERVE,
        )
    with pytest.raises(ValueError, match="sequential"):
        policy.promote(
            RolloutMode.PUBLISH_DRAFT,
            metrics.snapshot(),
            approved_by="policy-admin@example.com",
            expected_mode=RolloutMode.OBSERVE,
        )


def test_publish_gate_fails_closed_on_reversions_or_incidents() -> None:
    metrics = RolloutMetrics()
    metrics.record_reproduction(successes=100, attempts=100)
    metrics.record_fixture(safe=100, attempts=100)
    metrics.record_candidate(passed=90, attempts=100, no_winner=5)
    policy = RolloutPolicy(mode=RolloutMode.GENERATE)

    assert policy.can_publish(metrics.snapshot()).allowed is True

    metrics.record_publication(published=10, accepted=8, reverted=1)
    assert policy.can_publish(metrics.snapshot()).allowed is False
    metrics.record_incident(count=1)
    assert policy.can_generate(metrics.snapshot()).allowed is False


def test_metrics_track_cost_time_deduplication_and_publication_rates() -> None:
    metrics = RolloutMetrics()
    metrics.record_intake(accepted=80, duplicates=20)
    metrics.record_reproduction(successes=18, attempts=20)
    metrics.record_fixture(safe=19, attempts=20)
    metrics.record_candidate(passed=7, attempts=10, no_winner=2)
    metrics.record_workflow(cost_usd="12.50", duration_seconds=300)
    metrics.record_publication(published=4, accepted=3, reverted=0)
    snapshot = metrics.snapshot()

    assert snapshot.intake_deduplication_rate == pytest.approx(0.2)
    assert snapshot.reproduction_rate == pytest.approx(0.9)
    assert snapshot.safe_fixture_rate == pytest.approx(0.95)
    assert snapshot.candidate_pass_rate == pytest.approx(0.7)
    assert snapshot.no_winner_rate == pytest.approx(0.2)
    assert snapshot.publication_rate == pytest.approx(0.4)
    assert snapshot.pr_acceptance_rate == pytest.approx(0.75)
    assert str(snapshot.cost_usd) == "12.50"
    assert snapshot.workflow_duration_seconds == 300


def test_metric_inputs_are_bounded_and_cannot_reduce_counters() -> None:
    metrics = RolloutMetrics()

    with pytest.raises(ValueError, match="bounded"):
        metrics.record_reproduction(successes=2, attempts=1)
    with pytest.raises(ValueError, match="bounded"):
        metrics.record_workflow(cost_usd="-1", duration_seconds=1)
    with pytest.raises(ValueError, match="bounded"):
        metrics.record_incident(count=1_000_001)
