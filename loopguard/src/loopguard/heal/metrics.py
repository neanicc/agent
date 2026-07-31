"""Fail-closed rollout gates and aggregate safety metrics for auto-heal."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class RolloutMode(StrEnum):
    OBSERVE = "observe"
    REPRODUCE = "reproduce"
    GENERATE = "generate"
    PUBLISH_DRAFT = "publish-draft"


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    intake_accepted: int
    intake_duplicates: int
    reproduction_successes: int
    reproduction_attempts: int
    safe_fixtures: int
    fixture_attempts: int
    candidates_passed: int
    candidate_attempts: int
    no_winner: int
    workflow_count: int
    cost_usd: Decimal
    workflow_duration_seconds: float
    published: int
    accepted: int
    reverted: int
    incidents: int

    @property
    def intake_deduplication_rate(self) -> float:
        return _rate(self.intake_duplicates, self.intake_accepted + self.intake_duplicates)

    @property
    def reproduction_rate(self) -> float:
        return _rate(self.reproduction_successes, self.reproduction_attempts)

    @property
    def safe_fixture_rate(self) -> float:
        return _rate(self.safe_fixtures, self.fixture_attempts)

    @property
    def candidate_pass_rate(self) -> float:
        return _rate(self.candidates_passed, self.candidate_attempts)

    @property
    def no_winner_rate(self) -> float:
        return _rate(self.no_winner, self.candidate_attempts)

    @property
    def publication_rate(self) -> float:
        return _rate(self.published, self.candidate_attempts)

    @property
    def pr_acceptance_rate(self) -> float:
        return _rate(self.accepted, self.published)

    @property
    def pr_reversion_rate(self) -> float:
        return _rate(self.reverted, self.published)


class RolloutMetrics:
    """Thread-safe in-process collector; production exports the same counters."""

    def __init__(self) -> None:
        self._values = {
            "intake_accepted": 0,
            "intake_duplicates": 0,
            "reproduction_successes": 0,
            "reproduction_attempts": 0,
            "safe_fixtures": 0,
            "fixture_attempts": 0,
            "candidates_passed": 0,
            "candidate_attempts": 0,
            "no_winner": 0,
            "workflow_count": 0,
            "published": 0,
            "accepted": 0,
            "reverted": 0,
            "incidents": 0,
        }
        self._cost_usd = Decimal("0")
        self._workflow_duration_seconds = 0.0
        self._lock = threading.Lock()

    def record_intake(self, *, accepted: int, duplicates: int) -> None:
        self._record_counts(intake_accepted=accepted, intake_duplicates=duplicates)

    def record_reproduction(self, *, successes: int, attempts: int) -> None:
        _bounded_subset(successes, attempts)
        self._record_counts(
            reproduction_successes=successes,
            reproduction_attempts=attempts,
        )

    def record_fixture(self, *, safe: int, attempts: int) -> None:
        _bounded_subset(safe, attempts)
        self._record_counts(safe_fixtures=safe, fixture_attempts=attempts)

    def record_candidate(self, *, passed: int, attempts: int, no_winner: int) -> None:
        _bounded_subset(passed, attempts)
        _bounded_subset(no_winner, attempts)
        self._record_counts(
            candidates_passed=passed,
            candidate_attempts=attempts,
            no_winner=no_winner,
        )

    def record_workflow(
        self,
        *,
        cost_usd: Decimal | str,
        duration_seconds: float,
    ) -> None:
        try:
            cost = Decimal(cost_usd)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("metric values must be bounded") from exc
        if (
            not cost.is_finite()
            or not Decimal("0") <= cost <= Decimal("1000000")
            or not math.isfinite(duration_seconds)
            or not 0 <= duration_seconds <= 30 * 86_400
        ):
            raise ValueError("metric values must be bounded")
        with self._lock:
            self._values["workflow_count"] += 1
            self._cost_usd += cost
            self._workflow_duration_seconds += duration_seconds

    def record_publication(self, *, published: int, accepted: int, reverted: int) -> None:
        _bounded_subset(accepted, published)
        _bounded_subset(reverted, published)
        self._record_counts(
            published=published,
            accepted=accepted,
            reverted=reverted,
        )

    def record_incident(self, *, count: int = 1) -> None:
        self._record_counts(incidents=count)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                **self._values,
                cost_usd=self._cost_usd,
                workflow_duration_seconds=self._workflow_duration_seconds,
            )

    def _record_counts(self, **values: int) -> None:
        for value in values.values():
            _bounded_count(value)
        with self._lock:
            for key, value in values.items():
                self._values[key] += value


@dataclass(frozen=True, slots=True)
class RolloutPolicy:
    mode: RolloutMode = RolloutMode.OBSERVE
    organization_id: str = "local"
    last_approved_by: str | None = None
    minimum_intake_samples: int = 100
    minimum_reproduction_attempts: int = 20
    minimum_reproduction_rate: float = 0.85
    minimum_safe_fixture_rate: float = 0.95
    minimum_candidate_attempts: int = 20
    minimum_candidate_pass_rate: float = 0.80
    maximum_no_winner_rate: float = 0.20

    def __post_init__(self) -> None:
        if (
            not self.organization_id.strip()
            or len(self.organization_id) > 128
            or "\x00" in self.organization_id
            or self.minimum_intake_samples < 1
            or self.minimum_reproduction_attempts < 1
            or self.minimum_candidate_attempts < 1
            or any(
                not math.isfinite(rate) or not 0 <= rate <= 1
                for rate in (
                    self.minimum_reproduction_rate,
                    self.minimum_safe_fixture_rate,
                    self.minimum_candidate_pass_rate,
                    self.maximum_no_winner_rate,
                )
            )
        ):
            raise ValueError("rollout policy values must be bounded")

    def can_generate(self, metrics: MetricsSnapshot) -> GateDecision:
        reasons: list[str] = []
        if self.mode not in {
            RolloutMode.REPRODUCE,
            RolloutMode.GENERATE,
            RolloutMode.PUBLISH_DRAFT,
        }:
            reasons.append("organization is not in reproduce mode")
        if metrics.reproduction_attempts < self.minimum_reproduction_attempts:
            reasons.append("insufficient reproduction attempts")
        if metrics.reproduction_rate <= self.minimum_reproduction_rate:
            reasons.append("reproduction rate has not exceeded the safety threshold")
        if metrics.fixture_attempts < self.minimum_reproduction_attempts:
            reasons.append("insufficient safe-fixture attempts")
        if metrics.safe_fixture_rate < self.minimum_safe_fixture_rate:
            reasons.append("safe-fixture rate is below the safety threshold")
        if metrics.incidents:
            reasons.append("open safety incidents block promotion")
        return GateDecision(not reasons, tuple(reasons))

    def can_publish(self, metrics: MetricsSnapshot) -> GateDecision:
        reasons = list(self.can_generate(metrics).reasons)
        if self.mode not in {RolloutMode.GENERATE, RolloutMode.PUBLISH_DRAFT}:
            reasons.append("organization is not in generate mode")
        if metrics.candidate_attempts < self.minimum_candidate_attempts:
            reasons.append("insufficient candidate attempts")
        if metrics.candidate_pass_rate < self.minimum_candidate_pass_rate:
            reasons.append("candidate pass rate is below the safety threshold")
        if metrics.no_winner_rate > self.maximum_no_winner_rate:
            reasons.append("no-winner rate exceeds the safety threshold")
        if metrics.reverted:
            reasons.append("a reverted repair blocks draft publication promotion")
        return GateDecision(not reasons, tuple(dict.fromkeys(reasons)))

    def promote(
        self,
        target: RolloutMode,
        metrics: MetricsSnapshot,
        *,
        approved_by: str,
        expected_mode: RolloutMode,
    ) -> RolloutPolicy:
        if expected_mode is not self.mode:
            raise ValueError("expected rollout mode does not match current policy")
        order = list(RolloutMode)
        if order.index(target) != order.index(self.mode) + 1:
            raise ValueError("rollout promotion must be sequential")
        approver = approved_by.strip()
        if not approver or len(approver) > 256 or "\x00" in approver:
            raise ValueError("rollout promotion requires an explicit bounded approver")
        if target is RolloutMode.REPRODUCE:
            reasons = []
            if metrics.intake_accepted < self.minimum_intake_samples:
                reasons.append("insufficient intake samples")
            if metrics.incidents:
                reasons.append("open safety incidents block promotion")
            decision = GateDecision(not reasons, tuple(reasons))
        elif target is RolloutMode.GENERATE:
            decision = self.can_generate(metrics)
        else:
            decision = self.can_publish(metrics)
        if not decision.allowed:
            raise ValueError("rollout promotion denied: " + "; ".join(decision.reasons))
        return replace(self, mode=target, last_approved_by=approver)

    def demote(
        self,
        target: RolloutMode,
        *,
        approved_by: str,
        expected_mode: RolloutMode,
    ) -> RolloutPolicy:
        if expected_mode is not self.mode:
            raise ValueError("expected rollout mode does not match current policy")
        if list(RolloutMode).index(target) >= list(RolloutMode).index(self.mode):
            raise ValueError("rollout demotion must move to a safer mode")
        approver = approved_by.strip()
        if not approver or len(approver) > 256 or "\x00" in approver:
            raise ValueError("rollout demotion requires an explicit bounded approver")
        return replace(self, mode=target, last_approved_by=approver)


def _bounded_count(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        raise ValueError("metric values must be bounded")


def _bounded_subset(value: int, total: int) -> None:
    _bounded_count(value)
    _bounded_count(total)
    if value > total:
        raise ValueError("metric values must be bounded")


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
