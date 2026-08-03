from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from loopguard.control.decisions import ActionKind, ActionRequest, ActionTarget, TargetKind
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.redaction import redact


MAX_HOOK_INPUT_BYTES = 262_144
MAX_EVENT_PAYLOAD_BYTES = 131_072
MAX_TEXT_BYTES = 32_768

CODEX_PRE_TOOL_COVERAGE = {
    "tools": ["Bash(simple)", "apply_patch", "MCP"],
    "complete_enforcement": False,
    "limitations": ["unified_exec", "WebSearch", "non-shell non-MCP tools"],
}

_SUPPORTED_HOOKS = {
    "codex": frozenset(
        {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PermissionRequest",
            "PostToolUse",
            "PreCompact",
            "Stop",
        }
    ),
    "claude": frozenset(
        {
            "SessionStart",
            "SessionEnd",
            "UserPromptSubmit",
            "PreToolUse",
            "PermissionRequest",
            "PostToolUse",
            "PostToolUseFailure",
            "FileChanged",
            "PreCompact",
            "Stop",
        }
    ),
}

_EVENT_KINDS = {
    "SessionStart": EventKind.SESSION_STARTED,
    "SessionEnd": EventKind.SESSION_STOPPED,
    "UserPromptSubmit": EventKind.PROMPT_SUBMITTED,
    "PreToolUse": EventKind.TOOL_CALL,
    "PostToolUse": EventKind.TOOL_RESULT,
    "PostToolUseFailure": EventKind.TOOL_RESULT,
    "FileChanged": EventKind.FILE_CHANGED,
    "PreCompact": EventKind.CONTEXT_COMPACTED,
    "Stop": EventKind.TURN_COMPLETED,
}


class HookNormalizationError(Exception):
    """Untrusted vendor hook input could not be normalized safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    root: Path
    repo_id: str
    worktree_id: str


@dataclass(frozen=True, slots=True)
class NormalizedHook:
    vendor: str
    hook_name: str
    event: ControlEvent
    action_request: ActionRequest | None = None


IdentityResolver = Callable[[str | Path], RepositoryIdentity]
Clock = Callable[[], datetime]


def normalize_hook(
    vendor: str,
    hook_name: str,
    raw: dict[str, Any],
    *,
    identity_resolver: IdentityResolver | None = None,
    host_id: str | None = None,
    now: Clock | None = None,
) -> NormalizedHook:
    normalized_vendor = vendor.strip().lower() if isinstance(vendor, str) else ""
    if normalized_vendor not in _SUPPORTED_HOOKS:
        raise HookNormalizationError("unsupported_vendor")
    if not isinstance(hook_name, str):
        raise HookNormalizationError("unsupported_hook")
    if hook_name not in _SUPPORTED_HOOKS[normalized_vendor]:
        raise HookNormalizationError("unsupported_hook")

    snapshot = _snapshot_untrusted_input(raw)
    declared_hook = snapshot.get("hook_event_name")
    if declared_hook is not None and declared_hook != hook_name:
        raise HookNormalizationError("hook_name_mismatch")
    cwd = _bounded_string(snapshot.get("cwd"), "invalid_cwd", maximum=4_096)
    resolver = identity_resolver or resolve_repository_identity
    identity = resolver(cwd)
    vendor_session_id = _bounded_string(
        snapshot.get("session_id"),
        "invalid_session_id",
        maximum=512,
    )
    local_host = host_id or local_host_id()
    session_id = _stable_id(
        "session",
        local_host,
        identity.repo_id,
        normalized_vendor,
        vendor_session_id,
    )
    turn_value = snapshot.get("turn_id")
    turn_id = None
    if turn_value is not None:
        turn_id = _stable_id(
            "turn",
            session_id,
            _bounded_string(turn_value, "invalid_turn_id", maximum=512),
        )
    session = SessionRef(
        host_id=local_host,
        repo_id=identity.repo_id,
        session_id=session_id,
        worktree_id=identity.worktree_id,
        turn_id=turn_id,
    )
    timestamp = (now or _utc_now)()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise HookNormalizationError("invalid_clock")
    timestamp = timestamp.astimezone(timezone.utc).replace(microsecond=0)
    execution_environment = (
        "cloud"
        if normalized_vendor == "claude"
        and os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true"
        else "local"
    )

    seed: dict[str, Any] = {
        "vendor": normalized_vendor,
        "hook_name": hook_name,
        "session_id": session_id,
        "raw": snapshot,
        "execution_environment": execution_environment,
    }
    event_seed = _canonical_json(seed, error_code="invalid_input")
    event_id = _stable_id("hook", event_seed)

    if hook_name == "PermissionRequest":
        action_request = _permission_request(
            event_id=event_id,
            session=session,
            vendor=normalized_vendor,
            raw=snapshot,
            timestamp=timestamp,
        )
        payload = _bounded_payload(
            {
                "vendor": normalized_vendor,
                "hook_name": hook_name,
                "execution_environment": execution_environment,
                "action_request": action_request.model_dump(mode="json"),
            }
        )
        return NormalizedHook(
            vendor=normalized_vendor,
            hook_name=hook_name,
            event=ControlEvent(
                event_id=event_id,
                kind=EventKind.ACTION_REQUESTED,
                source=f"{normalized_vendor}-hooks",
                session=session,
                payload=payload,
                created_at=timestamp,
            ),
            action_request=action_request,
        )

    if hook_name == "FileChanged":
        snapshot = dict(snapshot)
        snapshot["file_path"] = _repository_relative_file_path(
            snapshot.get("file_path"),
            identity.root,
        )
    payload = _payload_for(
        normalized_vendor,
        hook_name,
        snapshot,
        execution_environment=execution_environment,
    )
    return NormalizedHook(
        vendor=normalized_vendor,
        hook_name=hook_name,
        event=ControlEvent(
            event_id=event_id,
            kind=_EVENT_KINDS[hook_name],
            source=f"{normalized_vendor}-hooks",
            session=session,
            payload=payload,
            created_at=timestamp,
        ),
    )


def _resolve_repository_identity_cached(cwd: str) -> RepositoryIdentity:
    path = Path(cwd).expanduser()
    if not path.is_absolute():
        raise HookNormalizationError("invalid_cwd")
    try:
        status = os.lstat(path)
    except (FileNotFoundError, OSError) as exc:
        raise HookNormalizationError("repository_unavailable") from exc
    if stat.S_ISLNK(status.st_mode) or _path_contains_symlink(path):
        raise HookNormalizationError("symlinked_cwd")
    if not stat.S_ISDIR(status.st_mode):
        raise HookNormalizationError("invalid_cwd")
    try:
        resolved = path.resolve(strict=True)
        process = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=0.1,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HookNormalizationError("repository_unavailable") from exc
    lines = process.stdout.splitlines()
    if len(lines) != 2:
        raise HookNormalizationError("repository_unavailable")
    try:
        root = Path(lines[0]).resolve(strict=True)
        common_dir = Path(lines[1])
        if not common_dir.is_absolute():
            common_dir = (resolved / common_dir).resolve(strict=True)
        else:
            common_dir = common_dir.resolve(strict=True)
    except OSError as exc:
        raise HookNormalizationError("repository_unavailable") from exc
    if not root.is_dir() or not common_dir.is_dir():
        raise HookNormalizationError("repository_unavailable")
    return RepositoryIdentity(
        root=root,
        repo_id=_stable_id("repo", str(common_dir)),
        worktree_id=_stable_id("worktree", str(root)),
    )


def resolve_repository_identity(cwd: str | Path) -> RepositoryIdentity:
    return _resolve_repository_identity_cached(os.fspath(cwd))


@lru_cache(maxsize=1)
def local_host_id() -> str:
    user_id = str(os.getuid()) if hasattr(os, "getuid") else os.environ.get("USERNAME", "unknown")
    return _stable_id("host", socket.gethostname(), user_id)


def _permission_request(
    *,
    event_id: str,
    session: SessionRef,
    vendor: str,
    raw: dict[str, Any],
    timestamp: datetime,
) -> ActionRequest:
    tool_name = _bounded_string(raw.get("tool_name"), "invalid_tool_name", maximum=512)
    tool_input = raw.get("tool_input", {})
    if not isinstance(tool_input, dict):
        raise HookNormalizationError("invalid_tool_input")
    parameters = _bounded_payload(
        {
            "vendor": vendor,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )
    pending_hash = hashlib.sha256(f"pending\n{event_id}".encode()).hexdigest()
    return ActionRequest(
        action_id=_stable_id("action", event_id),
        target=ActionTarget(kind=TargetKind.SESSION, target_id=session.session_id),
        kind=ActionKind.APPROVE,
        parameters=parameters,
        expected_state_version=0,
        expected_state_hash=pending_hash,
        nonce=_stable_id("nonce", event_id),
        expires_at=timestamp + timedelta(seconds=30),
    )


def _payload_for(
    vendor: str,
    hook_name: str,
    raw: dict[str, Any],
    *,
    execution_environment: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent": f"{vendor}-code",
        "hook_name": hook_name,
        "execution_environment": execution_environment,
    }
    if hook_name == "SessionStart":
        payload["start_source"] = _optional_string(raw.get("source"), maximum=128)
    elif hook_name == "SessionEnd":
        payload["reason"] = _optional_string(raw.get("reason"), maximum=256)
    elif hook_name == "UserPromptSubmit":
        payload["input_text"] = _bounded_string(
            raw.get("prompt"),
            "invalid_prompt",
            maximum=MAX_TEXT_BYTES,
        )
    elif hook_name in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
        payload.update(
            {
                "tool_name": _bounded_string(
                    raw.get("tool_name"),
                    "invalid_tool_name",
                    maximum=512,
                ),
                "arguments": raw.get("tool_input", {}),
                "tool_use_id": _optional_string(raw.get("tool_use_id"), maximum=512),
                "tokens": 0,
                "cost_usd": 0,
            }
        )
        if not isinstance(payload["arguments"], dict):
            raise HookNormalizationError("invalid_tool_input")
        if hook_name == "PostToolUse":
            payload["output"] = raw.get("tool_response")
        elif hook_name == "PostToolUseFailure":
            payload["error"] = _bounded_string(
                raw.get("error"),
                "invalid_tool_error",
                maximum=MAX_TEXT_BYTES,
            )
        if vendor == "codex" and hook_name == "PreToolUse":
            payload["coverage"] = CODEX_PRE_TOOL_COVERAGE
    elif hook_name == "FileChanged":
        payload.update(
            {
                "path": _bounded_string(
                    raw.get("file_path"),
                    "invalid_file_path",
                    maximum=4_096,
                ),
                "change": _bounded_string(
                    raw.get("event"),
                    "invalid_file_event",
                    maximum=32,
                ),
            }
        )
    elif hook_name == "PreCompact":
        payload["trigger"] = _optional_string(raw.get("trigger"), maximum=128)
    elif hook_name == "Stop":
        payload["stop_hook_active"] = bool(raw.get("stop_hook_active", False))
    return _bounded_payload(payload)


def _snapshot_untrusted_input(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HookNormalizationError("invalid_input")
    try:
        redacted = redact(raw)
    except (TypeError, ValueError, RecursionError) as exc:
        raise HookNormalizationError("invalid_input") from exc
    encoded = _canonical_json(redacted, error_code="invalid_input")
    if len(encoded.encode("utf-8")) > MAX_HOOK_INPUT_BYTES:
        raise HookNormalizationError("input_too_large")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise HookNormalizationError("invalid_input")
    return decoded


def _bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = _canonical_json(redact(payload), error_code="invalid_payload")
    if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
        raise HookNormalizationError("payload_too_large")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise HookNormalizationError("invalid_payload")
    return decoded


def _canonical_json(value: Any, *, error_code: str) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise HookNormalizationError(error_code) from exc


def _bounded_string(value: Any, error_code: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise HookNormalizationError(error_code)
    return value


def _optional_string(value: Any, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, "invalid_string", maximum=maximum)


def _stable_id(domain: str, *parts: str) -> str:
    body = "\x00".join((f"loopguard-{domain}-v1", *parts)).encode()
    return f"{domain}:{hashlib.sha256(body).hexdigest()}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _path_contains_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return True
        except OSError:
            return False
    return False


def _repository_relative_file_path(value: object, repository: Path) -> str:
    raw = _bounded_string(value, "invalid_file_path", maximum=4_096)
    candidate = Path(raw)
    lexical = Path(os.path.abspath(candidate if candidate.is_absolute() else repository / candidate))
    try:
        relative = lexical.relative_to(repository)
    except ValueError as exc:
        raise HookNormalizationError("file_path_outside_repository") from exc
    if not relative.parts:
        raise HookNormalizationError("invalid_file_path")
    return relative.as_posix()
