"""Normalize untrusted orchestrator failures into bounded repair evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Literal, cast

from pydantic import ValidationError

from loopguard.heal.fingerprint import (
    FingerprintInputRejected,
    failure_fingerprint,
    normalize_stack_frames,
)
from loopguard.heal.integrations import airflow, github_actions, openlineage, webhook
from loopguard.heal.models import FailureEvent

FailureSource = Literal["airflow", "openlineage", "github_actions", "webhook"]


class FailurePayloadRejected(ValueError):
    pass


_ADAPTERS: dict[str, Callable[[Mapping[str, object]], dict[str, object]]] = {
    "airflow": airflow.extract,
    "openlineage": openlineage.extract,
    "github_actions": github_actions.extract,
    "webhook": webhook.extract,
}


def normalize_failure(source: FailureSource | str, payload: Mapping[str, object]) -> FailureEvent:
    adapter = _ADAPTERS.get(source)
    if adapter is None:
        raise FailurePayloadRejected("failure source is not supported")
    if not isinstance(payload, Mapping) or len(payload) > 128:
        raise FailurePayloadRejected(f"{source} payload must be a bounded object")
    try:
        extracted = adapter(payload)
        frames = normalize_stack_frames(extracted.pop("frames"))
        schema = extracted.pop("schema")
        if not isinstance(schema, Mapping) or len(schema) > 512:
            raise FailurePayloadRejected(f"{source} schema must be a bounded object")
        fingerprint = failure_fingerprint(
            repo_id=cast(str, extracted["repo_id"]),
            revision=cast(str, extracted["revision"]),
            pipeline=cast(str, extracted["pipeline"]),
            step=cast(str, extracted["step"]),
            exception_type=cast(str, extracted["error_type"]),
            frames=frames,
            schema=schema,
        )
        source_event_id = cast(str, extracted.pop("source_event_id"))
        failure_id = f"{source}-{hashlib.sha256(source_event_id.encode()).hexdigest()[:24]}"
        return FailureEvent.model_validate(
            {
                **extracted,
                "failure_id": failure_id,
                "source": source,
                "fingerprint": fingerprint,
                "schema_artifact_ids": (),
            }
        )
    except (FingerprintInputRejected, ValidationError, ValueError, KeyError) as exc:
        if isinstance(exc, FailurePayloadRejected):
            raise
        raise FailurePayloadRejected(f"{source} failure payload is invalid: {exc}") from exc
