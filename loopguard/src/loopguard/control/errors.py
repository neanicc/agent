from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from .redaction import redact


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    message: str
    cause: str
    suggested_commands: tuple[str, ...]
    retryable: bool = False


ERROR_CATALOG: dict[str, ErrorDefinition] = {
    "LGD-DAEMON-001": ErrorDefinition(
        message="The local LoopGuard daemon is unreachable.",
        cause="The owner-only daemon socket is missing or is not accepting connections.",
        suggested_commands=(
            "loopguard daemon start --foreground",
            "loopguard doctor",
        ),
        retryable=True,
    ),
    "LGD-STATE-002": ErrorDefinition(
        message="LoopGuard state permissions are unsafe.",
        cause="The state directory, database, socket, or PID file is not owner-only.",
        suggested_commands=("loopguard doctor", "loopguard config path"),
    ),
    "LGD-SCHEMA-003": ErrorDefinition(
        message="The local state schema needs a compatible migration.",
        cause="The database schema is older or newer than this LoopGuard build supports.",
        suggested_commands=("loopguard doctor", "loopguard daemon start --foreground"),
    ),
    "LGD-TRUST-004": ErrorDefinition(
        message="An integration is waiting for explicit trust.",
        cause="LoopGuard will not activate hooks until the user approves their exact scope.",
        suggested_commands=("loopguard setup --agent auto", "loopguard doctor"),
    ),
    "LGD-CAP-005": ErrorDefinition(
        message="This capability is not available in the current installation.",
        cause="The platform backend or managed service integration has not been verified.",
        suggested_commands=("loopguard daemon start --foreground", "loopguard doctor"),
    ),
    "LGD-DEPS-006": ErrorDefinition(
        message="An optional LoopGuard dependency is missing.",
        cause="The requested command needs a product extra that is not installed.",
        suggested_commands=('pip install "loopguard[control]"', "loopguard doctor"),
    ),
    "LGD-CONFIG-007": ErrorDefinition(
        message="The LoopGuard configuration is invalid.",
        cause="A configuration value is malformed, unsupported, or outside safe bounds.",
        suggested_commands=("loopguard config validate", "loopguard config show"),
    ),
    "LGD-STORE-008": ErrorDefinition(
        message="The local LoopGuard event store failed integrity checks.",
        cause="SQLite integrity, encryption metadata, or durable dispatch state is invalid.",
        suggested_commands=("loopguard doctor", "loopguard config path"),
    ),
}


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    code: str
    message: str
    cause: str
    suggested_commands: tuple[str, ...]
    doc_url: str
    request_id: str | None = None
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "cause": self.cause,
            "suggested_commands": list(self.suggested_commands),
            "doc_url": self.doc_url,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "details": _json_safe(redact(self.details)),
        }


def error_for(
    code: str,
    *,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> ErrorEnvelope:
    try:
        definition = ERROR_CATALOG[code]
    except KeyError as exc:
        raise ValueError(f"unknown LoopGuard error code: {code}") from exc
    return ErrorEnvelope(
        code=code,
        message=definition.message,
        cause=definition.cause,
        suggested_commands=definition.suggested_commands,
        doc_url=f"docs/reference/errors.md#{code.lower()}",
        request_id=request_id,
        retryable=definition.retryable,
        details=details or {},
    )


def render_error(error: ErrorEnvelope, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(error.to_dict(), ensure_ascii=False, separators=(",", ":"))
    fixes = "\n".join(f"  {command}" for command in error.suggested_commands)
    return (
        f"Fix:\n{fixes}\n"
        f"Problem: {error.message}\n"
        f"Cause: {error.cause}\n"
        f"Code: {error.code}\n"
        f"Docs: {error.doc_url}"
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE]"
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    return f"[{type(value).__name__.upper()}]"
