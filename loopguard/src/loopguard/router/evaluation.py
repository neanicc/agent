from __future__ import annotations

import hashlib
import hmac
import json
import math
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .outcomes import RoutingOutcome


ExperimentGroup = Literal["control", "routed"]
_DEFAULT_ASSIGNMENT_KEY = b"loopguard-local-holdout-assignment-v1"


class ShadowDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    routing_id: str
    mode: Literal["shadow"] = "shadow"
    applied: Literal[False] = False
    actual_model: str
    actual_effort: str
    proposed_model: str
    proposed_effort: str

    @classmethod
    def record(
        cls,
        *,
        current_model: str,
        current_effort: str,
        proposed_model: str,
        proposed_effort: str,
        routing_id: str,
    ) -> ShadowDecision:
        return cls(
            routing_id=routing_id,
            actual_model=current_model,
            actual_effort=current_effort,
            proposed_model=proposed_model,
            proposed_effort=proposed_effort,
        )


class ConfidenceInterval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lower: float = Field(ge=0, le=1)
    upper: float = Field(ge=0, le=1)
    confidence: float = 0.95


class RateEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0, le=1)
    confidence_interval: ConfidenceInterval | None = None


class GroupMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_size: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    verified_success: RateEstimate
    regression_rate: RateEstimate
    observed_total_cost: Decimal | None
    mean_wall_time_ms: float | None = Field(default=None, ge=0)
    user_interventions: int = Field(ge=0)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control: GroupMetrics
    routed: GroupMetrics
    minimum_sample_size: int = Field(ge=1)
    minimum_sample_met: bool
    recommendation: Literal["enable_candidate", "keep_shadow", "insufficient_evidence"]
    estimated_counterfactual_savings: None = None


def assign_experiment(
    repository_id: str,
    task_id: str,
    *,
    percentage: int,
    assignment_key: bytes = _DEFAULT_ASSIGNMENT_KEY,
    opted_in: bool = True,
) -> ExperimentGroup:
    if not opted_in:
        raise ValueError("holdout assignment requires explicit opt-in")
    if (
        isinstance(percentage, bool)
        or not isinstance(percentage, int)
        or not 0 <= percentage <= 100
    ):
        raise ValueError("holdout percentage must be between 0 and 100")
    if not repository_id.strip() or not task_id.strip():
        raise ValueError("repository and task IDs must not be empty")
    if len(repository_id) > 512 or len(task_id) > 512 or not assignment_key:
        raise ValueError("holdout assignment inputs are invalid")
    identity = json.dumps(
        [repository_id, task_id], ensure_ascii=False, separators=(",", ":")
    ).encode()
    digest = hmac.new(
        assignment_key,
        b"loopguard-holdout-v1\0" + identity,
        hashlib.sha256,
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    return "routed" if bucket < percentage else "control"


def evaluate_outcomes(
    outcomes: list[RoutingOutcome],
    *,
    minimum_sample_size: int = 30,
) -> EvaluationReport:
    if not 1 <= minimum_sample_size <= 1_000_000:
        raise ValueError("minimum sample size is invalid")
    if len(outcomes) > 1_000_000:
        raise ValueError("outcome evaluation exceeds its sample limit")
    control = _metrics([item for item in outcomes if item.experiment_group == "control"])
    routed = _metrics([item for item in outcomes if item.experiment_group == "routed"])
    enough = (
        control.sample_size >= minimum_sample_size and routed.sample_size >= minimum_sample_size
    )
    recommendation: Literal["enable_candidate", "keep_shadow", "insufficient_evidence"]
    if not enough:
        recommendation = "insufficient_evidence"
    elif _candidate_is_supported(control, routed):
        recommendation = "enable_candidate"
    else:
        recommendation = "keep_shadow"
    return EvaluationReport(
        control=control,
        routed=routed,
        minimum_sample_size=minimum_sample_size,
        minimum_sample_met=enough,
        recommendation=recommendation,
    )


def _metrics(outcomes: list[RoutingOutcome]) -> GroupMetrics:
    verified = [item for item in outcomes if item.verified is not None]
    successes = sum(item.verified is True for item in verified)
    regressions = sum(
        bool(item.regressions) or item.verification_verdict == "regression" for item in outcomes
    )
    totals = [item.total_cost for item in outcomes]
    observed_total = (
        sum((value for value in totals if value is not None), Decimal("0"))
        if outcomes and all(value is not None for value in totals)
        else None
    )
    mean_wall = sum(item.wall_time_ms for item in outcomes) / len(outcomes) if outcomes else None
    return GroupMetrics(
        sample_size=len(outcomes),
        verified_count=len(verified),
        verified_success=_rate(successes, len(verified)),
        regression_rate=_rate(regressions, len(outcomes)),
        observed_total_cost=observed_total,
        mean_wall_time_ms=mean_wall,
        user_interventions=sum(item.interruptions + item.repairs for item in outcomes),
    )


def _rate(numerator: int, denominator: int) -> RateEstimate:
    if denominator == 0:
        return RateEstimate(numerator=0, denominator=0)
    value = numerator / denominator
    return RateEstimate(
        numerator=numerator,
        denominator=denominator,
        value=value,
        confidence_interval=_wilson(numerator, denominator),
    )


def _wilson(numerator: int, denominator: int) -> ConfidenceInterval:
    z = 1.959963984540054
    proportion = numerator / denominator
    denominator_adjusted = 1 + (z * z / denominator)
    center = (proportion + z * z / (2 * denominator)) / denominator_adjusted
    margin = (
        z
        * math.sqrt(
            (proportion * (1 - proportion) / denominator)
            + (z * z / (4 * denominator * denominator))
        )
        / denominator_adjusted
    )
    return ConfidenceInterval(lower=max(0, center - margin), upper=min(1, center + margin))


def _candidate_is_supported(control: GroupMetrics, routed: GroupMetrics) -> bool:
    control_rate = control.verified_success.value
    routed_rate = routed.verified_success.value
    if control_rate is None or routed_rate is None or routed_rate < control_rate:
        return False
    if (
        control.observed_total_cost is None
        or routed.observed_total_cost is None
        or routed.observed_total_cost > control.observed_total_cost
    ):
        return False
    routed_interval = routed.verified_success.confidence_interval
    control_interval = control.verified_success.confidence_interval
    return (
        routed_interval is not None
        and control_interval is not None
        and routed_interval.lower >= control_interval.lower
    )
