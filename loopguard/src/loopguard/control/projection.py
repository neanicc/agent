import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from loopguard.event import LoopEvent

from .events import ControlEvent, EventKind


def _parse_tokens(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("tokens must be a finite non-negative whole number")

    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("tokens must be a finite non-negative whole number") from None

    if not numeric.is_finite() or numeric < 0 or numeric != numeric.to_integral_value():
        raise ValueError("tokens must be a finite non-negative whole number")
    return int(numeric)


def _parse_cost_usd(value: object) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("cost_usd must be finite and non-negative")

    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("cost_usd must be finite and non-negative") from None

    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("cost_usd must be finite and non-negative")
    return numeric


def _validate_json_value(value: object, ancestors: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("arguments must contain only finite JSON-compatible values")
        return
    if not isinstance(value, (dict, list)):
        raise ValueError("arguments must contain only finite JSON-compatible values")

    identity = id(value)
    if identity in ancestors:
        raise ValueError("arguments must contain only finite JSON-compatible values")
    ancestors.add(identity)
    try:
        if isinstance(value, dict):
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise ValueError("arguments must contain only finite JSON-compatible values")
                _validate_json_value(nested, ancestors)
        else:
            for nested in value:
                _validate_json_value(nested, ancestors)
    finally:
        ancestors.remove(identity)


def _snapshot_arguments(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("arguments must be a JSON-compatible dict or None")

    try:
        _validate_json_value(value, set())
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except RecursionError:
        raise ValueError("arguments must contain only finite JSON-compatible values") from None
    return json.loads(serialized)


def to_loop_event(event: ControlEvent) -> LoopEvent | None:
    if event.kind not in {EventKind.TOOL_CALL, EventKind.TOOL_RESULT}:
        return None

    payload = event.payload
    return LoopEvent(
        run_id=event.session.session_id,
        agent=str(payload.get("agent", event.source)),
        kind="tool_call" if event.kind == EventKind.TOOL_CALL else "tool_result",
        tool_name=payload.get("tool_name"),
        tool_args=_snapshot_arguments(payload.get("arguments")),
        error=payload.get("error"),
        tokens=_parse_tokens(payload.get("tokens", 0)),
        cost_usd=_parse_cost_usd(payload.get("cost_usd", 0.0)),
        metadata={
            "control_event_id": event.event_id,
            "repo_id": event.session.repo_id,
            "worktree_id": event.session.worktree_id,
        },
    )
