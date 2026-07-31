from __future__ import annotations

from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.telemetry import InMemoryTelemetrySink, Telemetry


def event() -> ControlEvent:
    return ControlEvent(
        event_id="event-1",
        kind=EventKind.TOOL_CALL,
        source="codex",
        session=SessionRef(
            host_id="host-1",
            repo_id="repo-1",
            session_id="session-1",
        ),
        payload={
            "prompt": "secret prompt",
            "tool_args": {"authorization": "Bearer secret"},
        },
    )


def test_event_span_contains_ids_not_payload() -> None:
    sink = InMemoryTelemetrySink()
    telemetry = Telemetry(sink)

    span = telemetry.record_event(event())

    assert span.attributes["loopguard.event_id"] == "event-1"
    assert "payload" not in span.attributes
    assert "tool_args" not in span.attributes
    assert "secret prompt" not in str(span)
    assert sink.spans == [span]


def test_forbidden_attributes_and_unbounded_metric_labels_are_rejected() -> None:
    telemetry = Telemetry(InMemoryTelemetrySink())

    for values in (
        {"loopguard.payload": "raw"},
        {"loopguard.authorization": "Bearer secret"},
        {"loopguard.tool_args": "{}"},
    ):
        try:
            telemetry.record_span("loopguard.test", values, duration_ms=1, status="ok")
        except ValueError:
            pass
        else:
            raise AssertionError("sensitive telemetry attribute was accepted")

    try:
        telemetry.record_metric(
            "loopguard.events",
            1,
            labels={"tenant_id": "high-cardinality-secret"},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unbounded metric label was accepted")


def test_telemetry_is_disabled_by_default_at_the_configuration_layer() -> None:
    sink = InMemoryTelemetrySink()
    Telemetry(sink, enabled=False).record_event(event())

    assert sink.spans == []
