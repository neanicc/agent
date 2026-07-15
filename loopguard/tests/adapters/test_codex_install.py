from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from loopguard.adapters.codex_hooks import (
    CODEX_EVENTS,
    CodexInstallError,
    find_codex_fallback_events,
    install_codex_hooks,
    install_codex_plugin,
    materialize_codex_marketplace,
    uninstall_codex_hooks,
    verify_codex_hooks,
    verify_codex_plugin,
)
from loopguard.cli import app


runner = CliRunner()


def _commands(body: dict[str, object]) -> list[str]:
    commands: list[str] = []
    for groups in body["hooks"].values():  # type: ignore[union-attr]
        for group in groups:
            commands.extend(handler["command"] for handler in group["hooks"])
    return commands


def test_codex_install_preserves_existing_hooks_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(
        json.dumps(
            {
                "future_setting": {"keep": True},
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "custom",
                            "future_group_field": 1,
                            "hooks": [
                                {"type": "command", "command": "existing", "future": "keep"}
                            ],
                        }
                    ]
                },
            }
        )
    )

    first = install_codex_hooks(path, executable="/usr/local/bin/loopguard")
    second = install_codex_hooks(path, executable="/usr/local/bin/loopguard")

    body = json.loads(path.read_text())
    commands = _commands(body)
    assert "existing" in commands
    assert sum("hook-entry codex" in command for command in commands) == len(CODEX_EVENTS) == 7
    assert body["future_setting"] == {"keep": True}
    assert body["hooks"]["Stop"][0]["future_group_field"] == 1
    assert first.changed is True
    assert second.changed is False
    assert first.mode == second.mode == "fallback"
    assert first.trust_required is True


def test_codex_project_install_uses_committed_git_root_wrapper(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    result = install_codex_hooks(
        repo / ".codex" / "hooks.json",
        scope="project",
        executable="loopguard",
        repository=repo / "nested",
    )

    wrapper = repo / ".loopguard" / "hooks" / "codex-hook"
    assert result.changed is True
    assert result.wrapper_path == wrapper
    assert wrapper.exists()
    assert "git rev-parse --show-toplevel" in wrapper.read_text()
    assert "LOOPGUARD_INGEST" not in wrapper.read_text()
    assert "git rev-parse --show-toplevel" in (repo / ".codex" / "hooks.json").read_text()
    if os.name == "posix":
        assert stat.S_IMODE(wrapper.stat().st_mode) == 0o755


def test_fallback_refuses_when_plugin_activation_is_available(tmp_path: Path) -> None:
    with pytest.raises(CodexInstallError, match="mutually exclusive"):
        install_codex_hooks(tmp_path / "hooks.json", plugin_available=True)

    assert not (tmp_path / "hooks.json").exists()


def test_project_fallback_never_overwrites_a_foreign_wrapper(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wrapper = repo / ".loopguard" / "hooks" / "codex-hook"
    (repo / ".git").mkdir(parents=True)
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\necho user-owned\n")

    with pytest.raises(CodexInstallError, match="non-LoopGuard"):
        install_codex_hooks(
            repo / ".codex" / "hooks.json",
            scope="project",
            repository=repo,
        )

    assert wrapper.read_text() == "#!/bin/sh\necho user-owned\n"
    assert not (repo / ".codex" / "hooks.json").exists()


def test_install_rejects_malformed_or_symlinked_settings_without_mutation(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json")
    before = malformed.read_bytes()
    with pytest.raises(CodexInstallError, match="valid JSON"):
        install_codex_hooks(malformed)
    assert malformed.read_bytes() == before

    target = tmp_path / "target.json"
    target.write_text("{}")
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    with pytest.raises(CodexInstallError, match="symlink"):
        install_codex_hooks(alias)
    assert target.read_text() == "{}"

    repo = tmp_path / "repo"
    settings = repo / ".codex" / "hooks.json"
    (repo / ".git").mkdir(parents=True)
    settings.parent.mkdir(parents=True)
    settings.write_text("{bad")
    with pytest.raises(CodexInstallError, match="valid JSON"):
        install_codex_hooks(settings, scope="project", repository=repo)
    assert not (repo / ".loopguard" / "hooks" / "codex-hook").exists()


def test_uninstall_removes_only_exact_loopguard_handlers(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    install_codex_hooks(path, executable="loopguard")
    body = json.loads(path.read_text())
    body["hooks"]["Stop"][0]["hooks"].append(
        {"type": "command", "command": "loopguard-other hook-entry codex Stop"}
    )
    body["hooks"]["Stop"].append(
        {"hooks": [{"type": "command", "command": "existing"}]}
    )
    path.write_text(json.dumps(body))

    result = uninstall_codex_hooks(path, executable="loopguard")
    remaining = _commands(json.loads(path.read_text()))

    assert result.changed is True
    assert "loopguard-other hook-entry codex Stop" in remaining
    assert "existing" in remaining
    assert not any(command.startswith("loopguard hook-entry codex") for command in remaining)
    assert find_codex_fallback_events(path) == ()


def test_materialized_marketplace_is_pinned_and_plugin_relative(tmp_path: Path) -> None:
    result = materialize_codex_marketplace(tmp_path)
    manifest = json.loads(
        (result.plugin_path / ".codex-plugin" / "plugin.json").read_text()
    )
    hooks = json.loads((result.plugin_path / "hooks" / "hooks.json").read_text())
    marketplace = json.loads(
        (result.marketplace_path / ".agents" / "plugins" / "marketplace.json").read_text()
    )

    assert (result.marketplace_path / "loopguard.checksum").read_text() == (
        f"sha256:{result.checksum}\n"
    )
    assert manifest["name"] == result.plugin_path.name == "codex-plugin"
    assert "skills" not in manifest and "hooks" not in manifest
    assert marketplace["name"] == "loopguard-local"
    assert marketplace["plugins"][0]["source"]["path"] == "./plugins/codex-plugin"
    assert tuple(hooks["hooks"]) == CODEX_EVENTS
    for event, groups in hooks["hooks"].items():
        command = groups[0]["hooks"][0]["command"]
        assert command == f"${{PLUGIN_ROOT}}/bin/loopguard-hook {event}"
    assert "FileChanged" not in hooks["hooks"]
    assert "PostToolUseFailure" not in hooks["hooks"]
    checked_in = Path(__file__).resolve().parents[2] / "integrations" / "codex-plugin"
    for relative in (
        ".codex-plugin/plugin.json",
        "hooks/hooks.json",
        "bin/loopguard-hook",
    ):
        assert (checked_in / relative).read_bytes() == (result.plugin_path / relative).read_bytes()


def test_dry_run_reports_changes_without_writing(tmp_path: Path) -> None:
    settings = tmp_path / "hooks.json"
    marketplace_home = tmp_path / "marketplaces"

    fallback = install_codex_hooks(settings, dry_run=True)
    marketplace = materialize_codex_marketplace(marketplace_home, dry_run=True)

    assert fallback.changed is True
    assert fallback.status == "preview"
    assert not settings.exists()
    assert list(tmp_path.glob(".*.loopguard.lock")) == []
    assert marketplace.changed is True
    assert not marketplace.marketplace_path.exists()


def test_verify_requires_codex_reported_trust_for_every_exact_hook(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    install_codex_hooks(path, executable="loopguard")
    commands = {
        event: body[0]["hooks"][0]["command"]
        for event, body in json.loads(path.read_text())["hooks"].items()
    }
    report = [
        {
            "eventName": _wire_event(event),
            "command": command,
            "sourcePath": str(path),
            "enabled": True,
            "trustStatus": "untrusted",
            "currentHash": f"sha256:{index:064x}",
        }
        for index, (event, command) in enumerate(commands.items(), start=1)
    ]

    untrusted = verify_codex_hooks(path, executable="loopguard", hook_report=report)
    trusted_report = [dict(hook, trustStatus="trusted") for hook in report]
    trusted = verify_codex_hooks(path, executable="loopguard", hook_report=trusted_report)

    assert untrusted.installed is True
    assert untrusted.healthy is False
    assert untrusted.status == "trust_required"
    assert trusted.healthy is True
    assert trusted.status == "active"
    assert len(trusted.hook_hashes) == len(CODEX_EVENTS)


def test_plugin_install_is_idempotent_and_verification_checks_exact_package(tmp_path: Path) -> None:
    marketplace = materialize_codex_marketplace(tmp_path)
    payload = {
        "installed": [
            {
                "pluginId": "codex-plugin@loopguard-local",
                "version": "0.1.0",
                "installed": True,
                "enabled": True,
                "source": {"path": str(marketplace.plugin_path)},
            }
        ]
    }
    calls: list[list[str]] = []

    def fake_runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    repeated = install_codex_plugin(tmp_path, command_runner=fake_runner)
    hooks = json.loads((marketplace.plugin_path / "hooks" / "hooks.json").read_text())["hooks"]
    report = [
        {
            "eventName": _wire_event(event),
            "command": groups[0]["hooks"][0]["command"].replace(
                "${PLUGIN_ROOT}", str(marketplace.plugin_path)
            ),
            "sourcePath": str(marketplace.plugin_path / "hooks" / "hooks.json"),
            "pluginId": "codex-plugin@loopguard-local",
            "enabled": True,
            "trustStatus": "untrusted",
            "currentHash": f"sha256:{index:064x}",
        }
        for index, (event, groups) in enumerate(hooks.items(), start=1)
    ]
    untrusted = verify_codex_plugin(command_runner=fake_runner, hook_report=report)
    trusted = verify_codex_plugin(
        command_runner=fake_runner,
        hook_report=[dict(hook, trustStatus="trusted") for hook in report],
    )

    assert repeated.changed is False
    assert all(command[1:4] == ["plugin", "list", "--json"] for command in calls)
    assert untrusted.status == "trust_required"
    assert trusted.healthy is True


def _wire_event(event: str) -> str:
    return event[:1].lower() + event[1:]


def test_integrations_cli_exposes_fallback_lifecycle_without_real_codex_state(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"

    installed = runner.invoke(
        app,
        [
            "integrations",
            "install",
            "codex",
            "--fallback",
            "--settings",
            str(path),
            "--codex-version",
            "0.0.0",
            "--json",
        ],
    )
    verified = runner.invoke(
        app,
        ["integrations", "verify", "codex", "--settings", str(path), "--json"],
    )
    uninstalled = runner.invoke(
        app,
        ["integrations", "uninstall", "codex", "--settings", str(path), "--json"],
    )

    assert installed.exit_code == 0, installed.stdout
    assert json.loads(installed.stdout)["status"] == "trust_required"
    assert verified.exit_code == 1
    assert json.loads(verified.stdout)["installed"] is True
    assert uninstalled.exit_code == 0
    assert json.loads(uninstalled.stdout)["changed"] is True
