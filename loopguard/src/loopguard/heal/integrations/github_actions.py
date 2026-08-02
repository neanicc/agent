from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from loopguard.heal.integrations.airflow import _mapping, _text


def extract(payload: Mapping[str, object]) -> dict[str, Any]:
    repository = _mapping(payload.get("repository"), "github repository")
    workflow = _mapping(payload.get("workflow"), "github workflow")
    job = _mapping(payload.get("job"), "github job")
    check = _mapping(payload.get("check_run"), "github check run")
    annotations = check.get("annotations")
    if (
        not isinstance(annotations, Sequence)
        or isinstance(annotations, (str, bytes, bytearray))
        or not annotations
        or not isinstance(annotations[0], Mapping)
    ):
        raise ValueError("github check annotation is required")
    annotation = annotations[0]
    check_id = check.get("id")
    if not isinstance(check_id, (str, int)):
        raise ValueError("github check run ID is invalid")
    return {
        "source_event_id": str(check_id),
        "repo_id": _text(repository.get("node_id"), "github repository"),
        "revision": _text(check.get("head_sha"), "github head SHA"),
        "pipeline": _text(workflow.get("name"), "github workflow"),
        "step": _text(job.get("name"), "github job"),
        "error_type": _text(annotation.get("title"), "github annotation title"),
        "message": _text(
            annotation.get("message"),
            "github annotation message",
            maximum=16_384,
        ),
        "frames": [
            {
                "file": annotation.get("path"),
                "function": "github_check",
                "line": annotation.get("start_line"),
            }
        ],
        "schema": _mapping(payload.get("schema"), "github schema"),
        "stack_trace_artifact_id": None,
        "fixture_artifact_id": None,
    }
