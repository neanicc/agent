from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


BrowserMetricName = Literal[
    "cold_start_ms",
    "warm_context_ms",
    "impacted_suite_ms",
    "full_suite_ms",
]
_METRICS = {
    "cold_start_ms",
    "warm_context_ms",
    "impacted_suite_ms",
    "full_suite_ms",
}


@dataclass
class _Metric:
    durations: list[float] = field(default_factory=list)
    selected_tests: int = 0
    total_tests: int = 0
    cache_hits: int = 0
    retries: int = 0
    trace_bytes: int = 0


class BrowserMetricsRecorder:
    """Collect bounded local browser timing and selection-correctness evidence."""

    def __init__(self, *, max_samples_per_metric: int = 100_000) -> None:
        if not 1 <= max_samples_per_metric <= 1_000_000:
            raise ValueError("browser metric sample limit is invalid")
        self.max_samples_per_metric = max_samples_per_metric
        self._metrics: dict[str, _Metric] = {}
        self._full_gate_runs = 0
        self._selection_escapes = 0

    def observe(
        self,
        name: BrowserMetricName,
        duration_ms: float,
        *,
        selected_tests: int | None = None,
        total_tests: int | None = None,
        cache_hit: bool = False,
        retry: bool = False,
        trace_bytes: int = 0,
    ) -> None:
        if name not in _METRICS:
            raise ValueError("browser metric name is invalid")
        duration = _duration(duration_ms)
        selected, total = _selection_counts(selected_tests, total_tests)
        if not isinstance(cache_hit, bool) or not isinstance(retry, bool):
            raise ValueError("browser metric flags must be booleans")
        if (
            not isinstance(trace_bytes, int)
            or isinstance(trace_bytes, bool)
            or not 0 <= trace_bytes <= 1_000_000_000_000
        ):
            raise ValueError("browser trace byte count is invalid")
        metric = self._metrics.setdefault(name, _Metric())
        if len(metric.durations) >= self.max_samples_per_metric:
            raise ValueError("browser metric sample limit exceeded")
        metric.durations.append(duration)
        metric.selected_tests += selected
        metric.total_tests += total
        metric.cache_hits += int(cache_hit)
        metric.retries += int(retry)
        metric.trace_bytes += trace_bytes

    def record_full_gate(self, *, selection_escape: bool) -> None:
        if not isinstance(selection_escape, bool):
            raise ValueError("selection escape must be a boolean")
        self._full_gate_runs += 1
        self._selection_escapes += int(selection_escape)

    def report(self) -> dict[str, dict[str, int | float | None]]:
        escape_rate = (
            self._selection_escapes / self._full_gate_runs
            if self._full_gate_runs
            else None
        )
        report: dict[str, dict[str, int | float | None]] = {}
        for name in sorted(self._metrics):
            metric = self._metrics[name]
            durations = sorted(metric.durations)
            mean = math.fsum(durations) / len(durations)
            variance = math.fsum((value - mean) ** 2 for value in durations) / len(
                durations
            )
            report[name] = {
                "samples": len(durations),
                "p50_ms": _percentile(durations, 0.50),
                "p95_ms": _percentile(durations, 0.95),
                "mean_ms": mean,
                "stddev_ms": math.sqrt(variance),
                "selected_tests": metric.selected_tests,
                "total_tests": metric.total_tests,
                "cache_hits": metric.cache_hits,
                "retries": metric.retries,
                "trace_bytes": metric.trace_bytes,
                "full_gate_runs": self._full_gate_runs,
                "selection_escapes": self._selection_escapes,
                "selection_escape_rate": escape_rate,
            }
        return report


class BrowserAccelerationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    reason: str
    samples_after_warmup: int = Field(ge=0)
    reuse_p50_improvement_percent: float | None = None
    reuse_p95_improvement_percent: float | None = None
    selection_p50_improvement_percent: float | None = None
    selection_p95_improvement_percent: float | None = None
    selection_escape_rate: float | None = Field(default=None, ge=0, le=1)


class BrowserBenchmarkPolicy:
    """Enable warm-browser reuse only from paired native evidence with safety parity."""

    def __init__(
        self,
        *,
        minimum_samples: int = 30,
        warmup_samples: int = 5,
        material_improvement_percent: float = 20,
    ) -> None:
        if not 1 <= minimum_samples <= 10_000:
            raise ValueError("browser benchmark sample requirement is invalid")
        if not 0 <= warmup_samples <= 1_000:
            raise ValueError("browser benchmark warm-up is invalid")
        if not 0 < material_improvement_percent < 100:
            raise ValueError("browser benchmark materiality threshold is invalid")
        self.minimum_samples = minimum_samples
        self.warmup_samples = warmup_samples
        self.material_improvement_percent = float(material_improvement_percent)

    def evaluate(
        self,
        *,
        native_repeated_ms: Sequence[float],
        broker_repeated_ms: Sequence[float],
        impacted_suite_ms: Sequence[float],
        full_suite_ms: Sequence[float],
        full_gate_runs: int,
        selection_escapes: int,
        baseline_escape_rate: float,
        isolation_failures: int = 0,
        reporter_regressions: int = 0,
        trace_regressions: int = 0,
    ) -> BrowserAccelerationDecision:
        series = [
            _series(native_repeated_ms),
            _series(broker_repeated_ms),
            _series(impacted_suite_ms),
            _series(full_suite_ms),
        ]
        trimmed = [values[self.warmup_samples :] for values in series]
        samples = min(len(values) for values in trimmed)
        trimmed = [values[:samples] for values in trimmed]
        _non_negative_count(full_gate_runs, "full gate runs")
        _non_negative_count(selection_escapes, "selection escapes")
        _non_negative_count(isolation_failures, "isolation failures")
        _non_negative_count(reporter_regressions, "reporter regressions")
        _non_negative_count(trace_regressions, "trace regressions")
        if selection_escapes > full_gate_runs:
            raise ValueError("selection escapes cannot exceed full gate runs")
        if not math.isfinite(baseline_escape_rate) or not 0 <= baseline_escape_rate <= 1:
            raise ValueError("baseline selection escape rate is invalid")
        if samples < self.minimum_samples:
            return BrowserAccelerationDecision(
                enabled=False,
                reason="insufficient_samples",
                samples_after_warmup=samples,
            )
        if full_gate_runs < self.minimum_samples:
            return BrowserAccelerationDecision(
                enabled=False,
                reason="insufficient_correctness_samples",
                samples_after_warmup=samples,
            )

        native, broker, impacted, full = trimmed
        reuse_p50 = _improvement(native, broker, 0.50)
        reuse_p95 = _improvement(native, broker, 0.95)
        selection_p50 = _improvement(full, impacted, 0.50)
        selection_p95 = _improvement(full, impacted, 0.95)
        escape_rate = selection_escapes / full_gate_runs
        reason = "material_improvement_verified"
        enabled = True
        if isolation_failures:
            enabled, reason = False, "isolation_failure"
        elif reporter_regressions:
            enabled, reason = False, "reporter_regression"
        elif trace_regressions:
            enabled, reason = False, "trace_regression"
        elif escape_rate > baseline_escape_rate:
            enabled, reason = False, "selection_escape_rate_increased"
        elif min(reuse_p50, reuse_p95) < self.material_improvement_percent:
            enabled, reason = False, "improvement_below_threshold"
        return BrowserAccelerationDecision(
            enabled=enabled,
            reason=reason,
            samples_after_warmup=samples,
            reuse_p50_improvement_percent=reuse_p50,
            reuse_p95_improvement_percent=reuse_p95,
            selection_p50_improvement_percent=selection_p50,
            selection_p95_improvement_percent=selection_p95,
            selection_escape_rate=escape_rate,
        )


def _duration(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("browser duration must be numeric")
    duration = float(value)
    if not math.isfinite(duration) or not 0 <= duration <= 86_400_000:
        raise ValueError("browser duration is invalid")
    return duration


def _selection_counts(selected: int | None, total: int | None) -> tuple[int, int]:
    if selected is None and total is None:
        return 0, 0
    if selected is None or total is None:
        raise ValueError("selected and total test counts must be recorded together")
    _non_negative_count(selected, "selected test count")
    _non_negative_count(total, "total test count")
    if selected > total:
        raise ValueError("selected test count cannot exceed total")
    return selected, total


def _non_negative_count(value: int, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1_000_000_000:
        raise ValueError(f"{label} is invalid")


def _series(values: Sequence[float]) -> list[float]:
    if isinstance(values, (str, bytes)) or len(values) > 100_000:
        raise ValueError("browser benchmark series is invalid")
    return [_duration(value) for value in values]


def _percentile(values: Sequence[float], quantile: float) -> float:
    return values[max(0, math.ceil(quantile * len(values)) - 1)]


def _improvement(baseline: Sequence[float], candidate: Sequence[float], quantile: float) -> float:
    baseline_value = _percentile(sorted(baseline), quantile)
    candidate_value = _percentile(sorted(candidate), quantile)
    if baseline_value == 0:
        return 0.0 if candidate_value == 0 else -100.0
    return round((baseline_value - candidate_value) / baseline_value * 100, 3)
