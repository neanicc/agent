from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def extract(payload: Mapping[str, object]) -> dict[str, Any]:
    exception = _mapping(payload.get("exception"), "airflow exception")
    return {
        "source_event_id": _text(payload.get("run_id"), "airflow run ID"),
        "repo_id": _text(payload.get("repository"), "airflow repository"),
        "revision": _text(payload.get("revision"), "airflow revision"),
        "pipeline": _text(payload.get("dag_id"), "airflow DAG"),
        "step": _text(payload.get("task_id"), "airflow task"),
        "error_type": _text(exception.get("type"), "airflow exception type"),
        "message": _text(exception.get("message"), "airflow exception message", maximum=16_384),
        "frames": exception.get("frames"),
        "schema": _mapping(payload.get("schema"), "airflow schema"),
        "stack_trace_artifact_id": _optional_text(payload.get("log_artifact_id")),
        "fixture_artifact_id": _optional_text(payload.get("fixture_artifact_id")),
    }


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} is required")
    return value


def _text(value: object, field: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value, "airflow artifact ID")
