from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Protocol

from .control.events import ControlEvent


_FORBIDDEN_ATTRIBUTE_PARTS = (
    "payload",
    "prompt",
    "source_code",
    "tool_args",
    "tool_output",
    "token",
    "secret",
    "credential",
    "authorization",
)
_ATTRIBUTE_KEY = re.compile(r"^[a-z][a-z0-9_.]{0,127}$")


@dataclass(frozen=True, slots=True)
class SpanRecord:
    name: str
    attributes: Mapping[str, str | int | float | bool]
    duration_ms: float
    status: str


@dataclass(frozen=True, slots=True)
class MetricRecord:
    name: str
    value: float
    labels: Mapping[str, str]


class TelemetrySink(Protocol):
    def emit_span(self, span: SpanRecord) -> None: ...

    def emit_metric(self, metric: MetricRecord) -> None: ...


@dataclass(slots=True)
class InMemoryTelemetrySink:
    spans: list[SpanRecord] = field(default_factory=list)
    metrics: list[MetricRecord] = field(default_factory=list)

    def emit_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def emit_metric(self, metric: MetricRecord) -> None:
        self.metrics.append(metric)


class NoOpTelemetrySink:
    def emit_span(self, span: SpanRecord) -> None:
        del span

    def emit_metric(self, metric: MetricRecord) -> None:
        del metric


class Telemetry:
    def __init__(self, sink: TelemetrySink | None = None, *, enabled: bool = True) -> None:
        self.sink = sink or NoOpTelemetrySink()
        self.enabled = enabled

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, Any],
    ) -> Iterator[None]:
        started = time.monotonic()
        status = "ok"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            self.record_span(
                name,
                attributes,
                duration_ms=(time.monotonic() - started) * 1_000,
                status=status,
            )

    def record_event(self, event: ControlEvent) -> SpanRecord:
        return self.record_span(
            "loopguard.event.persist",
            {
                "loopguard.event_id": event.event_id,
                "loopguard.event_kind": event.kind.value,
                "loopguard.host_id": event.session.host_id,
                "loopguard.repo_id": event.session.repo_id,
                "loopguard.session_id": event.session.session_id,
            },
            duration_ms=0,
            status="ok",
        )

    def record_span(
        self,
        name: str,
        attributes: Mapping[str, Any],
        *,
        duration_ms: float,
        status: str,
    ) -> SpanRecord:
        span = SpanRecord(
            name=_safe_name(name),
            attributes=_safe_attributes(attributes),
            duration_ms=max(0, float(duration_ms)),
            status=status if status in {"ok", "error", "cancelled"} else "error",
        )
        if self.enabled:
            self.sink.emit_span(span)
        return span

    def record_metric(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str],
    ) -> MetricRecord:
        safe_labels = _safe_metric_labels(labels)
        metric = MetricRecord(_safe_name(name), float(value), safe_labels)
        if self.enabled:
            self.sink.emit_metric(metric)
        return metric


class OpenTelemetrySink:
    """Optional SDK bridge; importing LoopGuard never requires the OTel SDK."""

    def __init__(self, *, service_name: str) -> None:
        from opentelemetry import metrics, trace

        self.tracer = trace.get_tracer(service_name)
        self.meter = metrics.get_meter(service_name)
        self._histograms: dict[str, Any] = {}

    def emit_span(self, span: SpanRecord) -> None:
        with self.tracer.start_as_current_span(span.name) as current:
            for key, value in span.attributes.items():
                current.set_attribute(key, value)
            current.set_attribute("loopguard.duration_ms", span.duration_ms)
            current.set_attribute("loopguard.status", span.status)

    def emit_metric(self, metric: MetricRecord) -> None:
        histogram = self._histograms.setdefault(
            metric.name,
            self.meter.create_histogram(metric.name),
        )
        histogram.record(metric.value, attributes=dict(metric.labels))


def _safe_name(value: str) -> str:
    if not _ATTRIBUTE_KEY.fullmatch(value):
        raise ValueError("telemetry name is invalid")
    return value


def _safe_attributes(
    values: Mapping[str, Any],
) -> dict[str, str | int | float | bool]:
    safe: dict[str, str | int | float | bool] = {}
    for key, value in values.items():
        lowered = key.lower()
        if (
            not _ATTRIBUTE_KEY.fullmatch(key)
            or any(part in lowered for part in _FORBIDDEN_ATTRIBUTE_PARTS)
        ):
            raise ValueError(f"telemetry attribute {key!r} is prohibited")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"telemetry attribute {key!r} has an unsupported value")
        if isinstance(value, str) and len(value) > 256:
            raise ValueError(f"telemetry attribute {key!r} is too long")
        safe[key] = value
    return safe


def _safe_metric_labels(values: Mapping[str, str]) -> dict[str, str]:
    allowed = {
        "category",
        "kind",
        "method",
        "outcome",
        "route",
        "status_class",
        "workflow",
    }
    if not set(values).issubset(allowed):
        raise ValueError("metric labels may contain only bounded dimensions")
    return _safe_attributes(values)  # type: ignore[return-value]
