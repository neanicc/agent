from __future__ import annotations

import contextlib
import json
import os
import shlex
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .codex_hooks import (
    INTEGRATION_VERSION,
    _copy_json,
    _find_git_root,
    _json_bytes,
    _owned_file_needs_write,
    _read_settings,
    _settings_lock,
    _tree_checksum,
    _write_atomic_settings,
    _write_owned_file,
)


PLUGIN_NAME = "loopguard"
PLUGIN_VERSION = "0.1.0"
MARKETPLACE_NAME = "loopguard-local"
MIN_CLAUDE_PLUGIN_VERSION = (2, 1, 210)
FILE_CHANGED_MATCHER = ".env|.envrc|CLAUDE.md"
CLAUDE_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "FileChanged",
    "PreCompact",
    "Stop",
)
_TOOL_EVENTS = {"PreToolUse", "PermissionRequest", "PostToolUse", "PostToolUseFailure"}


class ClaudeInstallError(RuntimeError):
    """Claude integration state could not be changed without violating ownership."""


@dataclass(frozen=True, slots=True)
class ClaudeIntegrationResult:
    changed: bool
    mode: str
    status: str
    settings_path: Path | None = None
    wrapper_path: Path | None = None
    plugin_path: Path | None = None
    marketplace_path: Path | None = None
    checksum: str = ""
    cloud_status: str = "conditional"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}


@dataclass(frozen=True, slots=True)
class ClaudeVerification:
    installed: bool
    healthy: bool
    mode: str
    status: str
    checksum: str
    policy_source: Path | None = None
    cloud_status: str = "conditional"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}


@dataclass(frozen=True, slots=True)
class ClaudeMarketplace:
    marketplace_path: Path
    plugin_path: Path
    checksum: str
    changed: bool


def _manifest() -> dict[str, object]:
    return {
        "name": PLUGIN_NAME,
        "description": "Attach Claude Code lifecycle events to LoopGuard's local circuit breaker.",
        "version": PLUGIN_VERSION,
        "author": {"name": "LoopGuard contributors"},
        "homepage": "https://github.com/neanicc/agent/tree/main/loopguard",
        "repository": "https://github.com/neanicc/agent",
        "license": "MIT",
        "keywords": ["claude-code", "agent-safety", "loop-detection"],
    }


def _plugin_hooks() -> dict[str, object]:
    hooks: dict[str, object] = {}
    for event in CLAUDE_EVENTS:
        group: dict[str, object] = {
            "hooks": [
                {
                    "type": "command",
                    "command": "${CLAUDE_PLUGIN_ROOT}/bin/loopguard-hook",
                    "args": [event],
                    "timeout": 1,
                    "statusMessage": f"LoopGuard: {event}",
                }
            ]
        }
        if event in _TOOL_EVENTS:
            group["matcher"] = "*"
        elif event == "FileChanged":
            group["matcher"] = FILE_CHANGED_MATCHER
        hooks[event] = [group]
    return {"hooks": hooks}


def _marketplace() -> dict[str, object]:
    return {
        "name": MARKETPLACE_NAME,
        "owner": {"name": "LoopGuard contributors"},
        "description": "Local LoopGuard agent safety integrations.",
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": "./plugins/loopguard",
                "description": "Local lifecycle hooks for LoopGuard.",
                "version": PLUGIN_VERSION,
            }
        ],
    }


_PLUGIN_LAUNCHER = """#!/bin/sh
set -eu
event=${1:-}
case "$event" in
  SessionStart|UserPromptSubmit|PreToolUse|PermissionRequest|PostToolUse|PostToolUseFailure|FileChanged|PreCompact|Stop) ;;
  *) printf '%s\\n' 'LoopGuard: unsupported Claude hook event' >&2; exit 0 ;;
esac
exec loopguard hook-entry claude "$event" --integration-version 1
"""

_PROJECT_LAUNCHER = """#!/bin/sh
set -eu
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -d "$repo_root/.git" ] || [ -f "$repo_root/.git" ] || exit 0
exec {executable} hook-entry claude "$1" --integration-version 1
"""


def materialize_claude_marketplace(home: Path, *, dry_run: bool = False) -> ClaudeMarketplace:
    files = _plugin_file_bytes()
    checksum = _tree_checksum(files)
    root = home.expanduser() / "claude-marketplace"
    plugin = root / "plugins" / PLUGIN_NAME
    changed = False
    for relative, content in files.items():
        destination = plugin / relative
        needs_write = _owned_file_needs_write(
            destination,
            content,
            executable=relative == "bin/loopguard-hook",
        )
        changed = changed or needs_write
        if needs_write and not dry_run:
            _write_owned_file(destination, content, executable=relative == "bin/loopguard-hook")
    marketplace_path = root / ".claude-plugin" / "marketplace.json"
    marketplace_bytes = _json_bytes(_marketplace())
    needs_marketplace = _owned_file_needs_write(marketplace_path, marketplace_bytes)
    changed = changed or needs_marketplace
    if needs_marketplace and not dry_run:
        _write_owned_file(marketplace_path, marketplace_bytes)
    checksum_path = root / "loopguard.checksum"
    checksum_bytes = f"sha256:{checksum}\n".encode()
    needs_checksum = _owned_file_needs_write(checksum_path, checksum_bytes)
    changed = changed or needs_checksum
    if needs_checksum and not dry_run:
        _write_owned_file(checksum_path, checksum_bytes)
    return ClaudeMarketplace(root, plugin, checksum, changed)


def install_claude_plugin(
    home: Path,
    *,
    claude_executable: str = "claude",
    scope: str = "user",
    command_runner: Any = subprocess.run,
    dry_run: bool = False,
) -> ClaudeIntegrationResult:
    if scope not in {"user", "project", "local"}:
        raise ClaudeInstallError("Claude plugin scope must be user, project, or local")
    marketplace = materialize_claude_marketplace(home, dry_run=dry_run)
    if dry_run:
        return ClaudeIntegrationResult(
            changed=marketplace.changed,
            mode="plugin",
            status="preview",
            plugin_path=marketplace.plugin_path,
            marketplace_path=marketplace.marketplace_path,
            checksum=marketplace.checksum,
        )
    installed = _list_plugins(command_runner, claude_executable)
    if _matching_plugin(installed, marketplace.checksum):
        return ClaudeIntegrationResult(
            changed=marketplace.changed,
            mode="plugin",
            status="active",
            plugin_path=marketplace.plugin_path,
            marketplace_path=marketplace.marketplace_path,
            checksum=marketplace.checksum,
        )
    _run_claude(
        command_runner,
        [claude_executable, "plugin", "marketplace", "add", str(marketplace.marketplace_path), "--scope", scope],
    )
    existing = next(
        (item for item in installed if item.get("id") == f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"),
        None,
    )
    if existing is None:
        command = [
            claude_executable,
            "plugin",
            "install",
            f"{PLUGIN_NAME}@{MARKETPLACE_NAME}",
            "--scope",
            scope,
        ]
    else:
        command = [
            claude_executable,
            "plugin",
            "update",
            f"{PLUGIN_NAME}@{MARKETPLACE_NAME}",
        ]
    _run_claude(command_runner, command)
    return ClaudeIntegrationResult(
        changed=True,
        mode="plugin",
        status="active",
        plugin_path=marketplace.plugin_path,
        marketplace_path=marketplace.marketplace_path,
        checksum=marketplace.checksum,
    )


def uninstall_claude_plugin(
    *,
    claude_executable: str = "claude",
    command_runner: Any = subprocess.run,
) -> ClaudeIntegrationResult:
    checksum = _tree_checksum(_plugin_file_bytes())
    installed = _list_plugins(command_runner, claude_executable)
    if not _matching_plugin(installed, checksum):
        return ClaudeIntegrationResult(False, "plugin", "not_installed", checksum=checksum)
    _run_claude(
        command_runner,
        [claude_executable, "plugin", "uninstall", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"],
    )
    return ClaudeIntegrationResult(True, "plugin", "removed", checksum=checksum)


def verify_claude_plugin(
    *,
    claude_executable: str = "claude",
    command_runner: Any = subprocess.run,
    managed_settings_paths: Sequence[Path] | None = None,
) -> ClaudeVerification:
    checksum = _tree_checksum(_plugin_file_bytes())
    installed = _matching_plugin(_list_plugins(command_runner, claude_executable), checksum)
    policy_source = _managed_hook_policy_source(managed_settings_paths)
    healthy = installed and policy_source is None
    return ClaudeVerification(
        installed=installed,
        healthy=healthy,
        mode="plugin",
        status="active" if healthy else "unavailable" if policy_source else "not_installed",
        checksum=checksum,
        policy_source=policy_source,
    )


def verify_claude_hooks(
    path: Path,
    *,
    executable: str = "loopguard",
    scope: str = "user",
    repository: Path | None = None,
    managed_settings_paths: Sequence[Path] | None = None,
) -> ClaudeVerification:
    if scope not in {"user", "project"}:
        raise ClaudeInstallError("Claude fallback scope must be user or project")
    settings = path.expanduser()
    expected = _fallback_handlers(executable, scope=scope)
    try:
        body = _read_settings(settings)
    except Exception as exc:
        raise ClaudeInstallError(str(exc)) from exc
    hooks = body.get("hooks")
    exact_events: list[str] = []
    if isinstance(hooks, dict):
        for event, handler in expected.items():
            groups = hooks.get(event)
            if isinstance(groups, list) and any(
                isinstance(group, dict)
                and isinstance(group.get("hooks"), list)
                and handler in group["hooks"]
                for group in groups
            ):
                exact_events.append(event)
    installed = bool(exact_events)
    complete = len(exact_events) == len(CLAUDE_EVENTS)
    if scope == "project" and complete:
        repo_root = _claude_git_root(repository or settings.parent)
        wrapper_path = repo_root / ".loopguard" / "hooks" / "claude-hook"
        expected_wrapper = _PROJECT_LAUNCHER.format(executable=shlex.quote(executable)).encode()
        complete = wrapper_path.is_file() and wrapper_path.read_bytes() == expected_wrapper
        if complete and os.name == "posix":
            complete = os.access(wrapper_path, os.X_OK)
    policy_source = _managed_hook_policy_source(managed_settings_paths)
    healthy = complete and policy_source is None
    status = (
        "active"
        if healthy
        else "unavailable"
        if policy_source is not None
        else "incomplete"
        if installed
        else "not_installed"
    )
    return ClaudeVerification(
        installed=installed,
        healthy=healthy,
        mode="fallback",
        status=status,
        checksum=_definition_checksum(expected),
        policy_source=policy_source,
    )


def install_claude_hooks(
    path: Path,
    *,
    executable: str = "loopguard",
    scope: str = "user",
    plugin_available: bool = False,
    repository: Path | None = None,
    dry_run: bool = False,
) -> ClaudeIntegrationResult:
    if plugin_available:
        raise ClaudeInstallError("plugin and fallback Claude hooks are mutually exclusive")
    if scope not in {"user", "project"}:
        raise ClaudeInstallError("Claude fallback scope must be user or project")
    settings = path.expanduser()
    wrapper_path: Path | None = None
    wrapper: bytes | None = None
    wrapper_changed = False
    previous_mode: int | None = None
    if scope == "project":
        repo_root = _claude_git_root(repository or settings.parent)
        wrapper_path = repo_root / ".loopguard" / "hooks" / "claude-hook"
        wrapper = _PROJECT_LAUNCHER.format(executable=shlex.quote(executable)).encode()
        if wrapper_path.exists() and wrapper_path.read_bytes() != wrapper:
            raise ClaudeInstallError("refusing to overwrite a non-LoopGuard Claude hook wrapper")
        if wrapper_path.exists() and os.name == "posix":
            previous_mode = stat.S_IMODE(wrapper_path.stat().st_mode)
        wrapper_changed = _owned_file_needs_write(wrapper_path, wrapper, executable=True)
    expected = _fallback_handlers(executable, scope=scope)
    try:
        if dry_run:
            body = _read_settings(settings)
            updated = _copy_json(body)
            changed = _merge_handlers(updated, expected)
        else:
            with _settings_lock(settings):
                body = _read_settings(settings)
                updated = _copy_json(body)
                changed = _merge_handlers(updated, expected)
                wrapper_existed = wrapper_path is not None and wrapper_path.exists()
                try:
                    if wrapper_changed and wrapper_path is not None and wrapper is not None:
                        _write_owned_file(wrapper_path, wrapper, executable=True)
                    if changed:
                        _write_atomic_settings(settings, updated)
                except Exception:
                    if wrapper_changed and wrapper_path is not None:
                        if wrapper_existed and wrapper is not None:
                            _write_owned_file(wrapper_path, wrapper, executable=True)
                            if os.name == "posix" and previous_mode is not None:
                                os.chmod(wrapper_path, previous_mode)
                        else:
                            with contextlib.suppress(FileNotFoundError):
                                wrapper_path.unlink()
                    raise
    except Exception as exc:
        if isinstance(exc, ClaudeInstallError):
            raise
        raise ClaudeInstallError(str(exc)) from exc
    return ClaudeIntegrationResult(
        changed=changed or wrapper_changed,
        mode="fallback",
        status="preview" if dry_run else "active",
        settings_path=settings,
        wrapper_path=wrapper_path,
        checksum=_definition_checksum(expected),
    )


def uninstall_claude_hooks(
    path: Path,
    *,
    executable: str = "loopguard",
    scope: str = "user",
    repository: Path | None = None,
) -> ClaudeIntegrationResult:
    settings = path.expanduser()
    expected = _fallback_handlers(executable, scope=scope)
    try:
        with _settings_lock(settings):
            body = _read_settings(settings)
            updated = _copy_json(body)
            changed = _remove_handlers(updated, expected)
            if changed:
                _write_atomic_settings(settings, updated)
    except Exception as exc:
        raise ClaudeInstallError(str(exc)) from exc
    wrapper_path: Path | None = None
    if scope == "project":
        repo_root = _claude_git_root(repository or settings.parent)
        wrapper_path = repo_root / ".loopguard" / "hooks" / "claude-hook"
        expected_wrapper = _PROJECT_LAUNCHER.format(executable=shlex.quote(executable)).encode()
        if wrapper_path.exists() and wrapper_path.read_bytes() == expected_wrapper:
            wrapper_path.unlink()
            changed = True
    return ClaudeIntegrationResult(
        changed=changed,
        mode="fallback",
        status="removed" if changed else "not_installed",
        settings_path=settings,
        wrapper_path=wrapper_path,
        checksum=_definition_checksum(expected),
    )


def find_claude_fallback_events(path: Path) -> tuple[str, ...]:
    try:
        body = _read_settings(path.expanduser())
    except Exception as exc:
        raise ClaudeInstallError(str(exc)) from exc
    hooks = body.get("hooks")
    if not isinstance(hooks, dict):
        return ()
    found: list[str] = []
    for event in CLAUDE_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        if any(
            isinstance(group, dict)
            and isinstance(group.get("hooks"), list)
            and any(_is_owned_handler(handler, event) for handler in group["hooks"])
            for group in groups
        ):
            found.append(event)
    return tuple(found)


def claude_plugin_available(version: str) -> bool:
    raw = version.strip().split()[0]
    try:
        parsed = tuple(int(part) for part in raw.split(".")[:3])
    except ValueError:
        return False
    return len(parsed) == 3 and parsed >= MIN_CLAUDE_PLUGIN_VERSION


def _fallback_handlers(executable: str, *, scope: str) -> dict[str, dict[str, object]]:
    handlers: dict[str, dict[str, object]] = {}
    for event in CLAUDE_EVENTS:
        command = (
            "${CLAUDE_PROJECT_DIR}/.loopguard/hooks/claude-hook"
            if scope == "project"
            else executable
        )
        args = (
            [event]
            if scope == "project"
            else ["hook-entry", "claude", event, "--integration-version", str(INTEGRATION_VERSION)]
        )
        handlers[event] = {
            "type": "command",
            "command": command,
            "args": args,
            "timeout": 1,
            "statusMessage": f"LoopGuard: {event}",
        }
    return handlers


def _merge_handlers(body: dict[str, Any], expected: Mapping[str, dict[str, object]]) -> bool:
    hooks = body.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ClaudeInstallError("Claude settings field 'hooks' must be an object")
    changed = False
    for event, handler in expected.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ClaudeInstallError(f"Claude hook event {event!r} must be an array")
        if any(
            isinstance(group, dict)
            and isinstance(group.get("hooks"), list)
            and handler in group["hooks"]
            for group in groups
        ):
            continue
        group: dict[str, object] = {"hooks": [handler]}
        if event in _TOOL_EVENTS:
            group["matcher"] = "*"
        elif event == "FileChanged":
            group["matcher"] = FILE_CHANGED_MATCHER
        groups.append(group)
        changed = True
    return changed


def _remove_handlers(body: dict[str, Any], expected: Mapping[str, dict[str, object]]) -> bool:
    hooks = body.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event, expected_handler in expected.items():
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            original = group["hooks"]
            kept = [handler for handler in original if handler != expected_handler]
            changed = changed or len(kept) != len(original)
            if kept:
                copied = dict(group)
                copied["hooks"] = kept
                kept_groups.append(copied)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return changed


def _is_owned_handler(handler: object, event: str) -> bool:
    if not isinstance(handler, dict):
        return False
    if handler.get("timeout") != 1 or handler.get("statusMessage") != f"LoopGuard: {event}":
        return False
    args = handler.get("args")
    if handler.get("command") == "${CLAUDE_PROJECT_DIR}/.loopguard/hooks/claude-hook":
        return args == [event]
    return isinstance(args, list) and args == [
        "hook-entry",
        "claude",
        event,
        "--integration-version",
        str(INTEGRATION_VERSION),
    ]


def _plugin_file_bytes() -> dict[str, bytes]:
    return {
        ".claude-plugin/plugin.json": _json_bytes(_manifest()),
        "hooks/hooks.json": _json_bytes(_plugin_hooks()),
        "bin/loopguard-hook": _PLUGIN_LAUNCHER.encode(),
    }


def _definition_checksum(expected: Mapping[str, Mapping[str, object]]) -> str:
    import hashlib

    return hashlib.sha256(_json_bytes(expected)).hexdigest()


def _list_plugins(command_runner: Any, executable: str) -> list[dict[str, Any]]:
    try:
        result = command_runner(
            [executable, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaudeInstallError("Claude plugin list command failed") from exc
    if result.returncode != 0:
        raise ClaudeInstallError((result.stderr or result.stdout or "Claude plugin list failed").strip())
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ClaudeInstallError("Claude plugin list returned invalid JSON") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ClaudeInstallError("Claude plugin list returned an invalid shape")
    return payload


def _matching_plugin(plugins: Sequence[Mapping[str, Any]], checksum: str) -> bool:
    for plugin in plugins:
        if (
            plugin.get("id") != f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
            or plugin.get("version") != PLUGIN_VERSION
            or plugin.get("enabled") is not True
        ):
            continue
        raw_path = plugin.get("installPath")
        if not isinstance(raw_path, str):
            continue
        path = Path(raw_path)
        try:
            files = {relative: (path / relative).read_bytes() for relative in _plugin_file_bytes()}
        except OSError:
            continue
        if _tree_checksum(files) == checksum:
            return True
    return False


def _run_claude(command_runner: Any, command: Sequence[str]) -> None:
    try:
        result = command_runner(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaudeInstallError(f"Claude plugin command failed: {command[1:3]}") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown Claude error").strip()
        raise ClaudeInstallError(f"Claude plugin command failed: {message}")


def _managed_hook_policy_source(paths: Sequence[Path] | None) -> Path | None:
    candidates = tuple(paths) if paths is not None else _default_managed_settings_paths()
    for path in candidates:
        try:
            payload = json.loads(path.read_text())
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("allowManagedHooksOnly") is True:
            return path
    return None


def _default_managed_settings_paths() -> tuple[Path, ...]:
    if os.name == "nt":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        return (base / "ClaudeCode" / "managed-settings.json",)
    if sys_platform() == "darwin":
        return (Path("/Library/Application Support/ClaudeCode/managed-settings.json"),)
    return (Path("/etc/claude-code/managed-settings.json"),)


def sys_platform() -> str:
    import sys

    return sys.platform


def _claude_git_root(start: Path) -> Path:
    try:
        return _find_git_root(start)
    except Exception as exc:
        raise ClaudeInstallError("project fallback requires a canonical Git repository root") from exc
