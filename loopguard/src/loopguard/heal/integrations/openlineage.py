from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loopguard.heal.integrations.airflow import _mapping, _optional_text, _text


def extract(payload: Mapping[str, object]) -> dict[str, Any]:
    if payload.get("eventType") != "FAIL":
        raise ValueError("openlineage event must be terminal FAIL")
    run = _mapping(payload.get("run"), "openlineage run")
    job = _mapping(payload.get("job"), "openlineage job")
    facets = _mapping(payload.get("facets"), "openlineage facets")
    loopguard = _mapping(facets.get("loopguard"), "openlineage loopguard facet")
    error = _mapping(facets.get("errorMessage"), "openlineage error facet")
    return {
        "source_event_id": _text(run.get("runId"), "openlineage run ID"),
        "repo_id": _text(job.get("namespace"), "openlineage repository"),
        "revision": _text(loopguard.get("revision"), "openlineage revision"),
        "pipeline": _text(job.get("name"), "openlineage job"),
        "step": _text(loopguard.get("step"), "openlineage step"),
        "error_type": _text(error.get("type"), "openlineage error type"),
        "message": _text(error.get("message"), "openlineage error message", maximum=16_384),
        "frames": error.get("stackTrace"),
        "schema": _mapping(loopguard.get("schema"), "openlineage schema"),
        "stack_trace_artifact_id": _optional_text(loopguard.get("stackTraceArtifactId")),
        "fixture_artifact_id": _optional_text(loopguard.get("fixtureArtifactId")),
    }
