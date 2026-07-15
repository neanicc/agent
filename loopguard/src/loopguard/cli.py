from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import LoopGuardConfig
from .configuration import ControlConfiguration, ConfigurationError, load_configuration
from .control.diagnostics import build_doctor_report
from .control.errors import error_for, render_error
from .control.paths import ControlPaths, UnsafeStatePathError, ensure_private_home
from .storage import read_jsonl

app = typer.Typer(help="LoopGuard semantic circuit breaker")
daemon_app = typer.Typer(help="Manage the owner-only local LoopGuard daemon.")
config_app = typer.Typer(help="Inspect and validate layered LoopGuard configuration.")
integrations_app = typer.Typer(help="Install and verify native agent integrations.")
integrations_install_app = typer.Typer(help="Install one native agent integration.")
integrations_verify_app = typer.Typer(help="Verify one native agent integration.")
integrations_uninstall_app = typer.Typer(help="Uninstall one native agent integration.")
app.add_typer(daemon_app, name="daemon")
app.add_typer(config_app, name="config")
app.add_typer(integrations_app, name="integrations")
integrations_app.add_typer(integrations_install_app, name="install")
integrations_app.add_typer(integrations_verify_app, name="verify")
integrations_app.add_typer(integrations_uninstall_app, name="uninstall")


def _echo_json(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _configuration_error(*, as_json: bool, details: dict[str, object] | None = None) -> None:
    typer.echo(
        render_error(
            error_for("LGD-CONFIG-007", details=details or {}),
            as_json=as_json,
        )
    )
    raise typer.Exit(1)


def _exit_named_error(
    code: str,
    *,
    as_json: bool,
    details: dict[str, object] | None = None,
) -> None:
    typer.echo(render_error(error_for(code, details=details or {}), as_json=as_json))
    raise typer.Exit(1)


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env (cwd or repo root) without a dependency.

    Existing environment variables win, so an explicit `export` always overrides .env.
    """
    here = Path.cwd()
    for base in (here, *here.parents):
        env = base / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
            return


class DemoMode(str, Enum):
    pause = "pause"
    flag = "flag"
    auto = "auto"
    warn = "warn"


class DemoScenario(str, Enum):
    single = "single"
    pingpong = "pingpong"
    cerebras = "cerebras"


@app.command("quickstart")
def quickstart_cmd(
    home: Path | None = typer.Option(None, help="Parent for isolated temporary state."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Prove offline loop protection through the real daemon in under two minutes."""
    try:
        from .quickstart import QuickstartUnavailableError, run_quickstart

        result = asyncio.run(run_quickstart(home))
    except ImportError:
        typer.echo(render_error(error_for("LGD-DEPS-006"), as_json=as_json))
        raise typer.Exit(1)
    except QuickstartUnavailableError:
        typer.echo(render_error(error_for("LGD-CAP-005"), as_json=as_json))
        raise typer.Exit(1)
    except UnsafeStatePathError:
        _exit_named_error("LGD-STATE-002", as_json=as_json)
    if as_json:
        _echo_json(result.to_dict())
        return
    typer.echo("Loop detected before another paid turn.")
    typer.echo(f"Protected path: {result.persisted_events} encrypted events → real LoopGuard.")
    typer.echo("No model or API key used. No telemetry sent. Temporary state removed.")
    typer.echo("Next: loopguard setup --agent auto")


@app.command("doctor")
def doctor_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
    verbose: bool = typer.Option(False, "--verbose", help="Add redacted local diagnostics."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
) -> None:
    """Check daemon, storage, encryption, dispatch, and integration health."""
    _run_doctor(as_json=as_json, verbose=verbose, home=home)


@app.command("context-mcp", hidden=True)
def context_mcp_cmd(
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
) -> None:
    """Run the capability-bound stdio context server for a managed agent."""
    paths = ControlPaths.from_home(home)
    resources: list[object] = []
    try:
        ensure_private_home(paths.home)
        from .context.capability import load_capability_from_environment
        from .context.digest import DigestBuilder
        from .context.handoff import HandoffStore
        from .context.index import SymbolIndex
        from .context.journal import ChangeJournal
        from .context.leases import LeaseManager
        from .context.mcp_server import ContextServices, build_context_server
        from .context.worktrees import WorktreeManager

        capability = load_capability_from_environment(paths.home)
        journal = ChangeJournal(paths.home / "context.db")
        leases = LeaseManager(paths.home / "leases.db")
        handoffs = HandoffStore(paths.home / "handoffs.db")
        symbol_index = SymbolIndex(paths.home / "symbols.db")
        worktree_manager = WorktreeManager(root=paths.home / "worktrees")
        resources.extend([worktree_manager, symbol_index, handoffs, leases, journal])
        server = build_context_server(
            ContextServices(
                journal=journal,
                leases=leases,
                handoffs=handoffs,
                digest=DigestBuilder(),
                symbol_index=symbol_index,
                worktree_manager=worktree_manager,
            ),
            capability=capability,
        )
        server.run()
    except (ImportError, OSError, ValueError, PermissionError) as exc:
        typer.echo(f"LoopGuard context MCP unavailable: {type(exc).__name__}", err=True)
        raise typer.Exit(1)
    finally:
        for resource in resources:
            close = getattr(resource, "close", None)
            if close is not None:
                close()


def _run_doctor(*, as_json: bool, verbose: bool, home: Path | None) -> None:
    paths = ControlPaths.from_home(home)
    report = build_doctor_report(paths)
    if verbose:
        report["diagnostics"] = {
            "home": str(paths.home),
            "config": str(paths.config),
            "socket": str(paths.socket),
        }
    if as_json:
        _echo_json(report)
    elif report["healthy"]:
        typer.echo("LoopGuard is healthy.")
    else:
        for payload in report["errors"]:
            typer.echo(
                render_error(
                    error_for(
                        str(payload["code"]),
                        request_id=payload.get("request_id"),
                        details=payload.get("details") or {},
                    ),
                    as_json=False,
                )
            )
    if not report["healthy"]:
        raise typer.Exit(1)


@app.command("explain")
def explain_error(
    code: str = typer.Argument(..., help="Stable LoopGuard error code."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Print the cause, safe fixes, and local reference for an error code."""
    try:
        error = error_for(code.upper())
    except ValueError:
        typer.echo(f"Unknown LoopGuard error code: {code}")
        raise typer.Exit(1)
    typer.echo(render_error(error, as_json=as_json))


@daemon_app.command("start")
def daemon_start(
    foreground: bool = typer.Option(
        False,
        "--foreground",
        help="Run attached for launchd, systemd, containers, and debugging.",
    ),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Start the local daemon without unmanaged background forks."""
    if not foreground:
        typer.echo(
            render_error(
                error_for(
                    "LGD-CAP-005",
                    details={"capability": "managed_background_service"},
                ),
                as_json=as_json,
            )
        )
        raise typer.Exit(1)
    try:
        loaded = load_configuration(home=home)
    except ConfigurationError:
        _configuration_error(as_json=as_json)
    try:
        from .control.lifecycle import run_foreground_daemon
        from .control.crypto import CryptoError
        from .control.migrations import MigrationError
        from .control.store import EventStoreError
        from .control.transport import TransportError, UnsafeTransportPathError

        paths = ControlPaths.from_home(home)
        asyncio.run(
            run_foreground_daemon(
                paths,
                loaded.configuration,
                on_ready=lambda: typer.echo(
                    json.dumps({"status": "ready"}, separators=(",", ":"))
                    if as_json
                    else f"LoopGuard daemon ready at {paths.socket}"
                ),
            )
        )
    except ImportError:
        _exit_named_error("LGD-DEPS-006", as_json=as_json)
    except UnsafeStatePathError:
        _exit_named_error("LGD-STATE-002", as_json=as_json)
    except MigrationError:
        _exit_named_error("LGD-SCHEMA-003", as_json=as_json)
    except UnsafeTransportPathError as exc:
        code = "LGD-CAP-005" if exc.code == "socket_path_too_long" else "LGD-STATE-002"
        _exit_named_error(code, as_json=as_json)
    except TransportError:
        _exit_named_error("LGD-CAP-005", as_json=as_json)
    except (EventStoreError, CryptoError):
        _exit_named_error("LGD-STORE-008", as_json=as_json)


@daemon_app.command("status")
def daemon_status(
    as_json: bool = typer.Option(False, "--json"),
    home: Path | None = typer.Option(None),
) -> None:
    """Report whether the configured daemon endpoint is reachable."""
    _run_doctor(as_json=as_json, verbose=False, home=home)


@daemon_app.command("doctor")
def daemon_doctor(
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    home: Path | None = typer.Option(None),
) -> None:
    """Run the same full diagnostics as `loopguard doctor`."""
    _run_doctor(as_json=as_json, verbose=verbose, home=home)


@config_app.command("path")
def config_path_cmd(
    as_json: bool = typer.Option(False, "--json"),
    home: Path | None = typer.Option(None),
) -> None:
    """Print the active configuration file path."""
    path = ControlPaths.from_home(home).config
    if as_json:
        _echo_json({"path": str(path)})
    else:
        typer.echo(path)


@config_app.command("show")
def config_show(
    as_json: bool = typer.Option(False, "--json"),
    home: Path | None = typer.Option(None),
) -> None:
    """Show effective redacted values, sources, and precedence."""
    try:
        loaded = load_configuration(home=home)
    except ConfigurationError:
        _configuration_error(as_json=as_json)
    payload = loaded.redacted_dict()
    if as_json:
        _echo_json(payload)
    else:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@config_app.command("validate")
def config_validate(
    path: Path | None = typer.Argument(None),
    as_json: bool = typer.Option(False, "--json"),
    home: Path | None = typer.Option(None),
) -> None:
    """Validate a configuration file without starting the daemon."""
    try:
        loaded = load_configuration(path=path, home=home)
    except ConfigurationError:
        _configuration_error(
            as_json=as_json,
            details={"path": str(path) if path is not None else None},
        )
    if as_json:
        _echo_json({"valid": True, "path": str(loaded.path), "schema_version": 1})
    else:
        typer.echo(f"Configuration is valid: {loaded.path}")


def _codex_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not execute Codex to determine plugin compatibility") from exc
    if result.returncode != 0:
        raise RuntimeError("could not determine the installed Codex version")
    return result.stdout.strip()


def _claude_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not execute Claude Code to determine plugin compatibility") from exc
    if result.returncode != 0:
        raise RuntimeError("could not determine the installed Claude Code version")
    return result.stdout.strip()


def _integration_failure(message: str, *, as_json: bool) -> None:
    if as_json:
        _echo_json({"status": "error", "message": message})
    else:
        typer.echo(f"LoopGuard integration error: {message}")
    raise typer.Exit(1)


@integrations_install_app.command("codex")
def integrations_install_codex(
    fallback: bool = typer.Option(
        False,
        "--fallback",
        help="Use hooks.json only when this Codex version cannot activate plugins.",
    ),
    scope: str = typer.Option("user", help="Fallback scope: user or project."),
    settings: Path | None = typer.Option(None, help="Fallback hooks.json path."),
    repository: Path | None = typer.Option(None, help="Repository for project fallback scope."),
    executable: str = typer.Option("loopguard", help="LoopGuard executable for fallback hooks."),
    codex_executable: str = typer.Option("codex", help="Codex executable for plugin install."),
    codex_version: str | None = typer.Option(None, help="Explicit compatibility version probe."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME for plugin staging."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changing Codex."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Install LoopGuard's checksum-pinned Codex integration."""
    from .adapters.codex_hooks import (
        CodexInstallError,
        codex_project_hooks_path,
        codex_plugin_available,
        find_codex_fallback_events,
        install_codex_hooks,
        install_codex_plugin,
    )

    try:
        version = codex_version or _codex_version(codex_executable)
        plugin_available = codex_plugin_available(version)
        if fallback:
            if plugin_available:
                raise CodexInstallError(
                    "this Codex version supports plugins; fallback would duplicate plugin hooks"
                )
            fallback_path = settings or (
                (repository or Path.cwd()) / ".codex" / "hooks.json"
                if scope == "project"
                else Path.home() / ".codex" / "hooks.json"
            )
            if dry_run:
                result = install_codex_hooks(
                    fallback_path,
                    executable=executable,
                    scope=scope,
                    plugin_available=False,
                    repository=repository,
                    dry_run=True,
                )
            else:
                result = install_codex_hooks(
                    fallback_path,
                    executable=executable,
                    scope=scope,
                    plugin_available=False,
                    repository=repository,
                )
        else:
            if not plugin_available:
                raise CodexInstallError(
                    "installed Codex cannot activate plugins; rerun with --fallback after review"
                )
            codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
            candidate_paths = [settings or codex_home / "hooks.json"]
            project_path = codex_project_hooks_path(repository or Path.cwd())
            if project_path is not None:
                candidate_paths.append(project_path)
            for candidate in candidate_paths:
                if find_codex_fallback_events(candidate):
                    raise CodexInstallError(
                        f"fallback hooks remain at {candidate}; uninstall them before plugin install"
                    )
            paths = ControlPaths.from_home(home)
            ensure_private_home(paths.home)
            result = install_codex_plugin(
                paths.home / "integrations",
                codex_executable=codex_executable,
                dry_run=dry_run,
            )
    except (CodexInstallError, RuntimeError, UnsafeStatePathError) as exc:
        _integration_failure(str(exc), as_json=as_json)
    payload = result.to_dict()
    payload["next_step"] = "Review and trust the exact LoopGuard definition in Codex /hooks."
    if as_json:
        _echo_json(payload)
    else:
        typer.echo(f"LoopGuard Codex {result.mode} prepared ({result.hook_checksum}).")
        typer.echo(str(payload["next_step"]))


@integrations_verify_app.command("codex")
def integrations_verify_codex(
    settings: Path | None = typer.Option(None, help="Fallback hooks.json path."),
    scope: str = typer.Option("user", help="Fallback scope: user or project."),
    executable: str = typer.Option("loopguard", help="Executable recorded in fallback hooks."),
    codex_executable: str = typer.Option("codex", help="Codex executable for plugin verification."),
    cwd: Path | None = typer.Option(
        None,
        help="Working directory for effective Codex hook lookup (defaults to current directory).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Verify exact installation, discovery, and Codex-reported hook trust."""
    from .adapters.codex_hooks import (
        CodexInstallError,
        verify_codex_hooks,
        verify_codex_plugin,
    )

    try:
        if settings is None:
            result = verify_codex_plugin(
                codex_executable=codex_executable,
                cwd=cwd or Path.cwd(),
            )
        else:
            result = verify_codex_hooks(
                settings,
                executable=executable,
                scope=scope,
            )
    except CodexInstallError as exc:
        _integration_failure(str(exc), as_json=as_json)
    if as_json:
        _echo_json(result.to_dict())
    else:
        typer.echo(f"LoopGuard Codex integration: {result.status}")
        if result.trust_required:
            typer.echo("Review the exact hook definition in Codex /hooks.")
    if not result.healthy:
        raise typer.Exit(1)


@integrations_uninstall_app.command("codex")
def integrations_uninstall_codex(
    settings: Path | None = typer.Option(None, help="Fallback hooks.json path; omit for plugin."),
    scope: str = typer.Option("user", help="Fallback scope: user or project."),
    repository: Path | None = typer.Option(None, help="Repository for project fallback scope."),
    executable: str = typer.Option("loopguard", help="Executable recorded in fallback hooks."),
    codex_executable: str = typer.Option("codex", help="Codex executable for plugin removal."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Remove only the exact LoopGuard Codex plugin or fallback handlers."""
    from .adapters.codex_hooks import (
        CodexInstallError,
        uninstall_codex_hooks,
        uninstall_codex_plugin,
    )

    try:
        if settings is None:
            result = uninstall_codex_plugin(codex_executable=codex_executable)
        else:
            result = uninstall_codex_hooks(
                settings,
                executable=executable,
                scope=scope,
                repository=repository,
            )
    except CodexInstallError as exc:
        _integration_failure(str(exc), as_json=as_json)
    if as_json:
        _echo_json(result.to_dict())
    else:
        typer.echo(f"LoopGuard Codex integration: {result.status}")


@integrations_install_app.command("claude")
def integrations_install_claude(
    fallback: bool = typer.Option(
        False,
        "--fallback",
        help="Use settings hooks only when this Claude version cannot activate plugins.",
    ),
    cloud: bool = typer.Option(
        False,
        "--cloud",
        help="Prepare project hooks for signed HTTPS fallback in Claude Code remote sessions.",
    ),
    scope: str = typer.Option("user", help="Plugin scope, or fallback scope: user or project."),
    settings: Path | None = typer.Option(None, help="Fallback settings.json path."),
    repository: Path | None = typer.Option(None, help="Repository for project fallback scope."),
    executable: str = typer.Option("loopguard", help="LoopGuard executable for fallback hooks."),
    claude_executable: str = typer.Option("claude", help="Claude Code executable for plugin install."),
    claude_version: str | None = typer.Option(None, help="Explicit compatibility version probe."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME for plugin staging."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without changing Claude Code."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Install LoopGuard's Claude Code plugin or reviewed fallback hooks."""
    from .adapters.claude_hooks import (
        ClaudeInstallError,
        claude_plugin_available,
        find_claude_fallback_events,
        install_claude_hooks,
        install_claude_plugin,
        verify_claude_plugin,
    )
    from .adapters.cloud_hook_client import CloudHookClient

    try:
        version = claude_version or _claude_version(claude_executable)
        plugin_available = claude_plugin_available(version)
        fallback_requested = fallback or cloud
        repo = (repository or Path.cwd()).resolve()
        claude_config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        if cloud:
            if scope != "project":
                raise ClaudeInstallError("cloud fallback requires --scope project")
            if CloudHookClient.from_environment() is None:
                raise ClaudeInstallError(
                    "cloud fallback requires LOOPGUARD_CLOUD_INGEST_URL, "
                    "LOOPGUARD_HOOK_KEY_ID, and LOOPGUARD_HOOK_SECRET"
                )
        if fallback_requested:
            if plugin_available and not (cloud and scope == "project"):
                raise ClaudeInstallError(
                    "this Claude Code version supports plugins; fallback would duplicate plugin hooks"
                )
            if cloud:
                plugin = verify_claude_plugin(claude_executable=claude_executable)
                if plugin.installed:
                    raise ClaudeInstallError(
                        "the LoopGuard Claude plugin is installed; uninstall it before cloud fallback"
                    )
            fallback_path = settings or (
                repo / ".claude" / "settings.json"
                if scope == "project"
                else claude_config / "settings.json"
            )
            result = install_claude_hooks(
                fallback_path,
                executable=executable,
                scope=scope,
                plugin_available=False,
                repository=repo if scope == "project" else None,
                dry_run=dry_run,
            )
        else:
            if not plugin_available:
                raise ClaudeInstallError(
                    "installed Claude Code cannot activate plugins; rerun with --fallback after review"
                )
            candidate_paths = [settings or claude_config / "settings.json"]
            project_path = repo / ".claude" / "settings.json"
            if project_path not in candidate_paths:
                candidate_paths.append(project_path)
            for candidate in candidate_paths:
                if find_claude_fallback_events(candidate):
                    raise ClaudeInstallError(
                        f"fallback hooks remain at {candidate}; uninstall them before plugin install"
                    )
            paths = ControlPaths.from_home(home)
            ensure_private_home(paths.home)
            result = install_claude_plugin(
                paths.home / "integrations",
                claude_executable=claude_executable,
                scope=scope,
                dry_run=dry_run,
            )
    except (ClaudeInstallError, RuntimeError, UnsafeStatePathError, ValueError) as exc:
        _integration_failure(str(exc), as_json=as_json)
    payload = result.to_dict()
    payload["next_step"] = (
        "Run loopguard integrations verify claude; cloud delivery remains conditional "
        "until a signed endpoint smoke test succeeds."
        if cloud
        else "Run loopguard integrations verify claude and review the exact hooks in Claude Code."
    )
    if as_json:
        _echo_json(payload)
    else:
        typer.echo(f"LoopGuard Claude {result.mode} prepared ({result.status}).")
        typer.echo(str(payload["next_step"]))


@integrations_verify_app.command("claude")
def integrations_verify_claude(
    settings: Path | None = typer.Option(None, help="Fallback settings.json path; omit for plugin."),
    scope: str = typer.Option("user", help="Fallback scope: user or project."),
    repository: Path | None = typer.Option(None, help="Repository for project fallback scope."),
    executable: str = typer.Option("loopguard", help="Executable recorded in fallback hooks."),
    claude_executable: str = typer.Option(
        "claude", help="Claude Code executable for plugin verification."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Verify exact Claude installation bytes and managed-hook policy."""
    from .adapters.claude_hooks import (
        ClaudeInstallError,
        verify_claude_hooks,
        verify_claude_plugin,
    )

    try:
        if settings is None:
            result = verify_claude_plugin(claude_executable=claude_executable)
        else:
            result = verify_claude_hooks(
                settings,
                executable=executable,
                scope=scope,
                repository=repository,
            )
    except ClaudeInstallError as exc:
        _integration_failure(str(exc), as_json=as_json)
    if as_json:
        _echo_json(result.to_dict())
    else:
        typer.echo(f"LoopGuard Claude integration: {result.status}")
        if result.cloud_status == "conditional":
            typer.echo("Cloud delivery is conditional until a signed endpoint smoke test succeeds.")
    if not result.healthy:
        raise typer.Exit(1)


@integrations_uninstall_app.command("claude")
def integrations_uninstall_claude(
    settings: Path | None = typer.Option(None, help="Fallback settings.json path; omit for plugin."),
    scope: str = typer.Option("user", help="Fallback scope: user or project."),
    repository: Path | None = typer.Option(None, help="Repository for project fallback scope."),
    executable: str = typer.Option("loopguard", help="Executable recorded in fallback hooks."),
    claude_executable: str = typer.Option("claude", help="Claude Code executable for plugin removal."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Remove only exact LoopGuard Claude plugin or fallback handlers."""
    from .adapters.claude_hooks import (
        ClaudeInstallError,
        uninstall_claude_hooks,
        uninstall_claude_plugin,
    )

    try:
        if settings is None:
            result = uninstall_claude_plugin(claude_executable=claude_executable)
        else:
            result = uninstall_claude_hooks(
                settings,
                executable=executable,
                scope=scope,
                repository=repository,
            )
    except ClaudeInstallError as exc:
        _integration_failure(str(exc), as_json=as_json)
    if as_json:
        _echo_json(result.to_dict())
    else:
        typer.echo(f"LoopGuard Claude integration: {result.status}")


@app.command("hook-entry", hidden=True)
def hook_entry_command(
    vendor: str,
    hook_name: str,
    integration_version: int = typer.Option(..., help="Pinned integration protocol version."),
    fail_closed: bool = typer.Option(False, "--fail-closed"),
) -> None:
    """Internal native-hook entry point; input is one bounded JSON object on stdin."""
    from .adapters.codex_hooks import INTEGRATION_VERSION
    from .adapters.cloud_hook_client import FallbackHookClient
    from .adapters.hook_entry import MAX_HOOK_INPUT_BYTES, process_hook_input

    if integration_version != INTEGRATION_VERSION:
        typer.echo("LoopGuard hook warning: incompatible integration version", err=True)
        return
    raw_input = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    result = process_hook_input(
        vendor,
        hook_name,
        raw_input,
        client=FallbackHookClient.from_environment(),
        fail_closed=fail_closed,
    )
    if result.stdout:
        typer.echo(result.stdout, nl=False)
    if result.stderr:
        typer.echo(result.stderr, err=True, nl=False)
    if result.exit_code:
        raise typer.Exit(result.exit_code)


@app.command()
def demo(
    live: bool = typer.Option(False, "--live", help="Run a real LLM agent (needs an API key)."),
    model: str = typer.Option("cerebras/gpt-oss-120b", help="Model id (litellm routing string)."),
    provider: str = typer.Option("auto", help="auto | litellm | cerebras"),
    mode: DemoMode = typer.Option(DemoMode.pause, help="pause | flag | auto | warn"),
    scenario: DemoScenario = DemoScenario.single,
    guard: bool = typer.Option(True, "--guard/--no-guard"),
):
    if scenario == DemoScenario.pingpong:
        from .demos import run_pingpong_demo

        run_pingpong_demo(action=mode.value)
        return
    if live or scenario == DemoScenario.cerebras:
        _load_dotenv()
        from .live_demo import run_live

        run_live(model=model, provider=provider, mode=mode.value, use_guard=guard)
        return
    from .demos import run_broken_agent

    run_broken_agent(use_guard=guard, action=mode.value)


@app.command("projects")
def projects_cmd():
    """List the demo projects an agent can really run."""
    from .server.projects import PROJECTS

    console = Console()
    table = Table(title="LoopGuard demo projects")
    table.add_column("id", style="cyan")
    table.add_column("kind")
    table.add_column("what happens")
    for p in PROJECTS:
        table.add_row(p.id, p.kind, p.blurb)
    console.print(table)


@app.command("run")
def run_project(
    project_id: str = typer.Argument(..., help="Project id (see `loopguard projects`)."),
    mode: DemoMode = typer.Option(DemoMode.pause, help="pause | flag | auto | warn"),
    model: str = typer.Option("cerebras/gpt-oss-120b", help="Model id."),
    provider: str = typer.Option("auto", help="auto | litellm | cerebras"),
    task: str = typer.Option(None, help="Override the project task (custom project)."),
):
    """Run a real agent on a demo project and guard it live in the terminal.

    In `pause` mode you get the full terminate / continue / allowlist / inject flow;
    `auto` applies the judge's fix; `flag` reports without blocking.
    """
    _load_dotenv()
    from .agent import run_agent, run_multi_agent
    from .guard import LoopGuard
    from .judge import LLMJudge
    from .providers import make_provider
    from .server.projects import build_tools, get_project, workspace_listing

    console = Console()
    proj = get_project(project_id)
    if proj is None:
        from .server.projects import PROJECTS

        console.print(f"[red]Unknown project {project_id!r}.[/red] Try one of: "
                      + ", ".join(p.id for p in PROJECTS))
        raise typer.Exit(1)

    try:
        prov = make_provider(model, provider)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    guard = LoopGuard(
        LoopGuardConfig(action=mode.value, max_tool_calls=15),
        judge=LLMJudge(prov, context=workspace_listing(proj)),
    )
    schemas, impls = build_tools(proj)
    the_task = task or proj.task
    console.rule(f"[bold cyan]{proj.label} · {proj.kind} · {mode.value}")
    console.print(f"[dim]{proj.blurb}[/dim]\n[bold]Task:[/bold] {the_task}\n")

    def on_event(step, name, args, output):
        is_err = output.startswith("Error:")
        color = "red" if is_err else "green"
        path = args.get("path", "")
        console.print(f'[{color}][{step}] {name}({path!r}) -> {output[:70]}[/{color}]')

    try:
        if proj.kind == "multi":
            result = run_multi_agent(
                prov, agents=[(a.name, a.system) for a in proj.agents], task=the_task,
                tools_schema=schemas, tool_impls=impls, guard=guard, run_id=proj.id,
                max_steps=proj.max_steps, on_event=on_event,
            )
        else:
            agent = proj.agents[0]
            result = run_agent(
                prov, system=agent.system, task=the_task, tools_schema=schemas,
                tool_impls=impls, guard=guard, run_id=proj.id, agent_name=agent.name,
                max_steps=proj.max_steps, on_event=on_event,
            )
        if result.final_text:
            console.print(f"\n[bold green]Agent:[/bold green] {result.final_text}")
        if result.stopped_by_guard:
            console.print("[bold red]LoopGuard stopped the agent.[/bold red]")
    except Exception as exc:  # noqa: BLE001 - show real API/runtime errors, don't crash
        console.print(f"[red]Run error: {exc}[/red]")
        raise typer.Exit(1)
    finally:
        allow = guard.allowlisted_tools()
        if allow:
            console.print(f"[yellow]Allowlisted tools:[/yellow] {', '.join(allow)}")
        s = guard.summary()
        console.print(
            f"\n[dim]Total: {s['events']} events · {s['tokens']} tokens · "
            f"${s['cost_usd']:.4f} (+ judge ${s['judge_cost_usd']:.4f})[/dim]"
        )


@app.command("inspect")
def inspect_run(path: Path):
    console = Console()
    rows = read_jsonl(path)
    if not rows:
        console.print(f"[yellow]No events found in {path}.[/yellow]")
        return

    table = Table(title=f"LoopGuard run: {path}")
    table.add_column("#", justify="right")
    table.add_column("agent")
    table.add_column("kind")
    table.add_column("tool")
    table.add_column("input/error")
    table.add_column("tokens", justify="right")
    table.add_column("cost", justify="right")

    total_tokens = 0
    total_cost = 0.0
    for i, row in enumerate(rows, start=1):
        tokens = row.get("tokens", 0) or 0
        cost = row.get("cost_usd", 0) or 0
        total_tokens += tokens
        total_cost += cost
        detail = (row.get("error") or row.get("input_text") or row.get("output_text") or "")[:60]
        table.add_row(
            str(i),
            row.get("agent", ""),
            row.get("kind", ""),
            row.get("tool_name") or "",
            detail,
            str(row.get("tokens", 0)),
            f"${row.get('cost_usd', 0):.4f}",
        )

    console.print(table)
    console.print(f"[dim]{len(rows)} events · {total_tokens} tokens · ${total_cost:.4f}[/dim]")


@app.command("init-config")
def init_config(path: Path = Path("loopguard.json")):
    path.write_text(ControlConfiguration().model_dump_json(indent=2) + "\n")
    print(f"Wrote {path}")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    _load_dotenv()
    import uvicorn

    from .server import create_app

    uvicorn.run(create_app(), host=host, port=port)
