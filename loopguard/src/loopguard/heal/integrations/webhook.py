from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loopguard.heal.integrations.airflow import _mapping, _optional_text, _text


def extract(payload: Mapping[str, object]) -> dict[str, Any]:
    return {
        "source_event_id": _text(payload.get("event_id"), "webhook event ID"),
        "repo_id": _text(payload.get("repo_id"), "webhook repository"),
        "revision": _text(payload.get("revision"), "webhook revision"),
        "pipeline": _text(payload.get("pipeline"), "webhook pipeline"),
        "step": _text(payload.get("step"), "webhook step"),
        "error_type": _text(payload.get("error_type"), "webhook error type"),
        "message": _text(payload.get("message"), "webhook message", maximum=16_384),
        "frames": payload.get("frames"),
        "schema": _mapping(payload.get("schema"), "webhook schema"),
        "stack_trace_artifact_id": _optional_text(payload.get("stack_trace_artifact_id")),
        "fixture_artifact_id": _optional_text(payload.get("fixture_artifact_id")),
    }
