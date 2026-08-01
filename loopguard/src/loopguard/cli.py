from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any

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
data_app = typer.Typer(help="Manage owner-only local LoopGuard data.")
dx_app = typer.Typer(help="Inspect privacy-safe local developer-experience metrics.")
router_app = typer.Typer(help="Validate and evaluate deterministic model routing.")
router_policy_app = typer.Typer(help="Validate versioned routing policies.")
update_app = typer.Typer(help="Verify signed LoopGuard release manifests.")
migrate_app = typer.Typer(help="Check and apply backup-first local schema migrations.")
app.add_typer(daemon_app, name="daemon")
app.add_typer(config_app, name="config")
app.add_typer(integrations_app, name="integrations")
app.add_typer(data_app, name="data")
app.add_typer(dx_app, name="dx")
app.add_typer(router_app, name="router")
app.add_typer(update_app, name="update")
app.add_typer(migrate_app, name="migrate")
integrations_app.add_typer(integrations_install_app, name="install")
integrations_app.add_typer(integrations_verify_app, name="verify")
integrations_app.add_typer(integrations_uninstall_app, name="uninstall")
router_app.add_typer(router_policy_app, name="policy")


@update_app.command("check")
def update_check(
    manifest: Path = typer.Option(..., exists=True, dir_okay=False),
    public_key: Path = typer.Option(..., exists=True, dir_okay=False),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Verify release metadata without downloading or executing an artifact."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from .security.update import UpdateVerifier

    try:
        loaded = serialization.load_pem_public_key(public_key.read_bytes())
        if not isinstance(loaded, Ed25519PublicKey):
            raise ValueError("update key must be Ed25519")
        result = UpdateVerifier(loaded).verify(manifest.read_bytes())
    except (OSError, ValueError):
        result = None
    payload = {
        "status": result.code if result is not None else "invalid_public_key",
        "trusted": bool(result and result.trusted),
        "manifest": dict(result.manifest) if result and result.manifest else None,
    }
    if as_json:
        _echo_json(payload)
    else:
        typer.echo(
            "Trusted signed update manifest."
            if payload["trusted"]
            else f"Update check failed: {payload['status']}."
        )
    if not payload["trusted"]:
        raise typer.Exit(1)


@migrate_app.command("check")
def migrate_check(
    database: Path | None = typer.Option(None, help="Override the local events database."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Inspect migration compatibility without modifying local state."""
    from .security.migrate import LocalMigrationService

    path = database or ControlPaths.from_home(home).events_db
    status = LocalMigrationService(b"\0" * 32).check(path)
    payload = _migration_payload(status)
    if as_json:
        _echo_json(payload)
    else:
        typer.echo(
            f"{status.code}: schema {status.current_schema} → {status.target_schema}"
        )
        for step in status.required_steps:
            typer.echo(f"- {step}")
    if status.code not in {"current", "migration_required", "not_initialized"}:
        raise typer.Exit(1)


@migrate_app.command("apply")
def migrate_apply(
    database: Path | None = typer.Option(None, help="Override the local events database."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    yes: bool = typer.Option(False, "--yes", help="Apply the previewed forward migration."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Create and verify an encrypted backup, then apply forward-only migrations."""
    from .security.migrate import LocalMigrationService

    path = database or ControlPaths.from_home(home).events_db
    preview = LocalMigrationService(b"\0" * 32).check(path)
    if preview.code == "migration_required" and not yes:
        payload = {**_migration_payload(preview), "status": "confirmation_required"}
        _echo_json(payload) if as_json else typer.echo(
            "Migration previewed; rerun with --yes to create an encrypted backup and apply."
        )
        raise typer.Exit(2)
    service = LocalMigrationService(_migration_backup_key(create=True))
    result = service.apply(path)
    payload = _migration_payload(result)
    _echo_json(payload) if as_json else typer.echo(
        f"{result.code}: schema {result.current_schema}; backup {result.backup_path or 'not needed'}"
    )
    if result.code != "current":
        raise typer.Exit(1)


@migrate_app.command("restore")
def migrate_restore(
    backup: Path = typer.Option(..., exists=True, dir_okay=False),
    database: Path = typer.Option(..., dir_okay=False),
    yes: bool = typer.Option(False, "--yes", help="Replace the database from this backup."),
) -> None:
    """Restore a verified encrypted backup after an exact operator confirmation."""
    if not yes:
        typer.echo("Restore not applied; rerun with --yes after stopping the daemon.")
        raise typer.Exit(2)
    from .security.migrate import LocalMigrationService

    LocalMigrationService(_migration_backup_key(create=False)).restore(backup, database)
    typer.echo(f"Restored {database}. Run `loopguard doctor --json` before restart.")


def _migration_backup_key(*, create: bool) -> bytes:
    from .control.crypto import MissingKeyError, PlatformKeyStore, generate_data_key

    store = PlatformKeyStore(service_name="dev.loopguard.migration-backup")
    key_id = "migration-backup-v1"
    try:
        return store.get(key_id)
    except MissingKeyError:
        if not create:
            raise
        key = generate_data_key()
        store.put(key_id, key)
        return key


def _migration_payload(status: Any) -> dict[str, object]:
    return {
        "status": status.code,
        "current_schema": status.current_schema,
        "target_schema": status.target_schema,
        "required_steps": list(status.required_steps),
        "backup_path": str(status.backup_path) if status.backup_path else None,
        "restore_command": status.restore_command,
    }


@router_policy_app.command("validate")
def router_policy_validate(
    path: Path,
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Validate a strict, versioned routing policy without invoking a model."""
    try:
        from .router.policy import validate_policy_toml

        if path.stat().st_size > 1_000_000:
            raise ValueError("policy exceeds the 1 MB size limit")
        document = validate_policy_toml(path.read_text("utf-8"))
    except Exception as exc:
        payload = {"status": "error", "message": str(exc)}
        typer.echo(json.dumps(payload, separators=(",", ":")) if as_json else payload["message"])
        raise typer.Exit(1)
    payload = {"status": "valid", "version": document.version, "rules": len(document.rules)}
    typer.echo(
        json.dumps(payload, separators=(",", ":")) if as_json else f"Valid {document.version}"
    )


@router_app.command("evaluate")
def router_evaluate(
    since: str = typer.Option("30d", help="Window such as 30d or 24h."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    minimum_sample_size: int = typer.Option(30, min=1, max=1_000_000),
) -> None:
    """Compare observed holdout outcomes; never report estimated savings as fact."""
    from datetime import datetime, timedelta, timezone

    from .router.evaluation import evaluate_outcomes
    from .router.outcomes import OutcomeRecorder

    try:
        amount, unit = int(since[:-1]), since[-1:].lower()
        maximum = 3_650 if unit == "d" else 87_600
        if amount <= 0 or unit not in {"d", "h"} or amount > maximum:
            raise ValueError
    except (ValueError, IndexError):
        typer.echo(
            '{"status":"error","message":"invalid --since window"}'
            if as_json
            else "Invalid --since window"
        )
        raise typer.Exit(1)
    delta = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
    paths = ControlPaths.from_home(home)
    recorder = OutcomeRecorder.for_path(paths.home / "router.db")
    try:
        cutoff = datetime.now(timezone.utc) - delta
        outcomes = [item for item in recorder.list(limit=10_000) if item.created_at >= cutoff]
    finally:
        recorder.close()
    report = evaluate_outcomes(outcomes, minimum_sample_size=minimum_sample_size)
    payload = report.model_dump(mode="json")
    if as_json:
        _echo_json(payload)
        return
    typer.echo(
        f"Control {report.control.sample_size} / routed {report.routed.sample_size}; "
        f"recommendation: {report.recommendation}."
    )


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
    started = time.monotonic()
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
    if home is None:
        try:
            from .adapters.setup import record_dx_metric

            record_dx_metric(
                ControlPaths.from_home().home,
                event="quickstart",
                duration_seconds=max(0.0, time.monotonic() - started),
                outcome_code="ok",
            )
        except (OSError, RuntimeError):
            pass
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
    fix_safe: bool = typer.Option(False, "--fix-safe", help="Repair LoopGuard-owned state only."),
    bundle: Path | None = typer.Option(None, "--bundle", help="Write a redacted diagnostic ZIP."),
    confirm_bundle: bool = typer.Option(
        False,
        "--confirm-bundle",
        help="Confirm the displayed diagnostic redaction preview.",
    ),
) -> None:
    """Check daemon, storage, encryption, dispatch, and integration health."""
    _run_doctor(
        as_json=as_json,
        verbose=verbose,
        home=home,
        fix_safe=fix_safe,
        bundle=bundle,
        confirm_bundle=confirm_bundle,
    )


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


def _run_doctor(
    *,
    as_json: bool,
    verbose: bool,
    home: Path | None,
    fix_safe: bool = False,
    bundle: Path | None = None,
    confirm_bundle: bool = False,
) -> None:
    from .adapters.doctor import (
        build_diagnostic_payload,
        diagnostic_bundle_preview,
        probe_report,
        write_diagnostic_bundle,
    )
    from .adapters.setup import apply_safe_fixes
    from .adapters.service_install import (
        ServiceInstallError,
        safe_fix_user_service,
        service_status,
    )

    paths = ControlPaths.from_home(home)
    fixed: tuple[Path, ...] = ()
    if fix_safe:
        fixed = apply_safe_fixes(
            paths.home,
            (
                paths.config,
                paths.events_db,
                paths.events_db.with_name(f"{paths.events_db.name}-wal"),
                paths.events_db.with_name(f"{paths.events_db.name}-shm"),
                paths.pid,
                paths.integration_trust,
            ),
        )
        try:
            service_fixes = safe_fix_user_service(home=paths.home)
        except ServiceInstallError:
            service_fixes = ()
    else:
        service_fixes = ()
    report = build_doctor_report(paths)
    try:
        report["user_service"] = service_status(home=paths.home).to_dict()
    except ServiceInstallError:
        report["user_service"] = {
            "status": "unsafe",
            "automatic_startup": "unavailable",
        }
    integrations = probe_report(
        daemon_reachable=report["daemon"] == "reachable",
        home=paths.home,
    )
    report["agent_integrations"] = integrations.to_dict()
    report["safe_fixes"] = len(fixed) + len(service_fixes)
    if verbose:
        report["diagnostics"] = {
            "home": str(paths.home),
            "config": str(paths.config),
            "socket": str(paths.socket),
        }
    if bundle is not None and not confirm_bundle:
        preview = diagnostic_bundle_preview()
        if as_json:
            _echo_json({"status": "confirmation_required", "bundle_preview": preview})
        else:
            typer.echo(json.dumps(preview, indent=2))
            typer.echo("Review this preview, then rerun with --confirm-bundle.")
        raise typer.Exit(2)
    if bundle is not None:
        report["diagnostic_bundle"] = str(
            write_diagnostic_bundle(
                bundle,
                build_diagnostic_payload(report, integrations),
                confirmed=confirm_bundle,
            )
        )
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


@app.command("setup")
def setup_cmd(
    agent: str = typer.Option("auto", "--agent", help="auto, codex, claude, or comma-separated."),
    scope: str | None = typer.Option(None, "--scope", help="user or project."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview every change without writes."),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Require explicit agents and scope with stable errors.",
    ),
    resume_from: str | None = typer.Option(None, "--resume-from", help="Resume at a named step."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Prepare a local protected-session integration without approving vendor trust."""
    from .adapters.setup import SetupError, SetupRequest, record_dx_metric, run_setup

    try:
        requested = tuple(part.strip().lower() for part in agent.split(",") if part.strip())
        resolved = _resolve_setup_agents(requested, non_interactive=non_interactive)
        resolved_scope = scope if scope is not None else (None if non_interactive else "user")
        request = SetupRequest(
            agents=resolved,
            scope=resolved_scope,
            dry_run=dry_run,
            non_interactive=non_interactive,
            resume_from=resume_from,
        )
        paths = ControlPaths.from_home(home)
        actions = _GuidedSetupActions(
            agents=resolved,
            scope=resolved_scope or "user",
            paths=paths,
            repository=Path.cwd(),
        )
        result = run_setup(
            request,
            actions=actions,
            metric_recorder=(
                None
                if dry_run
                else lambda event, duration, outcome: record_dx_metric(
                    paths.home,
                    event=event,
                    duration_seconds=duration,
                    outcome_code=outcome,
                )
            ),
        )
    except (SetupError, RuntimeError) as exc:
        payload = {"status": "error", "code": str(exc).partition(":")[0], "message": str(exc)}
        _echo_json(payload) if as_json else typer.echo(str(exc))
        raise typer.Exit(2)
    payload = result.to_dict()
    payload["trust_review"] = {vendor: _trust_review_step(vendor) for vendor in result.agents}
    if as_json:
        _echo_json(payload)
    else:
        typer.echo("LoopGuard setup preview:" if dry_run else "LoopGuard setup result:")
        for step in result.steps:
            typer.echo(f"  {step.name}: {step.status}")
            for detail in step.details:
                typer.echo(f"    - {detail}")
        for vendor, instruction in payload["trust_review"].items():
            typer.echo(f"  {vendor} trust review: {instruction}")
        typer.echo(f"Status: {result.status}")
        if result.resume_command:
            typer.echo(f"Resume: {result.resume_command}")
    if result.status == "failed":
        raise typer.Exit(1)


@app.command("uninstall")
def uninstall_cmd(
    agent: str = typer.Option("auto", "--agent", help="auto, codex, claude, or comma-separated."),
    scope: str = typer.Option("user", "--scope", help="user or project."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview owned removals only."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Remove only LoopGuard-owned integrations and retain all local event data."""
    from .adapters.service_install import ServiceInstallError, uninstall_user_service
    from .adapters.setup import SetupError, uninstall_owned_integrations

    requested = tuple(part.strip().lower() for part in agent.split(",") if part.strip())
    try:
        agents = _resolve_setup_agents(requested, non_interactive=False, all_if_auto=True)
        repository = _project_root(Path.cwd()) if scope == "project" else Path.cwd().resolve()
        removers = {
            vendor: _integration_remover(
                vendor,
                scope=scope,
                repository=repository,
                paths=ControlPaths.from_home(home),
            )
            for vendor in agents
        }
        result = uninstall_owned_integrations(
            agents=agents,
            removers=removers,
            dry_run=dry_run,
        )
        if requested == ("auto",):
            service = uninstall_user_service(
                home=ControlPaths.from_home(home).home,
                dry_run=dry_run,
            ).to_dict()
        else:
            service = {
                "status": "retained_for_other_integrations",
                "changed": False,
            }
    except (ServiceInstallError, SetupError, RuntimeError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _echo_json(payload) if as_json else typer.echo(str(exc))
        raise typer.Exit(1)
    payload = result.to_dict()
    payload["service"] = service
    if as_json:
        _echo_json(payload)
    else:
        typer.echo(f"LoopGuard integrations: {result.status}.")
        typer.echo(
            "Local LoopGuard data retained. Purge requires `loopguard data purge --confirm`."
        )
        typer.echo(f"LoopGuard user service: {service['status']}.")


@data_app.command("purge")
def data_purge_cmd(
    confirm: bool = typer.Option(
        False, "--confirm", help="Permanently delete local LoopGuard data."
    ),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Permanently remove owner-controlled local data after explicit confirmation."""
    from .adapters.service_install import ServiceInstallError, service_status
    from .adapters.setup import SetupError, purge_data

    try:
        paths = ControlPaths.from_home(home)
        if confirm and service_status(home=paths.home).installed:
            raise SetupError(
                "LGD-DATA-SERVICE-ACTIVE: uninstall the LoopGuard user service before purge"
            )
        removed = purge_data(paths.home, confirmed=confirm)
    except (ServiceInstallError, SetupError) as exc:
        code = str(exc).partition(":")[0]
        payload = {
            "status": "confirmation_required" if code == "LGD-DATA-CONFIRMATION" else "error",
            "code": code,
        }
        _echo_json(payload) if as_json else typer.echo(str(exc))
        raise typer.Exit(2 if code == "LGD-DATA-CONFIRMATION" else 1)
    payload = {"status": "removed" if removed else "not_found"}
    _echo_json(payload) if as_json else typer.echo(f"LoopGuard data: {payload['status']}.")


@dx_app.command("report")
def dx_report_cmd(
    local: bool = typer.Option(False, "--local", help="Read local-only, privacy-safe timings."),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Report setup timing and outcomes; upload is always off by default."""
    from .adapters.setup import SetupError, build_dx_report

    if not local:
        typer.echo("Specify --local. Upload is disabled unless telemetry is explicitly enabled.")
        raise typer.Exit(2)
    try:
        report = build_dx_report(ControlPaths.from_home(home).home)
    except SetupError as exc:
        payload = {"status": "error", "code": str(exc).partition(":")[0]}
        _echo_json(payload) if as_json else typer.echo(str(exc))
        raise typer.Exit(1)
    _echo_json(report) if as_json else typer.echo(json.dumps(report, indent=2))


@app.command("feedback")
def feedback_cmd() -> None:
    """Print a version-prefilled public support route without sending data."""
    version = _loopguard_version()
    typer.echo(
        "Report an issue: https://github.com/neanicc/agent/issues/new"
        f"?title=LoopGuard%20{version}%20feedback&labels=loopguard"
    )
    typer.echo("No diagnostic data is uploaded automatically.")


@app.command("sessions")
def sessions_cmd(
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """List locally observed sessions and exact attached-hook coverage."""
    try:
        payload = _protected_sessions(ControlPaths.from_home(home))
    except RuntimeError as exc:
        error = {"status": "error", "code": "LGD-SESSIONS-UNAVAILABLE", "message": str(exc)}
        _echo_json(error) if as_json else typer.echo(str(exc))
        raise typer.Exit(1)
    if as_json:
        _echo_json({"sessions": payload})
        return
    if not payload:
        typer.echo("No protected attached sessions observed yet.")
        return
    for session in payload:
        coverage = ", ".join(session["coverage"]) or "none verified"
        typer.echo(
            f"{session['vendor']} {session['status']} · session {session['session_id']} · "
            f"coverage: {coverage}"
        )


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


@daemon_app.command("install")
def daemon_install(
    executable: Path | None = typer.Option(
        None,
        help="Absolute LoopGuard console-script path; detected by default.",
    ),
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing a service."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Install or upgrade the current user's automatic LoopGuard daemon service."""
    from .adapters.service_install import ServiceInstallError, install_user_service

    paths = ControlPaths.from_home(home)
    try:
        resolved = _service_executable(executable)
        if not dry_run:
            ensure_private_home(paths.home)
        result = install_user_service(
            executable=resolved,
            home=paths.home,
            dry_run=dry_run,
        )
    except (RuntimeError, ServiceInstallError, UnsafeStatePathError) as exc:
        payload = {"status": "error", "code": "LGD-SERVICE-INSTALL", "message": str(exc)}
        _echo_json(payload) if as_json else typer.echo(str(exc))
        raise typer.Exit(1)
    _echo_json(result.to_dict()) if as_json else typer.echo(
        f"LoopGuard user service: {result.status} ({result.destination})."
    )


@daemon_app.command("uninstall")
def daemon_uninstall(
    home: Path | None = typer.Option(None, help="Override LOOPGUARD_HOME."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview owned service removal."),
    as_json: bool = typer.Option(False, "--json", help="Emit stable machine-readable output."),
) -> None:
    """Stop and remove only the current user's LoopGuard-owned service definition."""
    from .adapters.service_install import ServiceInstallError, uninstall_user_service

    try:
        result = uninstall_user_service(
            home=ControlPaths.from_home(home).home,
            dry_run=dry_run,
        )
    except ServiceInstallError as exc:
        payload = {"status": "error", "code": "LGD-SERVICE-UNINSTALL", "message": str(exc)}
        _echo_json(payload) if as_json else typer.echo(str(exc))
        raise typer.Exit(1)
    _echo_json(result.to_dict()) if as_json else typer.echo(
        f"LoopGuard user service: {result.status}. Local event data retained."
    )


@daemon_app.command("status")
def daemon_status(
    as_json: bool = typer.Option(False, "--json"),
    home: Path | None = typer.Option(None),
) -> None:
    """Report user-service installation plus live daemon reachability."""
    from .adapters.service_install import ServiceInstallError, service_status

    paths = ControlPaths.from_home(home)
    core = build_doctor_report(paths)
    try:
        service = service_status(home=paths.home).to_dict()
    except ServiceInstallError:
        service = {"status": "unsafe", "running": False, "automatic_startup": "unavailable"}
    payload = {
        "schema_version": 1,
        "daemon": core["daemon"],
        "service": service,
        "errors": core["errors"],
    }
    if as_json:
        _echo_json(payload)
    else:
        typer.echo(f"LoopGuard daemon: {payload['daemon']}.")
        typer.echo(f"User service: {service['status']}.")
    if core["daemon"] != "reachable":
        raise typer.Exit(1)


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
        raise RuntimeError(
            "could not execute Claude Code to determine plugin compatibility"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError("could not determine the installed Claude Code version")
    return result.stdout.strip()


class _GuidedSetupActions:
    def __init__(
        self,
        *,
        agents: tuple[str, ...],
        scope: str,
        paths: ControlPaths,
        repository: Path,
    ) -> None:
        self.agents = agents
        self.scope = scope
        self.paths = paths
        self.repository = (
            _project_root(repository) if scope == "project" else repository.resolve(strict=False)
        )
        self.versions = {vendor: _optional_vendor_version(vendor) for vendor in agents}

    def describe(self, step: str) -> tuple[str, ...]:
        if step == "install_detected":
            return tuple(
                f"{vendor}: {version or 'not detected'}"
                for vendor, version in self.versions.items()
            )
        if step == "daemon_extra":
            return ("Python extras: cryptography, keyring",)
        if step == "service":
            from .adapters.service_install import default_service_path

            target = default_service_path(sys.platform, self.paths.home)
            return (f"User service definition: {target}",)
        if step == "integration":
            return tuple(self._integration_description(vendor) for vendor in self.agents)
        if step == "daemon":
            return ("Start or restart only the LoopGuard user service",)
        if step == "synthetic_event":
            return ("Send one local synthetic lifecycle event and verify durable intake",)
        return ()

    def execute(self, step: str, *, dry_run: bool) -> str:
        if step == "install_detected":
            missing = [vendor for vendor, version in self.versions.items() if version is None]
            if missing:
                raise RuntimeError(f"installed agent not detected: {','.join(missing)}")
            return "supported"
        if step == "daemon_extra":
            missing = [
                dependency
                for dependency in ("cryptography", "keyring")
                if importlib.util.find_spec(dependency) is None
            ]
            if missing:
                raise RuntimeError(
                    "daemon dependencies missing; install loopguard[control]: " + ",".join(missing)
                )
            return "supported"
        if step == "service":
            if dry_run:
                return "preview"
            from .adapters import service_install

            result = service_install.install_user_service(
                executable=_service_executable(None),
                home=self.paths.home,
            )
            return "changed" if result.changed else "unchanged"
        if step == "integration":
            changed = False
            for vendor in self.agents:
                changed = self._install_integration(vendor, dry_run=dry_run) or changed
            return "preview" if dry_run else "changed" if changed else "unchanged"
        if step == "daemon":
            if dry_run:
                return "preview"
            from .adapters import service_install

            status = service_install.service_status(home=self.paths.home)
            if status.running:
                return "supported"
            service_install.start_user_service(home=self.paths.home)
            return "changed"
        if step == "synthetic_event":
            if dry_run:
                return "preview"
            from .adapters import service_install

            service_install.verify_synthetic_event(home=self.paths.home)
            return "supported"
        raise RuntimeError(f"unsupported setup step: {step}")

    def _install_integration(self, vendor: str, *, dry_run: bool) -> bool:
        version = self.versions[vendor]
        assert version is not None
        if vendor == "codex":
            from .adapters.codex_hooks import (
                codex_plugin_available,
                install_codex_hooks,
                install_codex_plugin,
            )

            if codex_plugin_available(version) and self.scope == "user":
                result = install_codex_plugin(
                    self.paths.home / "integrations",
                    dry_run=dry_run,
                )
            else:
                if self.scope == "project" and _plugin_is_installed("codex", self.repository):
                    raise RuntimeError(
                        "global Codex plugin is already active; refusing duplicate project hooks"
                    )
                settings = (
                    self.repository / ".codex" / "hooks.json"
                    if self.scope == "project"
                    else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json"
                )
                result = install_codex_hooks(
                    settings,
                    scope=self.scope,
                    repository=self.repository,
                    dry_run=dry_run,
                )
            return result.changed
        from .adapters.claude_hooks import (
            claude_plugin_available,
            install_claude_hooks,
            install_claude_plugin,
        )

        if claude_plugin_available(version) and self.scope == "user":
            result = install_claude_plugin(
                self.paths.home / "integrations",
                scope=self.scope,
                dry_run=dry_run,
            )
        else:
            if self.scope == "project" and _plugin_is_installed("claude", self.repository):
                raise RuntimeError(
                    "Claude plugin is already active; refusing duplicate project hooks"
                )
            config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
            settings = (
                self.repository / ".claude" / "settings.json"
                if self.scope == "project"
                else config / "settings.json"
            )
            result = install_claude_hooks(
                settings,
                scope=self.scope,
                repository=self.repository,
                dry_run=dry_run,
            )
        return result.changed

    def _integration_description(self, vendor: str) -> str:
        version = self.versions[vendor]
        assert version is not None
        if vendor == "codex":
            from .adapters.codex_hooks import codex_plugin_available

            if codex_plugin_available(version) and self.scope == "user":
                target = self.paths.home / "integrations/codex-marketplace"
                return f"codex plugin: stage checksum-pinned marketplace at {target}"
            target = (
                self.repository / ".codex/hooks.json"
                if self.scope == "project"
                else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json"
            )
            return f"codex fallback hooks: merge exact LoopGuard handlers into {target}"
        from .adapters.claude_hooks import claude_plugin_available

        if claude_plugin_available(version) and self.scope == "user":
            target = self.paths.home / "integrations/claude-marketplace"
            return f"claude plugin: stage checksum-pinned marketplace at {target}"
        config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        target = (
            self.repository / ".claude/settings.json"
            if self.scope == "project"
            else config / "settings.json"
        )
        return f"claude fallback hooks: merge exact LoopGuard handlers into {target}"


def _resolve_setup_agents(
    requested: tuple[str, ...],
    *,
    non_interactive: bool,
    all_if_auto: bool = False,
) -> tuple[str, ...]:
    if not requested:
        return requested
    if requested != ("auto",):
        return tuple(dict.fromkeys(requested))
    if non_interactive:
        return requested
    if all_if_auto:
        return ("codex", "claude")
    detected = tuple(vendor for vendor in ("codex", "claude") if shutil.which(vendor))
    if not detected:
        raise RuntimeError("no supported local agent installation was detected")
    return detected


def _project_root(path: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(path.resolve(strict=False)), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("project scope requires an accessible Git repository") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("project scope requires an accessible Git repository")
    return Path(result.stdout.strip()).resolve(strict=False)


def _service_executable(explicit: Path | None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser().absolute())
    invoked = Path(sys.argv[0]).expanduser()
    if invoked.name.lower().startswith("loopguard"):
        candidates.append(invoked.absolute())
    discovered = shutil.which("loopguard")
    if discovered is not None:
        candidates.append(Path(discovered).absolute())
    for candidate in candidates:
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return candidate
    raise RuntimeError(
        "an absolute executable LoopGuard console script is required; use --executable"
    )


def _optional_vendor_version(vendor: str) -> str | None:
    if shutil.which(vendor) is None:
        return None
    try:
        return _codex_version(vendor) if vendor == "codex" else _claude_version(vendor)
    except RuntimeError:
        return None


def _trust_review_step(vendor: str) -> str:
    if vendor == "codex":
        return "Open Codex /hooks; verify LoopGuard's exact definition and choose trust yourself."
    return "Run `loopguard integrations verify claude`; review Claude Code's exact hook source."


def _plugin_is_installed(vendor: str, repository: Path) -> bool:
    try:
        if vendor == "codex":
            from .adapters.codex_hooks import verify_codex_plugin

            return verify_codex_plugin(cwd=repository, hook_report=[]).installed
        from .adapters.claude_hooks import verify_claude_plugin

        return verify_claude_plugin().installed
    except Exception as exc:  # noqa: BLE001 - normalize vendor registry failures
        raise RuntimeError(
            f"could not verify the {vendor} plugin registry; refusing potential duplicate hooks"
        ) from exc


def _integration_remover(
    vendor: str,
    *,
    scope: str,
    repository: Path,
    paths: ControlPaths,
):
    def remove(dry_run: bool) -> str:
        removed = False
        if vendor == "codex":
            from .adapters.codex_hooks import (
                find_codex_fallback_events,
                uninstall_codex_hooks,
                uninstall_codex_plugin,
                verify_codex_plugin,
            )

            settings = (
                repository / ".codex" / "hooks.json"
                if scope == "project"
                else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json"
            )
            fallback_installed = bool(find_codex_fallback_events(settings))
            plugin_installed = False
            if scope == "user" and shutil.which("codex"):
                try:
                    plugin_installed = verify_codex_plugin(cwd=repository).installed
                except Exception:  # noqa: BLE001 - exact fallback removal remains available
                    plugin_installed = False
            if dry_run:
                return "preview" if fallback_installed or plugin_installed else "not_installed"
            if fallback_installed:
                removed = uninstall_codex_hooks(
                    settings,
                    scope=scope,
                    repository=repository,
                ).changed
            if plugin_installed:
                removed = uninstall_codex_plugin().changed or removed
        else:
            from .adapters.claude_hooks import (
                find_claude_fallback_events,
                uninstall_claude_hooks,
                uninstall_claude_plugin,
                verify_claude_plugin,
            )

            config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
            settings = (
                repository / ".claude" / "settings.json"
                if scope == "project"
                else config / "settings.json"
            )
            fallback_installed = bool(find_claude_fallback_events(settings))
            plugin_installed = False
            if scope == "user" and shutil.which("claude"):
                try:
                    plugin_installed = verify_claude_plugin().installed
                except Exception:  # noqa: BLE001 - exact fallback removal remains available
                    plugin_installed = False
            if dry_run:
                return "preview" if fallback_installed or plugin_installed else "not_installed"
            if fallback_installed:
                removed = uninstall_claude_hooks(
                    settings,
                    scope=scope,
                    repository=repository,
                ).changed
            if plugin_installed:
                removed = uninstall_claude_plugin().changed or removed
        return "removed" if removed else "not_installed"

    return remove


def _protected_sessions(paths: ControlPaths) -> list[dict[str, object]]:
    if not paths.events_db.exists():
        return []
    try:
        from .adapters.doctor import probe_report
        from .control.store import EventStore

        integration_report = probe_report(daemon_reachable=True, home=paths.home)
        events = []
        with EventStore(paths.events_db) as store:
            cursor = 0
            while len(events) < 10_000:
                page = store.read_local_after(cursor, 500)
                if not page:
                    break
                events.extend(page)
                cursor = page[-1].local_log_seq
    except Exception as exc:  # noqa: BLE001 - normalize store/key failures without data leakage
        raise RuntimeError(
            "LoopGuard sessions are unavailable; run `loopguard doctor --json`."
        ) from exc
    grouped: dict[tuple[str, str], set[str]] = {}
    for stored in events:
        event = stored.event
        if not event.source.endswith("-hooks"):
            continue
        vendor = event.source.removesuffix("-hooks")
        grouped.setdefault((vendor, event.session.session_id), set()).add(event.kind.value)
    result: list[dict[str, object]] = []
    for (vendor, session_id), observed in sorted(grouped.items()):
        try:
            surface = integration_report.surface(f"{vendor}-attached-local")
            coverage = surface.coverage
            version = surface.installed_version
        except KeyError:
            coverage = ()
            version = None
        result.append(
            {
                "session_id": session_id,
                "vendor": vendor,
                "vendor_version": version,
                "status": "protected_attached",
                "coverage": list(coverage),
                "observed_events": sorted(observed),
            }
        )
    if result:
        try:
            from .adapters.setup import record_protected_session_metric

            record_protected_session_metric(paths.home)
        except (OSError, RuntimeError):
            pass
    return result


def _loopguard_version() -> str:
    try:
        return importlib.metadata.version("loopguard")
    except importlib.metadata.PackageNotFoundError:
        return "development"


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
    claude_executable: str = typer.Option(
        "claude", help="Claude Code executable for plugin install."
    ),
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
    settings: Path | None = typer.Option(
        None, help="Fallback settings.json path; omit for plugin."
    ),
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
    settings: Path | None = typer.Option(
        None, help="Fallback settings.json path; omit for plugin."
    ),
    scope: str = typer.Option("user", help="Fallback scope: user or project."),
    repository: Path | None = typer.Option(None, help="Repository for project fallback scope."),
    executable: str = typer.Option("loopguard", help="Executable recorded in fallback hooks."),
    claude_executable: str = typer.Option(
        "claude", help="Claude Code executable for plugin removal."
    ),
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

        console.print(
            f"[red]Unknown project {project_id!r}.[/red] Try one of: "
            + ", ".join(p.id for p in PROJECTS)
        )
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
        console.print(f"[{color}][{step}] {name}({path!r}) -> {output[:70]}[/{color}]")

    try:
        if proj.kind == "multi":
            result = run_multi_agent(
                prov,
                agents=[(a.name, a.system) for a in proj.agents],
                task=the_task,
                tools_schema=schemas,
                tool_impls=impls,
                guard=guard,
                run_id=proj.id,
                max_steps=proj.max_steps,
                on_event=on_event,
            )
        else:
            agent = proj.agents[0]
            result = run_agent(
                prov,
                system=agent.system,
                task=the_task,
                tools_schema=schemas,
                tool_impls=impls,
                guard=guard,
                run_id=proj.id,
                agent_name=agent.name,
                max_steps=proj.max_steps,
                on_event=on_event,
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
def serve(host: str = "127.0.0.1", port: int = 8000):
    _load_dotenv()
    import uvicorn

    from .server import create_app

    uvicorn.run(create_app(), host=host, port=port)
