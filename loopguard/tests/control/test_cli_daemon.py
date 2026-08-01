from __future__ import annotations

import asyncio
import json
import os
import stat
from types import SimpleNamespace
from pathlib import Path

import pytest

from typer.testing import CliRunner

from loopguard.cli import app
from loopguard.cli_docs import render_cli_reference
from loopguard.configuration import ControlConfiguration
from loopguard.control.lifecycle import run_foreground_daemon
from loopguard.control.paths import (
    ControlPaths,
    UnsafeStatePathError,
    ensure_private_home,
    remove_owned_pid_file,
    write_pid_file,
)
from loopguard.control.store import EventStore

from .daemon_test_support import short_socket_path


runner = CliRunner()


def test_production_commands_are_discoverable():
    root = runner.invoke(app, ["--help"])
    daemon = runner.invoke(app, ["daemon", "--help"])
    config = runner.invoke(app, ["config", "--help"])
    integrations = runner.invoke(app, ["integrations", "--help"])

    assert root.exit_code == daemon.exit_code == config.exit_code == integrations.exit_code == 0
    for command in (
        "quickstart",
        "setup",
        "uninstall",
        "sessions",
        "doctor",
        "feedback",
        "daemon",
        "config",
        "integrations",
        "data",
        "dx",
    ):
        assert command in root.stdout
    for command in ("start", "install", "status", "doctor", "uninstall"):
        assert command in daemon.stdout
    for command in ("path", "show", "validate"):
        assert command in config.stdout
    for command in ("install", "verify", "uninstall"):
        assert command in integrations.stdout


def test_legacy_demo_server_defaults_to_loopback():
    result = runner.invoke(app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "127.0.0.1" in result.stdout
    assert "0.0.0.0" not in result.stdout


def test_doctor_json_reports_missing_daemon_with_structured_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPGUARD_HOME", str(tmp_path))

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["daemon"] == "unreachable"
    assert body["protocol"]["version"] == 1
    assert body["store"]["status"] == "missing"
    assert body["user_service"]["automatic_startup"] in {"supported", "experimental"}
    assert body["agent_integrations"]["schema_version"] == 1
    assert len(body["agent_integrations"]["surfaces"]) == 6
    assert body["errors"][0]["code"] == "LGD-DAEMON-001"
    assert "loopguard daemon start --foreground" in body["errors"][0]["suggested_commands"]
    assert "traceback" not in result.stdout.lower()


def test_setup_noninteractive_requires_explicit_agent_and_scope() -> None:
    result = runner.invoke(app, ["setup", "--agent", "auto", "--non-interactive", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["code"] == "LGD-SETUP-NONINTERACTIVE"


def test_data_purge_requires_confirmation_and_dx_report_stays_local(tmp_path) -> None:
    home = tmp_path / "loopguard"
    home.mkdir()
    (home / "events.db").write_text("events")

    refused = runner.invoke(app, ["data", "purge", "--home", str(home), "--json"])
    report = runner.invoke(app, ["dx", "report", "--local", "--home", str(home), "--json"])

    assert refused.exit_code == 2
    assert home.exists()
    assert json.loads(refused.stdout)["code"] == "LGD-DATA-CONFIRMATION"
    assert json.loads(report.stdout)["upload_enabled"] is False


def test_data_purge_refuses_to_orphan_an_installed_service(tmp_path, monkeypatch) -> None:
    home = tmp_path / "loopguard"
    home.mkdir()
    (home / "events.db").write_text("events")
    monkeypatch.setattr(
        "loopguard.adapters.service_install.service_status",
        lambda **_kwargs: SimpleNamespace(installed=True),
    )

    result = runner.invoke(
        app,
        ["data", "purge", "--confirm", "--home", str(home), "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "LGD-DATA-SERVICE-ACTIVE"
    assert (home / "events.db").exists()


def test_background_start_refuses_unmanaged_fork_with_capability_error(tmp_path):
    result = runner.invoke(
        app,
        ["daemon", "start", "--home", str(tmp_path), "--json"],
    )

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["code"] == "LGD-CAP-005"
    assert body["retryable"] is False
    assert "--foreground" in " ".join(body["suggested_commands"])


def test_service_install_dry_run_previews_without_writes(tmp_path):
    executable = tmp_path / ("loopguard.exe" if os.name == "nt" else "loopguard")
    executable.write_text("#!/bin/sh\n")
    if os.name == "posix":
        os.chmod(executable, 0o700)
    home = tmp_path / "state"

    result = runner.invoke(
        app,
        [
            "daemon",
            "install",
            "--executable",
            str(executable),
            "--home",
            str(home),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["status"] == "preview"
    assert not home.exists()


def test_config_path_show_and_environment_precedence(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "daemon": {"idle_timeout_seconds": 12},
                "telemetry": {"enabled": False},
            }
        )
    )
    monkeypatch.setenv("LOOPGUARD_HOME", str(tmp_path))
    monkeypatch.setenv("LOOPGUARD_IDLE_TIMEOUT_SECONDS", "7")

    path_result = runner.invoke(app, ["config", "path", "--json"])
    show_result = runner.invoke(app, ["config", "show", "--json"])

    assert path_result.exit_code == show_result.exit_code == 0
    assert json.loads(path_result.stdout)["path"] == str(config_path)
    shown = json.loads(show_result.stdout)
    assert shown["config"]["daemon"]["idle_timeout_seconds"] == 7
    assert shown["sources"]["daemon.idle_timeout_seconds"] == "environment"
    assert shown["precedence"] == ["defaults", "file", "environment", "cli"]


def test_config_validate_returns_stable_error_without_traceback(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"schema_version":1,"daemon":{"idle_timeout_seconds":0}}')

    result = runner.invoke(app, ["config", "validate", str(path), "--json"])

    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["code"] == "LGD-CONFIG-007"
    assert "traceback" not in result.stdout.lower()


def test_config_validate_rejects_unknown_guard_setting(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"schema_version":1,"guard":{"trip_count_typo":3}}')

    result = runner.invoke(app, ["config", "validate", str(path), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "LGD-CONFIG-007"


def test_init_config_writes_the_current_layered_configuration_schema(tmp_path):
    path = tmp_path / "loopguard.json"

    initialized = runner.invoke(app, ["init-config", "--path", str(path)])
    validated = runner.invoke(app, ["config", "validate", str(path), "--json"])

    assert initialized.exit_code == 0
    assert validated.exit_code == 0
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == 1
    assert set(payload) == {"schema_version", "daemon", "guard", "telemetry"}


def test_explain_prints_local_error_reference_as_json():
    result = runner.invoke(app, ["explain", "LGD-DAEMON-001", "--json"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["code"] == "LGD-DAEMON-001"
    assert body["doc_url"].endswith("errors.md#lgd-daemon-001")


def test_generated_cli_reference_has_no_drift():
    reference = Path(__file__).resolve().parents[3] / "docs/reference/cli.md"

    rendered = render_cli_reference()
    assert reference.read_text() == rendered
    assert "Usage: loopguard" in rendered
    assert "terminate / continue / allowlist / inject" in rendered
    assert all(line == line.rstrip() for line in rendered.splitlines())
    for command in (
        "loopguard daemon status",
        "loopguard daemon doctor",
        "loopguard daemon install",
        "loopguard daemon uninstall",
        "loopguard init-config",
        "loopguard integrations install codex",
        "loopguard integrations install claude",
        "loopguard integrations verify codex",
        "loopguard integrations verify claude",
        "loopguard integrations uninstall codex",
        "loopguard integrations uninstall claude",
        "loopguard setup",
        "loopguard uninstall",
        "loopguard sessions",
        "loopguard data purge",
        "loopguard dx report",
        "loopguard feedback",
        "loopguard demo",
        "loopguard projects",
        "loopguard run",
        "loopguard inspect",
        "loopguard serve",
    ):
        assert f"## `{command}`" in rendered


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_state_paths_are_owner_only_and_pid_file_is_non_overwriting(tmp_path):
    home = tmp_path / "state"
    ensure_private_home(home)
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    pid = home / "loopguard.pid"
    write_pid_file(pid, 123)
    assert pid.read_text() == "123\n"
    assert stat.S_IMODE(pid.stat().st_mode) == 0o600
    with pytest.raises(UnsafeStatePathError):
        write_pid_file(pid, 456)
    remove_owned_pid_file(pid)
    assert not pid.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX daemon lifecycle")
def test_foreground_lifecycle_starts_and_cleans_socket_and_pid(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            home = tmp_path / "state"
            ensure_private_home(home)
            paths = ControlPaths(
                home=home,
                events_db=home / "events.db",
                socket=socket_path,
                pid=home / "loopguard.pid",
                config=home / "config.json",
                integration_trust=home / "integration-trust.json",
            )
            stop = asyncio.Event()
            ready = asyncio.Event()
            task = asyncio.create_task(
                run_foreground_daemon(
                    paths,
                    ControlConfiguration(),
                    on_ready=ready.set,
                    stop_event=stop,
                    store_factory=lambda: EventStore.for_test(paths.events_db),
                )
            )
            await asyncio.wait_for(ready.wait(), timeout=1)
            assert paths.socket.exists()
            assert paths.pid.exists()
            stop.set()
            await asyncio.wait_for(task, timeout=1)
            assert not paths.socket.exists()
            assert not paths.pid.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_foreground_start_returns_structured_unsafe_state_error(tmp_path):
    os.chmod(tmp_path, 0o755)

    result = runner.invoke(
        app,
        ["daemon", "start", "--foreground", "--home", str(tmp_path), "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "LGD-STATE-002"
    assert "traceback" not in result.stdout.lower()


@pytest.mark.skipif(os.name != "posix", reason="POSIX daemon storage")
def test_foreground_start_returns_structured_schema_error(tmp_path):
    import sqlite3

    database = tmp_path / "events.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 999")
    connection.close()
    database.chmod(0o600)

    result = runner.invoke(
        app,
        ["daemon", "start", "--foreground", "--home", str(tmp_path), "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "LGD-SCHEMA-003"
    assert not (tmp_path / "loopguard.pid").exists()
