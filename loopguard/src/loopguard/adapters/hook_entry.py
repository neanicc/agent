from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from loopguard.control.decisions import PolicyDecision, TargetKind
from loopguard.control.events import ControlEvent
from loopguard.control.redaction import redact

from .hook_client import HookClient, HookClientError
from .normalize_hook import (
    MAX_HOOK_INPUT_BYTES,
    HookNormalizationError,
    IdentityResolver,
    NormalizedHook,
    normalize_hook,
)


MAX_HOOK_OUTPUT_BYTES = 16_384


class HookSender(Protocol):
    def send(self, event: ControlEvent) -> PolicyDecision: ...


@dataclass(frozen=True, slots=True)
class HookResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    blocked: bool = False
    warning: str | None = None


def process_hook(
    vendor: str,
    hook_name: str,
    raw: dict[str, Any],
    *,
    client: HookSender,
    fail_closed: bool = False,
    identity_resolver: IdentityResolver | None = None,
    host_id: str | None = None,
) -> HookResult:
    try:
        normalized = normalize_hook(
            vendor,
            hook_name,
            raw,
            identity_resolver=identity_resolver,
            host_id=host_id,
        )
    except HookNormalizationError as exc:
        return _failure_result(vendor, hook_name, exc.code, fail_closed=fail_closed)
    except Exception:
        return _failure_result(
            vendor,
            hook_name,
            "normalization_failure",
            fail_closed=fail_closed,
        )
    try:
        raw_decision = client.send(normalized.event)
        decision = PolicyDecision.model_validate(raw_decision)
    except HookClientError as exc:
        return _failure_result(vendor, hook_name, exc.code, fail_closed=fail_closed)
    except (ValidationError, TypeError, ValueError):
        return _failure_result(vendor, hook_name, "invalid_decision", fail_closed=fail_closed)
    except Exception:
        return _failure_result(vendor, hook_name, "client_failure", fail_closed=fail_closed)
    if (
        decision.target.kind is not TargetKind.SESSION
        or decision.target.target_id != normalized.event.session.session_id
        or decision.state_version <= 0
        or not decision.state_hash
    ):
        return _failure_result(vendor, hook_name, "invalid_decision", fail_closed=fail_closed)
    if normalized.action_request is not None:
        return _render_permission_decision(normalized, decision)
    return _render_policy_decision(normalized, decision)


def process_hook_input(
    vendor: str,
    hook_name: str,
    raw_input: bytes,
    *,
    client: HookSender,
    fail_closed: bool = False,
    identity_resolver: IdentityResolver | None = None,
    host_id: str | None = None,
) -> HookResult:
    if len(raw_input) > MAX_HOOK_INPUT_BYTES:
        return _failure_result(vendor, hook_name, "input_too_large", fail_closed=fail_closed)
    try:
        decoded = raw_input.decode("utf-8")
    except UnicodeDecodeError:
        return _failure_result(vendor, hook_name, "invalid_utf8", fail_closed=fail_closed)
    try:
        raw = json.loads(
            decoded,
            parse_constant=lambda _value: _reject_json_constant(),
        )
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _failure_result(vendor, hook_name, "invalid_json", fail_closed=fail_closed)
    if not isinstance(raw, dict):
        return _failure_result(vendor, hook_name, "invalid_input", fail_closed=fail_closed)
    return process_hook(
        vendor,
        hook_name,
        raw,
        client=client,
        fail_closed=fail_closed,
        identity_resolver=identity_resolver,
        host_id=host_id,
    )


def _render_policy_decision(normalized: NormalizedHook, decision: PolicyDecision) -> HookResult:
    action = decision.action
    if action == "allow":
        return HookResult(exit_code=0)
    if action == "warn":
        message = _safe_message(decision.reason or "LoopGuard reported a warning.")
        return _json_result({"systemMessage": message})
    if action == "pause":
        if _can_block(normalized.hook_name):
            return _blocking_result(
                normalized.vendor,
                normalized.hook_name,
                _safe_message(decision.reason or "LoopGuard blocked this operation."),
            )
        return _visible_capability_warning(normalized, action)
    if action == "interrupt":
        return _visible_capability_warning(normalized, action)
    if action == "inject":
        if normalized.hook_name in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
            return _json_result(
                {
                    "hookSpecificOutput": {
                        "hookEventName": normalized.hook_name,
                        "additionalContext": _safe_message(decision.reason),
                    }
                }
            )
        return _visible_capability_warning(normalized, action)
    if action == "request_approval":
        return _visible_capability_warning(normalized, action)
    return _failure_result(
        normalized.vendor,
        normalized.hook_name,
        "invalid_decision",
        fail_closed=False,
    )


def _render_permission_decision(
    normalized: NormalizedHook,
    decision: PolicyDecision,
) -> HookResult:
    approval = decision.metadata.get("approval")
    request = normalized.action_request
    if not isinstance(approval, dict) or request is None:
        return HookResult(exit_code=0)
    if (
        approval.get("action_id") != request.action_id
        or approval.get("nonce") != request.nonce
        or approval.get("expected_state_version") != request.expected_state_version
        or approval.get("expected_state_hash") != request.expected_state_hash
        or approval.get("behavior") not in {"allow", "deny"}
    ):
        return HookResult(exit_code=0, warning="stale_approval_decision")
    behavior = str(approval["behavior"])
    if behavior == "allow":
        body = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
        return _json_result(body)
    return _blocking_result(
        normalized.vendor,
        "PermissionRequest",
        _safe_message(decision.reason or "LoopGuard denied this permission request."),
    )


def _visible_capability_warning(normalized: NormalizedHook, action: str) -> HookResult:
    message = _safe_message(
        f"LoopGuard requested {action}, but {normalized.vendor} {normalized.hook_name} "
        "does not expose that exact control. Normal vendor behavior continues."
    )
    result = _json_result({"systemMessage": message})
    return HookResult(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        blocked=False,
        warning=f"capability_unavailable:{action}",
    )


def _failure_result(
    vendor: str,
    hook_name: str,
    code: str,
    *,
    fail_closed: bool,
) -> HookResult:
    if fail_closed and _can_block(hook_name):
        return _blocking_result(
            vendor,
            hook_name,
            f"LoopGuard fail-closed policy blocked this operation ({code}).",
            warning=code,
        )
    if code == "invalid_decision":
        result = _json_result(
            {"systemMessage": "LoopGuard ignored an invalid daemon decision and failed open."}
        )
        return HookResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            blocked=False,
            warning=code,
        )
    return HookResult(
        exit_code=0,
        stderr=f"LoopGuard hook warning: {code}\n",
        warning=code,
    )


def _blocking_result(
    vendor: str,
    hook_name: str,
    reason: str,
    *,
    warning: str | None = None,
) -> HookResult:
    safe_reason = _safe_message(reason)
    if hook_name == "PermissionRequest":
        body = {
            "hookSpecificOutput": {
                "hookEventName": hook_name,
                "decision": {"behavior": "deny", "message": safe_reason},
            }
        }
    elif hook_name == "UserPromptSubmit":
        body = {"decision": "block", "reason": safe_reason}
    else:
        body = {
            "hookSpecificOutput": {
                "hookEventName": hook_name,
                "permissionDecision": "deny",
                "permissionDecisionReason": safe_reason,
            }
        }
    result = _json_result(body)
    return HookResult(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        blocked=True,
        warning=warning,
    )


def _json_result(body: dict[str, Any]) -> HookResult:
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded.encode("utf-8")) > MAX_HOOK_OUTPUT_BYTES:
        encoded = json.dumps(
            {"systemMessage": "LoopGuard output exceeded the safe hook response limit."},
            separators=(",", ":"),
        )
    return HookResult(exit_code=0, stdout=encoded)


def _safe_message(value: object) -> str:
    message = redact(str(value))
    if not isinstance(message, str):
        return "LoopGuard returned a redacted decision."
    encoded = message.encode("utf-8")
    if len(encoded) <= 4_096:
        return message
    return encoded[:4_096].decode("utf-8", errors="ignore") + "…"


def _can_block(hook_name: object) -> bool:
    return isinstance(hook_name, str) and hook_name in {
        "PreToolUse",
        "PermissionRequest",
        "UserPromptSubmit",
    }


def _reject_json_constant() -> None:
    raise ValueError("non-finite JSON number")


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward a native agent hook to LoopGuard.")
    parser.add_argument("vendor", choices=("codex", "claude"))
    parser.add_argument("hook_name")
    parser.add_argument("--fail-closed", action="store_true")
    arguments = parser.parse_args()
    raw_input = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    result = process_hook_input(
        arguments.vendor,
        arguments.hook_name,
        raw_input,
        client=HookClient(),
        fail_closed=arguments.fail_closed,
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
