from __future__ import annotations

import gzip
import io
import json
import threading
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field

from loopguard.heal.intake import FailurePayloadRejected, normalize_failure
from loopguard.heal.models import FailureEvent

from ..errors import ApiProblem
from ..hook_ingest import HookRejected, HookService, VerifiedHook


router = APIRouter(prefix="/v1", tags=["repair-intake"])


@dataclass(frozen=True, slots=True)
class RepairIntakeRecord:
    repair_id: uuid.UUID
    tenant_id: uuid.UUID
    repository_handle: str
    failure: FailureEvent


class RepairIntakeRegistry:
    """Atomic active-repair deduplication; durable storage replaces this adapter in hosted use."""

    def __init__(self) -> None:
        self._records: dict[tuple[uuid.UUID, str, str], RepairIntakeRecord] = {}
        self._lock = threading.Lock()

    def accept(
        self,
        verified: VerifiedHook,
        failure: FailureEvent,
    ) -> tuple[RepairIntakeRecord, bool]:
        if failure.fingerprint is None:
            raise ValueError("normalized failure fingerprint is required")
        key = (verified.tenant_id, verified.repository_handle, failure.fingerprint)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing, True
            record = RepairIntakeRecord(
                repair_id=uuid.uuid4(),
                tenant_id=verified.tenant_id,
                repository_handle=verified.repository_handle,
                failure=failure.model_copy(update={"tenant_id": str(verified.tenant_id)}),
            )
            self._records[key] = record
            return record, False


class RepairIntakeAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["accepted"] = "accepted"
    repair_id: uuid.UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    duplicate: bool


@router.post(
    "/repair-intake",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RepairIntakeAccepted,
)
async def ingest_repair_failure(
    request: Request,
    x_loopguard_key_id: str = Header(),
    x_loopguard_timestamp: str = Header(),
    x_loopguard_nonce: str = Header(),
    x_loopguard_repository: str = Header(),
    x_loopguard_signature: str = Header(),
    content_encoding: str | None = Header(default=None),
) -> RepairIntakeAccepted:
    raw_body = await request.body()
    service: HookService = request.app.state.hook_service
    try:
        verified = service.verify(
            method=request.method,
            path=request.url.path,
            body=raw_body,
            key_id=x_loopguard_key_id,
            timestamp=x_loopguard_timestamp,
            nonce=x_loopguard_nonce,
            repository_handle=x_loopguard_repository,
            signature=x_loopguard_signature,
            required_scope="repair:intake",
        )
    except HookRejected as exc:
        raise _hook_problem(exc) from exc

    maximum_bytes = request.app.state.settings.max_request_bytes
    try:
        body = _decode_body(raw_body, content_encoding, maximum_bytes)
        payload = _parse_json(body)
        source = payload.get("source")
        event = payload.get("event")
        if not isinstance(source, str) or not isinstance(event, dict):
            raise FailurePayloadRejected("source and event are required")
        failure = normalize_failure(source, event)
    except _DecodedBodyTooLarge as exc:
        raise ApiProblem("LGAPI-BODY-TOO-LARGE") from exc
    except (FailurePayloadRejected, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ApiProblem("LGAPI-REQUEST-INVALID", field="body.event") from exc

    if failure.repo_id != verified.repository_handle:
        raise ApiProblem("LGAPI-HOOK-BINDING")
    registry: RepairIntakeRegistry = request.app.state.repair_intake_registry
    record, duplicate = registry.accept(verified, failure)
    return RepairIntakeAccepted(
        repair_id=record.repair_id,
        fingerprint=failure.fingerprint,
        duplicate=duplicate,
    )


class _DecodedBodyTooLarge(ValueError):
    pass


def _decode_body(raw: bytes, encoding: str | None, maximum_bytes: int) -> bytes:
    normalized = (encoding or "identity").strip().lower()
    if normalized in {"", "identity"}:
        return raw
    if normalized != "gzip":
        raise ValueError("content encoding is not supported")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as compressed:
            body = compressed.read(maximum_bytes + 1)
    except (OSError, EOFError) as exc:
        raise ValueError("gzip body is invalid") from exc
    if len(body) > maximum_bytes:
        raise _DecodedBodyTooLarge("decompressed body exceeds the request limit")
    return body


def _parse_json(body: bytes) -> dict[str, object]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value} is forbidden")

    def bounded_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        if len(pairs) > 128:
            raise ValueError("JSON object has too many keys")
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result or len(key) > 256 or "\x00" in key:
                raise ValueError("JSON object key is invalid")
            result[key] = value
        return result

    parsed = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=bounded_object,
        parse_constant=reject_constant,
    )
    if not isinstance(parsed, dict):
        raise ValueError("request body must be an object")
    _validate_json_value(parsed, depth=0)
    return parsed


def _validate_json_value(value: object, *, depth: int) -> None:
    if depth > 10:
        raise ValueError("JSON nesting is too deep")
    if isinstance(value, dict):
        for child in value.values():
            _validate_json_value(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 256:
            raise ValueError("JSON array is too large")
        for child in value:
            _validate_json_value(child, depth=depth + 1)
    elif isinstance(value, str) and (len(value) > 65_536 or "\x00" in value):
        raise ValueError("JSON string is invalid")


def _hook_problem(exc: HookRejected) -> ApiProblem:
    message = str(exc)
    if "replay" in message:
        return ApiProblem("LGAPI-HOOK-REPLAY")
    if "repository binding" in message or "scope" in message:
        return ApiProblem("LGAPI-HOOK-BINDING")
    return ApiProblem("LGAPI-HOOK-INVALID")
