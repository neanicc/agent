from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from loopguard.telemetry import InMemoryTelemetrySink
from loopguard_api.app import create_app
from loopguard_api.settings import Settings
from loopguard_api.telemetry import ControlApiTelemetry


def test_request_span_propagates_ids_without_raw_resource_path() -> None:
    sink = InMemoryTelemetrySink()
    telemetry = ControlApiTelemetry(sink)
    resource_id = uuid.uuid4()

    span = telemetry.record_request(
        request_id="req-1",
        method="GET",
        path=f"/v1/sessions/{resource_id}",
        status_code=200,
        duration_ms=12.5,
        tenant_id=uuid.uuid4(),
    )

    assert span.attributes["loopguard.request_id"] == "req-1"
    assert span.attributes["http.route"] == "/v1/sessions/{id}"
    assert str(resource_id) not in sink.metrics[0].labels["route"]
    assert set(sink.metrics[0].labels) == {"method", "route", "status_class"}


def test_workflow_span_uses_ids_and_bounded_outcome_only() -> None:
    sink = InMemoryTelemetrySink()
    telemetry = ControlApiTelemetry(sink)

    span = telemetry.record_workflow(
        workflow_id="repair-123",
        workflow="repair",
        outcome="completed",
        duration_ms=100,
    )

    assert span.attributes == {
        "loopguard.workflow_id": "repair-123",
        "loopguard.workflow": "repair",
        "loopguard.outcome": "completed",
    }
    assert sink.metrics[0].labels == {
        "workflow": "repair",
        "outcome": "completed",
    }


def test_api_middleware_records_route_templates_and_unmatched_paths() -> None:
    sink = InMemoryTelemetrySink()
    client = TestClient(
        create_app(
            Settings.for_test(),
            telemetry=ControlApiTelemetry(sink),
        )
    )

    assert client.get("/health").status_code == 200
    assert client.get("/attacker-controlled-cardinality").status_code == 404

    assert [metric.labels["route"] for metric in sink.metrics] == [
        "/health",
        "/unmatched",
    ]
