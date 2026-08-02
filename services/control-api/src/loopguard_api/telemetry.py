from __future__ import annotations

import re
import uuid
from typing import Any

from loopguard.telemetry import NoOpTelemetrySink, SpanRecord, Telemetry, TelemetrySink


_UUID_PATH = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}(?=/|$)"
)
_OPAQUE_ACTION = re.compile(r"/act_[A-Za-z0-9_-]+(?=/|$)")


class ControlApiTelemetry:
    def __init__(self, sink: TelemetrySink | None = None, *, enabled: bool = True) -> None:
        self.telemetry = Telemetry(sink or NoOpTelemetrySink(), enabled=enabled)

    def record_request(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        tenant_id: uuid.UUID | None = None,
    ) -> SpanRecord:
        route = bounded_route(path)
        attributes: dict[str, Any] = {
            "loopguard.request_id": request_id,
            "http.request.method": method,
            "http.route": route,
            "http.response.status_code": status_code,
        }
        if tenant_id is not None:
            attributes["loopguard.tenant_id"] = str(tenant_id)
        span = self.telemetry.record_span(
            "loopguard.http.request",
            attributes,
            duration_ms=duration_ms,
            status="ok" if status_code < 500 else "error",
        )
        self.telemetry.record_metric(
            "loopguard.http.server.duration",
            duration_ms,
            labels={
                "method": method,
                "route": route,
                "status_class": f"{status_code // 100}xx",
            },
        )
        return span

    def record_workflow(
        self,
        *,
        workflow_id: str,
        workflow: str,
        outcome: str,
        duration_ms: float,
    ) -> SpanRecord:
        span = self.telemetry.record_span(
            "loopguard.workflow.run",
            {
                "loopguard.workflow_id": workflow_id,
                "loopguard.workflow": workflow,
                "loopguard.outcome": outcome,
            },
            duration_ms=duration_ms,
            status="ok" if outcome == "completed" else "error",
        )
        self.telemetry.record_metric(
            "loopguard.workflow.duration",
            duration_ms,
            labels={"workflow": workflow, "outcome": outcome},
        )
        return span


def bounded_route(path: str) -> str:
    normalized = _OPAQUE_ACTION.sub("/{action_id}", _UUID_PATH.sub("/{id}", path))
    if len(normalized) > 128 or not normalized.startswith("/"):
        return "/unknown"
    return normalized
