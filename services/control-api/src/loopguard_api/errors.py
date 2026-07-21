from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse


DOC_ROOT = "https://docs.loopguard.dev/reference/control-api-errors"


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    status: int
    title: str
    detail: str
    retryable: bool = False


ERROR_CATALOG: dict[str, ErrorDefinition] = {
    "LGAPI-ACTION-CONFLICT": ErrorDefinition(409, "Action state conflict", "The action cannot transition from its current state."),
    "LGAPI-BODY-TOO-LARGE": ErrorDefinition(413, "Request body too large", "The request exceeds the documented size limit."),
    "LGAPI-CSRF-REQUIRED": ErrorDefinition(403, "CSRF proof required", "Cookie-authenticated changes require an allowed origin and matching CSRF proof."),
    "LGAPI-DEVICE-PROOF-REQUIRED": ErrorDefinition(401, "Device proof required", "A current registered device signature is required for this action."),
    "LGAPI-FORBIDDEN": ErrorDefinition(403, "Operation forbidden", "The authenticated principal is not allowed to perform this operation."),
    "LGAPI-HOST-UNTRUSTED": ErrorDefinition(400, "Untrusted host", "The HTTP host is not in the deployment allowlist."),
    "LGAPI-HOOK-BINDING": ErrorDefinition(403, "Hook repository denied", "The hook credential is not bound to the requested repository."),
    "LGAPI-HOOK-INVALID": ErrorDefinition(401, "Hook authentication failed", "The hook request could not be authenticated."),
    "LGAPI-HOOK-REPLAY": ErrorDefinition(409, "Hook replay rejected", "The hook nonce has already been consumed."),
    "LGAPI-INTERNAL": ErrorDefinition(500, "Internal service error", "The service could not complete the request.", True),
    "LGAPI-METHOD-NOT-ALLOWED": ErrorDefinition(405, "Method not allowed", "The resource does not support this HTTP method."),
    "LGAPI-NOT-FOUND": ErrorDefinition(404, "Resource not found", "The requested resource was not found."),
    "LGAPI-ORIGIN-DENIED": ErrorDefinition(403, "Origin denied", "The browser origin is not in the exact deployment allowlist."),
    "LGAPI-PAIRING-CONFLICT": ErrorDefinition(409, "Pairing code unavailable", "The pairing code is unknown or has already been consumed."),
    "LGAPI-PAIRING-EXPIRED": ErrorDefinition(410, "Pairing code expired", "The one-time host pairing code has expired."),
    "LGAPI-PROXY-UNTRUSTED": ErrorDefinition(400, "Untrusted proxy headers", "Forwarded headers are accepted only from a configured proxy."),
    "LGAPI-REQUEST-INVALID": ErrorDefinition(422, "Request validation failed", "One or more request fields are invalid."),
    "LGAPI-UNAUTHORIZED": ErrorDefinition(401, "Authentication required", "Valid authentication is required for this operation."),
    "LGAPI-TIMEOUT": ErrorDefinition(504, "Request timed out", "The request exceeded the service time limit.", True),
}


class ApiProblem(Exception):
    def __init__(
        self,
        code: str,
        *,
        field: str | None = None,
        current_state: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CATALOG:
            raise ValueError("unknown public API error code")
        self.code = code
        self.field = field
        self.current_state = current_state
        super().__init__(code)


def problem_response(
    code: str,
    request_id: str,
    *,
    field: str | None = None,
    current_state: dict[str, Any] | None = None,
) -> JSONResponse:
    definition = ERROR_CATALOG[code]
    body: dict[str, Any] = {
        "type": f"{DOC_ROOT}#{code.lower()}",
        "code": code,
        "title": definition.title,
        "detail": definition.detail,
        "request_id": request_id,
        "retryable": definition.retryable,
        "doc_url": f"{DOC_ROOT}#{code.lower()}",
    }
    if field is not None:
        body["field"] = field
    if current_state is not None:
        body["current_state"] = current_state
    return JSONResponse(
        body,
        status_code=definition.status,
        media_type="application/problem+json",
        headers={"X-Request-ID": request_id},
    )
