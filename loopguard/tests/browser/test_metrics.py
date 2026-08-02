from __future__ import annotations

import pytest

from loopguard.browser.metrics import BrowserBenchmarkPolicy, BrowserMetricsRecorder


def test_metrics_separate_cold_warm_impacted_and_full() -> None:
    recorder = BrowserMetricsRecorder()
    recorder.observe("cold_start_ms", 1800)
    recorder.observe("warm_context_ms", 70, cache_hit=True)
    recorder.observe("impacted_suite_ms", 2300, selected_tests=4, total_tests=80)
    recorder.observe("full_suite_ms", 47000, selected_tests=80, total_tests=80)
    recorder.record_full_gate(selection_escape=False)

    report = recorder.report()

    assert set(report) == {
        "cold_start_ms",
        "warm_context_ms",
        "impacted_suite_ms",
        "full_suite_ms",
    }
    assert report["warm_context_ms"]["cache_hits"] == 1
    assert report["impacted_suite_ms"]["selected_tests"] == 4
    assert report["impacted_suite_ms"]["total_tests"] == 80
    assert report["full_suite_ms"]["selection_escape_rate"] == 0.0


def test_histograms_publish_distribution_and_correctness_counters() -> None:
    recorder = BrowserMetricsRecorder()
    for value in range(1, 101):
        recorder.observe(
            "warm_context_ms",
            value,
            cache_hit=value % 2 == 0,
            retry=value == 100,
            trace_bytes=value,
        )
    recorder.record_full_gate(selection_escape=False)
    recorder.record_full_gate(selection_escape=True)

    metric = recorder.report()["warm_context_ms"]

    assert metric["samples"] == 100
    assert metric["p50_ms"] == 50
    assert metric["p95_ms"] == 95
    assert metric["mean_ms"] == 50.5
    assert metric["stddev_ms"] > 0
    assert metric["cache_hits"] == 50
    assert metric["retries"] == 1
    assert metric["trace_bytes"] == sum(range(1, 101))
    assert metric["full_gate_runs"] == 2
    assert metric["selection_escapes"] == 1
    assert metric["selection_escape_rate"] == 0.5


def test_metric_input_is_bounded_and_internally_consistent() -> None:
    recorder = BrowserMetricsRecorder(max_samples_per_metric=2)
    with pytest.raises(ValueError):
        recorder.observe("cold_start_ms", float("nan"))
    with pytest.raises(ValueError):
        recorder.observe("impacted_suite_ms", 1, selected_tests=2, total_tests=1)
    recorder.observe("cold_start_ms", 1)
    recorder.observe("cold_start_ms", 2)
    with pytest.raises(ValueError, match="sample limit"):
        recorder.observe("cold_start_ms", 3)


def test_benchmark_enables_only_material_safe_post_warmup_improvement() -> None:
    decision = BrowserBenchmarkPolicy(
        minimum_samples=30,
        warmup_samples=5,
        material_improvement_percent=20,
    ).evaluate(
        native_repeated_ms=[100.0] * 35,
        broker_repeated_ms=[70.0] * 35,
        impacted_suite_ms=[20.0] * 35,
        full_suite_ms=[80.0] * 35,
        full_gate_runs=35,
        selection_escapes=0,
        baseline_escape_rate=0,
    )

    assert decision.enabled is True
    assert decision.reuse_p50_improvement_percent == 30
    assert decision.reuse_p95_improvement_percent == 30
    assert decision.selection_p50_improvement_percent == 75
    assert decision.samples_after_warmup == 30


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"broker_repeated_ms": [90.0] * 35}, "improvement_below_threshold"),
        ({"isolation_failures": 1}, "isolation_failure"),
        ({"reporter_regressions": 1}, "reporter_regression"),
        ({"trace_regressions": 1}, "trace_regression"),
        ({"selection_escapes": 1}, "selection_escape_rate_increased"),
        ({"native_repeated_ms": [100.0] * 34}, "insufficient_samples"),
    ],
)
def test_benchmark_remains_disabled_when_evidence_is_weak_or_unsafe(
    overrides: dict[str, object], reason: str
) -> None:
    inputs: dict[str, object] = {
        "native_repeated_ms": [100.0] * 35,
        "broker_repeated_ms": [70.0] * 35,
        "impacted_suite_ms": [20.0] * 35,
        "full_suite_ms": [80.0] * 35,
        "full_gate_runs": 35,
        "selection_escapes": 0,
        "baseline_escape_rate": 0,
    }
    inputs.update(overrides)

    decision = BrowserBenchmarkPolicy().evaluate(**inputs)

    assert decision.enabled is False
    assert decision.reason == reason
