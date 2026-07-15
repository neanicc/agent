from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


INTEGRATION_VERSION = 1
PLUGIN_VERSION = "0.1.0"
PLUGIN_NAME = "codex-plugin"
MARKETPLACE_NAME = "loopguard-local"
MIN_CODEX_PLUGIN_VERSION = (0, 144, 0)
CODEX_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "Stop",
)
_TOOL_EVENTS = {"PreToolUse", "PermissionRequest", "PostToolUse"}


class CodexInstallError(RuntimeError):
    """A Codex integration could not be changed without violating its safety contract."""


@dataclass(frozen=True, slots=True)
class CodexIntegrationResult:
    changed: bool
    mode: str
    status: str
    trust_required: bool
    hook_checksum: str
    settings_path: Path | None = None
    wrapper_path: Path | None = None
    plugin_path: Path | None = None
    marketplace_path: Path | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}


@dataclass(frozen=True, slots=True)
class CodexVerification:
    installed: bool
    healthy: bool
    mode: str
    status: str
    trust_required: bool
    hook_checksum: str
    missing_events: tuple[str, ...] = ()
    hook_hashes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CodexMarketplace:
    marketplace_path: Path
    plugin_path: Path
    checksum: str
    changed: bool


def _manifest() -> dict[str, object]:
    return {
        "name": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "description": "Attach Codex lifecycle events to the local LoopGuard circuit breaker.",
        "author": {
            "name": "LoopGuard contributors",
            "url": "https://github.com/neanicc/agent",
        },
        "homepage": "https://github.com/neanicc/agent/tree/main/loopguard",
        "repository": "https://github.com/neanicc/agent",
        "license": "MIT",
        "keywords": ["codex", "agent-safety", "loop-detection"],
        "interface": {
            "displayName": "LoopGuard",
            "shortDescription": "Local circuit breaker for runaway agent loops.",
            "longDescription": (
                "Forwards a documented subset of Codex lifecycle events to the owner-only "
                "LoopGuard daemon for local observation and policy decisions."
            ),
            "developerName": "LoopGuard contributors",
            "category": "Developer Tools",
            "capabilities": ["Observe", "Block supported tool calls", "Request approval"],
            "websiteURL": "https://github.com/neanicc/agent/tree/main/loopguard",
            "defaultPrompt": ["Show the current LoopGuard protection status."],
            "brandColor": "#0D9488",
        },
    }


def _plugin_hooks() -> dict[str, object]:
    hooks: dict[str, object] = {}
    for event in CODEX_EVENTS:
        group: dict[str, object] = {
            "hooks": [
                {
                    "type": "command",
                    "command": f"${{PLUGIN_ROOT}}/bin/loopguard-hook {event}",
                    "commandWindows": (
                        f"loopguard hook-entry codex {event} "
                        f"--integration-version {INTEGRATION_VERSION}"
                    ),
                    "timeout": 1,
                    "statusMessage": f"LoopGuard: {event}",
                }
            ]
        }
        if event in _TOOL_EVENTS:
            group["matcher"] = "*"
        hooks[event] = [group]
    return {"hooks": hooks}


def _marketplace() -> dict[str, object]:
    return {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": "LoopGuard local integrations"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
                "category": "Developer Tools",
            }
        ],
    }


_PLUGIN_LAUNCHER = """#!/bin/sh
set -eu
event=${1:-}
case "$event" in
  SessionStart|UserPromptSubmit|PreToolUse|PermissionRequest|PostToolUse|PreCompact|Stop) ;;
  *) printf '%s\\n' 'LoopGuard: unsupported Codex hook event' >&2; exit 0 ;;
esac
exec loopguard hook-entry codex "$event" --integration-version 1
"""

_PROJECT_LAUNCHER = """#!/bin/sh
set -eu
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -d "$repo_root/.git" ] || [ -f "$repo_root/.git" ] || exit 0
exec {executable} hook-entry codex "$1" --integration-version 1
"""


def materialize_codex_marketplace(home: Path, *, dry_run: bool = False) -> CodexMarketplace:
    """Write a checksum-pinned local marketplace without changing Codex configuration."""
    files = _plugin_file_bytes()
    checksum = _tree_checksum(files)
    root = home.expanduser() / "codex-marketplace"
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
    marketplace_bytes = _json_bytes(_marketplace())
    manifest_path = root / ".agents" / "plugins" / "marketplace.json"
    needs_marketplace_write = _owned_file_needs_write(manifest_path, marketplace_bytes)
    changed = changed or needs_marketplace_write
    if needs_marketplace_write and not dry_run:
        _write_owned_file(manifest_path, marketplace_bytes)
    checksum_path = root / "loopguard.checksum"
    checksum_bytes = f"sha256:{checksum}\n".encode()
    needs_checksum_write = _owned_file_needs_write(checksum_path, checksum_bytes)
    changed = changed or needs_checksum_write
    if needs_checksum_write and not dry_run:
        _write_owned_file(checksum_path, checksum_bytes)
    return CodexMarketplace(root, plugin, checksum, changed)


def install_codex_plugin(
    home: Path,
    *,
    codex_executable: str = "codex",
    command_runner: Any = subprocess.run,
    dry_run: bool = False,
) -> CodexIntegrationResult:
    marketplace = materialize_codex_marketplace(home, dry_run=dry_run)
    hook_checksum = _tree_checksum(_plugin_file_bytes())
    if dry_run:
        return CodexIntegrationResult(
            changed=marketplace.changed,
            mode="plugin",
            status="preview",
            trust_required=True,
            hook_checksum=hook_checksum,
            plugin_path=marketplace.plugin_path,
            marketplace_path=marketplace.marketplace_path,
        )
    installed = _list_codex_plugins(command_runner, codex_executable)
    if _matching_plugin(installed, expected_checksum=hook_checksum):
        return CodexIntegrationResult(
            changed=marketplace.changed,
            mode="plugin",
            status="trust_required",
            trust_required=True,
            hook_checksum=hook_checksum,
            plugin_path=marketplace.plugin_path,
            marketplace_path=marketplace.marketplace_path,
        )
    _run_codex(
        command_runner,
        [codex_executable, "plugin", "marketplace", "add", str(marketplace.marketplace_path), "--json"],
    )
    _run_codex(
        command_runner,
        [codex_executable, "plugin", "add", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}", "--json"],
    )
    return CodexIntegrationResult(
        changed=True,
        mode="plugin",
        status="trust_required",
        trust_required=True,
        hook_checksum=hook_checksum,
        plugin_path=marketplace.plugin_path,
        marketplace_path=marketplace.marketplace_path,
    )


def verify_codex_plugin(
    *,
    codex_executable: str = "codex",
    command_runner: Any = subprocess.run,
    cwd: Path | None = None,
    hook_report: Sequence[Mapping[str, Any]] | None = None,
) -> CodexVerification:
    checksum = _tree_checksum(_plugin_file_bytes())
    plugins = _list_codex_plugins(command_runner, codex_executable)
    installed = _matching_plugin(plugins, expected_checksum=checksum)
    report = (
        list(hook_report)
        if hook_report is not None
        else _list_codex_hooks(codex_executable, cwd or Path.cwd())
    )
    expected = _plugin_expected_commands()
    matching, missing = _matching_hook_report(
        report,
        expected,
        plugin_id=f"{PLUGIN_NAME}@{MARKETPLACE_NAME}",
    )
    trusted = installed and not missing and all(
        hook.get("enabled") is True and hook.get("trustStatus") == "trusted"
        for hook in matching
    )
    status = (
        "active"
        if trusted
        else "not_installed"
        if not installed
        else "hooks_not_discovered"
        if missing
        else "trust_required"
    )
    return CodexVerification(
        installed=installed,
        healthy=trusted,
        mode="plugin",
        status=status,
        trust_required=installed and not missing and not trusted,
        hook_checksum=checksum,
        missing_events=missing,
        hook_hashes=tuple(
            str(hook["currentHash"])
            for hook in matching
            if isinstance(hook.get("currentHash"), str)
        ),
    )
def uninstall_codex_plugin(
    *,
    codex_executable: str = "codex",
    command_runner: Any = subprocess.run,
) -> CodexIntegrationResult:
    checksum = _tree_checksum(_plugin_file_bytes())
    installed = _list_codex_plugins(command_runner, codex_executable)
    if not _matching_plugin(installed, expected_checksum=checksum):
        return CodexIntegrationResult(
            changed=False,
            mode="plugin",
            status="not_installed",
            trust_required=False,
            hook_checksum=checksum,
        )
    _run_codex(
        command_runner,
        [codex_executable, "plugin", "remove", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}", "--json"],
    )
    return CodexIntegrationResult(
        changed=True,
        mode="plugin",
        status="removed",
        trust_required=False,
        hook_checksum=checksum,
    )


def install_codex_hooks(
    path: Path,
    *,
    executable: str = "loopguard",
    scope: str = "user",
    plugin_available: bool = False,
    repository: Path | None = None,
    dry_run: bool = False,
) -> CodexIntegrationResult:
    """Install the direct hooks fallback, preserving all non-LoopGuard content."""
    if plugin_available:
        raise CodexInstallError("plugin and fallback Codex hooks are mutually exclusive")
    if scope not in {"user", "project"}:
        raise CodexInstallError("scope must be user or project")
    settings = Path(path).expanduser()
    wrapper_path: Path | None = None
    wrapper_changed = False
    wrapper: bytes | None = None
    wrapper_previous_mode: int | None = None
    if scope == "project":
        repo_root = _find_git_root(repository or settings.parent)
        wrapper_path = repo_root / ".loopguard" / "hooks" / "codex-hook"
        wrapper = _PROJECT_LAUNCHER.format(executable=shlex.quote(executable)).encode()
        if wrapper_path.exists() and wrapper_path.read_bytes() != wrapper:
            raise CodexInstallError(
                "refusing to overwrite a non-LoopGuard project hook wrapper"
            )
        if wrapper_path.exists() and os.name == "posix":
            wrapper_previous_mode = stat.S_IMODE(wrapper_path.stat().st_mode)
        wrapper_changed = _owned_file_needs_write(wrapper_path, wrapper, executable=True)
    expected = _fallback_handlers(executable, scope=scope)
    if dry_run:
        body = _read_settings(settings)
        merged = _copy_json(body)
        changed = _merge_handlers(merged, expected)
    else:
        with _settings_lock(settings):
            body = _read_settings(settings)
            merged = _copy_json(body)
            changed = _merge_handlers(merged, expected)
            wrapper_existed = wrapper_path is not None and wrapper_path.exists()
            try:
                if wrapper_changed and wrapper_path is not None and wrapper is not None:
                    _write_owned_file(wrapper_path, wrapper, executable=True)
                if changed:
                    _write_atomic_settings(settings, merged)
            except Exception:
                if wrapper_changed and wrapper_path is not None:
                    if wrapper_existed and wrapper is not None:
                        _write_owned_file(wrapper_path, wrapper, executable=True)
                        if os.name == "posix" and wrapper_previous_mode is not None:
                            os.chmod(wrapper_path, wrapper_previous_mode)
                    else:
                        with contextlib.suppress(FileNotFoundError):
                            wrapper_path.unlink()
                raise
    checksum = _definition_checksum(expected)
    return CodexIntegrationResult(
        changed=changed or wrapper_changed,
        mode="fallback",
        status="preview" if dry_run else "trust_required",
        trust_required=True,
        hook_checksum=checksum,
        settings_path=settings,
        wrapper_path=wrapper_path,
    )


def uninstall_codex_hooks(
    path: Path,
    *,
    executable: str = "loopguard",
    scope: str = "user",
    repository: Path | None = None,
) -> CodexIntegrationResult:
    settings = Path(path).expanduser()
    expected = _fallback_handlers(executable, scope=scope)
    with _settings_lock(settings):
        body = _read_settings(settings)
        updated = _copy_json(body)
        changed = _remove_handlers(updated, expected)
        if changed:
            _write_atomic_settings(settings, updated)
    wrapper_path: Path | None = None
    if scope == "project":
        repo_root = _find_git_root(repository or settings.parent)
        wrapper_path = repo_root / ".loopguard" / "hooks" / "codex-hook"
        if wrapper_path.exists() and wrapper_path.read_bytes() == _PROJECT_LAUNCHER.format(
            executable=shlex.quote(executable)
        ).encode():
            wrapper_path.unlink()
            changed = True
    return CodexIntegrationResult(
        changed=changed,
        mode="fallback",
        status="removed" if changed else "not_installed",
        trust_required=False,
        hook_checksum=_definition_checksum(expected),
        settings_path=settings,
        wrapper_path=wrapper_path,
    )


def verify_codex_hooks(
    path: Path,
    *,
    executable: str = "loopguard",
    scope: str = "user",
    hook_report: Sequence[Mapping[str, Any]] | None = None,
) -> CodexVerification:
    settings = Path(path).expanduser()
    body = _read_settings(settings)
    expected = _fallback_handlers(executable, scope=scope)
    missing_file = tuple(
        event for event, handler in expected.items() if not _contains_handler(body, event, handler)
    )
    installed = not missing_file
    checksum = _definition_checksum(expected)
    matching: list[Mapping[str, Any]] = []
    missing_runtime: tuple[str, ...] = ()
    if hook_report is not None:
        matching, missing_runtime = _matching_hook_report(
            hook_report,
            {event: str(handler["command"]) for event, handler in expected.items()},
            source_path=settings,
        )
    missing = missing_file or missing_runtime
    trusted = installed and hook_report is not None and not missing and all(
        hook.get("enabled") is True and hook.get("trustStatus") == "trusted"
        for hook in matching
    )
    status = (
        "active"
        if trusted
        else "not_installed"
        if not installed
        else "hooks_not_discovered"
        if hook_report is not None and missing
        else "trust_required"
    )
    return CodexVerification(
        installed=installed,
        healthy=trusted,
        mode="fallback",
        status=status,
        trust_required=installed and not missing and not trusted,
        hook_checksum=checksum,
        missing_events=missing,
        hook_hashes=tuple(
            str(hook["currentHash"])
            for hook in matching
            if isinstance(hook.get("currentHash"), str)
        ),
    )


def codex_plugin_available(version: str) -> bool:
    parts = version.strip().removeprefix("codex-cli ").split(".")
    try:
        parsed = tuple(int(part) for part in parts[:3])
    except ValueError:
        return False
    return len(parsed) == 3 and parsed >= MIN_CODEX_PLUGIN_VERSION


def find_codex_fallback_events(path: Path) -> tuple[str, ...]:
    """Return exact versioned LoopGuard fallback handlers in one Codex hook source."""
    body = _read_settings(path.expanduser())
    hooks = body.get("hooks")
    if not isinstance(hooks, dict):
        return ()
    found: list[str] = []
    for event in CODEX_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        for group in groups:
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list):
                continue
            if any(_is_owned_fallback_handler(handler, event) for handler in handlers):
                found.append(event)
                break
    return tuple(found)


def codex_project_hooks_path(start: Path) -> Path | None:
    try:
        return _find_git_root(start) / ".codex" / "hooks.json"
    except CodexInstallError:
        return None


def _is_owned_fallback_handler(handler: object, event: str) -> bool:
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    if handler.get("timeout") != 1 or handler.get("statusMessage") != f"LoopGuard: {event}":
        return False
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    project_command = _fallback_handlers("loopguard", scope="project")[event]["command"]
    if command == project_command:
        return True
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return len(parts) == 6 and parts[1:] == [
        "hook-entry",
        "codex",
        event,
        "--integration-version",
        str(INTEGRATION_VERSION),
    ]


def _fallback_handlers(executable: str, *, scope: str) -> dict[str, dict[str, object]]:
    handlers: dict[str, dict[str, object]] = {}
    for event in CODEX_EVENTS:
        if scope == "project":
            command = (
                'repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" && '
                f'exec "$repo_root/.loopguard/hooks/codex-hook" {event}'
            )
        else:
            command = (
                f"{shlex.quote(executable)} hook-entry codex {event} "
                f"--integration-version {INTEGRATION_VERSION}"
            )
        handlers[event] = {
            "type": "command",
            "command": command,
            "timeout": 1,
            "statusMessage": f"LoopGuard: {event}",
        }
    return handlers


def _plugin_expected_commands() -> dict[str, str]:
    return {
        event: f"${{PLUGIN_ROOT}}/bin/loopguard-hook {event}" for event in CODEX_EVENTS
    }


def _matching_hook_report(
    report: Sequence[Mapping[str, Any]],
    expected: Mapping[str, str],
    *,
    plugin_id: str | None = None,
    source_path: Path | None = None,
) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
    matching: list[Mapping[str, Any]] = []
    found: set[str] = set()
    for hook in report:
        event = next(
            (
                candidate
                for candidate in CODEX_EVENTS
                if hook.get("eventName") == _event_wire_name(candidate)
            ),
            None,
        )
        if event is None:
            continue
        if plugin_id is not None and hook.get("pluginId") != plugin_id:
            continue
        expected_command = expected[event]
        if plugin_id is not None:
            raw_source = hook.get("sourcePath")
            if not isinstance(raw_source, str):
                continue
            plugin_root = Path(raw_source).parent.parent
            expected_command = expected_command.replace("${PLUGIN_ROOT}", str(plugin_root))
        windows_command = (
            f"loopguard hook-entry codex {event} --integration-version {INTEGRATION_VERSION}"
        )
        if hook.get("command") not in {expected_command, windows_command}:
            continue
        if source_path is not None:
            raw_source = hook.get("sourcePath")
            if not isinstance(raw_source, str):
                continue
            if Path(raw_source).expanduser().resolve(strict=False) != source_path.resolve(strict=False):
                continue
        matching.append(hook)
        found.add(event)
    return matching, tuple(event for event in CODEX_EVENTS if event not in found)


def _event_wire_name(event: str) -> str:
    return event[:1].lower() + event[1:]


def _merge_handlers(body: dict[str, Any], expected: Mapping[str, dict[str, object]]) -> bool:
    hooks = body.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise CodexInstallError("Codex hooks.json field 'hooks' must be an object")
    changed = False
    for event, handler in expected.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise CodexInstallError(f"Codex hook event {event!r} must be an array")
        if _contains_handler(body, event, handler):
            continue
        group: dict[str, object] = {"hooks": [handler]}
        if event in _TOOL_EVENTS:
            group["matcher"] = "*"
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
            kept_handlers = [handler for handler in original if handler != expected_handler]
            if len(kept_handlers) != len(original):
                changed = True
            if kept_handlers:
                copied = dict(group)
                copied["hooks"] = kept_handlers
                kept_groups.append(copied)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return changed


def _contains_handler(body: Mapping[str, Any], event: str, expected: Mapping[str, object]) -> bool:
    hooks = body.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return False
    return any(
        isinstance(group, dict)
        and isinstance(group.get("hooks"), list)
        and any(handler == expected for handler in group["hooks"])
        for group in groups
    )


def _read_settings(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CodexInstallError("refusing to read a symlinked Codex hooks file")
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CodexInstallError("Codex hooks file must contain valid JSON") from exc
    if not isinstance(body, dict):
        raise CodexInstallError("Codex hooks file must contain a JSON object")
    _validate_settings_shape(body)
    return body


def _validate_settings_shape(body: Mapping[str, Any]) -> None:
    hooks = body.get("hooks", {})
    if not isinstance(hooks, dict):
        raise CodexInstallError("Codex hooks.json field 'hooks' must be an object")
    for event, groups in hooks.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            raise CodexInstallError("Codex hook events must map to arrays")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise CodexInstallError("Codex hook matcher groups must contain a hooks array")
            if not all(isinstance(handler, dict) for handler in group["hooks"]):
                raise CodexInstallError("Codex hook handlers must be objects")


@contextlib.contextmanager
def _settings_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.loopguard.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_atomic_settings(path: Path, body: dict[str, Any]) -> None:
    _validate_settings_shape(body)
    if path.is_symlink():
        raise CodexInstallError("refusing to replace a symlinked Codex hooks file")
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    backup: Path | None = None
    if path.exists():
        backup = path.with_name(f"{path.name}.loopguard-backup-{time.time_ns()}")
        shutil.copy2(path, backup)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.loopguard-", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        encoded = _json_bytes(body)
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        if os.name == "posix":
            os.fchmod(descriptor, old_mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(temp_path, path)
        verified = _read_settings(path)
        if verified != body:
            raise CodexInstallError("Codex hooks validation failed after atomic replace")
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()
        if backup is not None and backup.exists():
            shutil.copy2(backup, path)
        raise
    else:
        if backup is not None:
            backup.unlink()


def _owned_file_needs_write(path: Path, content: bytes, *, executable: bool = False) -> bool:
    if path.is_symlink():
        raise CodexInstallError(f"refusing to replace symlinked integration file: {path}")
    if not path.exists():
        return True
    try:
        if path.read_bytes() != content:
            return True
    except OSError as exc:
        raise CodexInstallError(f"could not inspect integration file: {path}") from exc
    if os.name == "posix":
        expected_mode = 0o755 if executable else 0o600
        return stat.S_IMODE(path.stat().st_mode) != expected_mode
    return False


def _write_owned_file(path: Path, content: bytes, *, executable: bool = False) -> None:
    if path.is_symlink():
        raise CodexInstallError(f"refusing to replace symlinked integration file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.loopguard-", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        os.write(descriptor, content)
        os.fsync(descriptor)
        if os.name == "posix":
            os.fchmod(descriptor, 0o755 if executable else 0o600)
        os.close(descriptor)
        descriptor = -1
        os.replace(temp_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def _find_git_root(start: Path) -> Path:
    current = start.expanduser().resolve(strict=False)
    if not current.is_dir():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise CodexInstallError("project fallback requires a canonical Git repository root")


def _plugin_file_bytes() -> dict[str, bytes]:
    return {
        ".codex-plugin/plugin.json": _json_bytes(_manifest()),
        "hooks/hooks.json": _json_bytes(_plugin_hooks()),
        "bin/loopguard-hook": _PLUGIN_LAUNCHER.encode(),
    }


def _definition_checksum(expected: Mapping[str, Mapping[str, object]]) -> str:
    return hashlib.sha256(_json_bytes(expected)).hexdigest()


def _tree_checksum(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative, content in sorted(files.items()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode()


def _copy_json(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))


def _run_codex(command_runner: Any, command: Sequence[str]) -> None:
    try:
        result = command_runner(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexInstallError(f"Codex plugin command failed: {command[1:3]}") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown Codex error").strip()
        raise CodexInstallError(f"Codex plugin command failed: {message}")


def _list_codex_plugins(command_runner: Any, codex_executable: str) -> list[dict[str, Any]]:
    command = [codex_executable, "plugin", "list", "--json"]
    try:
        result = command_runner(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexInstallError("Codex plugin list command failed") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown Codex error").strip()
        raise CodexInstallError(f"Codex plugin list command failed: {message}")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CodexInstallError("Codex plugin list returned invalid JSON") from exc
    installed = payload.get("installed") if isinstance(payload, dict) else None
    if not isinstance(installed, list) or not all(isinstance(item, dict) for item in installed):
        raise CodexInstallError("Codex plugin list returned an invalid installed-plugin shape")
    return installed


def _matching_plugin(
    plugins: Sequence[Mapping[str, Any]],
    *,
    expected_checksum: str,
) -> bool:
    for plugin in plugins:
        if (
            plugin.get("pluginId") != f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
            or plugin.get("version") != PLUGIN_VERSION
            or plugin.get("installed") is not True
            or plugin.get("enabled") is not True
        ):
            continue
        source = plugin.get("source")
        raw_path = source.get("path") if isinstance(source, dict) else None
        if not isinstance(raw_path, str):
            continue
        path = Path(raw_path)
        try:
            files = {relative: (path / relative).read_bytes() for relative in _plugin_file_bytes()}
        except OSError:
            continue
        if _tree_checksum(files) == expected_checksum:
            return True
    return False


def _list_codex_hooks(codex_executable: str, cwd: Path) -> list[dict[str, Any]]:
    """Read Codex's effective hook hashes/trust through the supported app-server API."""
    command = [codex_executable, "app-server"]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise CodexInstallError("could not start Codex app-server for hook verification") from exc
    try:
        if process.stdin is None or process.stdout is None:
            raise CodexInstallError("Codex app-server did not expose its stdio transport")
        _write_rpc(
            process,
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "loopguard",
                        "title": "LoopGuard",
                        "version": PLUGIN_VERSION,
                    }
                },
            },
        )
        _read_rpc_response(process, 0, timeout=5)
        _write_rpc(process, {"method": "initialized", "params": {}})
        _write_rpc(
            process,
            {
                "method": "hooks/list",
                "id": 1,
                "params": {"cwds": [str(cwd.expanduser().resolve(strict=False))]},
            },
        )
        response = _read_rpc_response(process, 1, timeout=10)
    finally:
        if process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=1)
        if process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
    if "error" in response:
        raise CodexInstallError(f"Codex hooks/list failed: {response['error']}")
    result = response.get("result")
    rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise CodexInstallError("Codex hooks/list returned an invalid result shape")
    hooks = rows[0].get("hooks")
    if not isinstance(hooks, list) or not all(isinstance(hook, dict) for hook in hooks):
        raise CodexInstallError("Codex hooks/list returned an invalid hook shape")
    return hooks


def _write_rpc(process: subprocess.Popen[str], message: Mapping[str, object]) -> None:
    if process.stdin is None:
        raise CodexInstallError("Codex app-server stdin closed unexpectedly")
    try:
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise CodexInstallError("Codex app-server closed during hook verification") from exc


def _read_rpc_response(
    process: subprocess.Popen[str],
    request_id: int,
    *,
    timeout: float,
) -> dict[str, Any]:
    if process.stdout is None:
        raise CodexInstallError("Codex app-server stdout closed unexpectedly")
    responses: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def read_until_match() -> None:
        assert process.stdout is not None
        while True:
            line = process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                responses.put(message)
                return
        responses.put(None)

    reader = threading.Thread(target=read_until_match, daemon=True)
    reader.start()
    try:
        response = responses.get(timeout=timeout)
    except queue.Empty as exc:
        raise CodexInstallError(
            f"Codex app-server timed out waiting for response {request_id}"
        ) from exc
    if response is None:
        raise CodexInstallError("Codex app-server closed before hook verification completed")
    return response
