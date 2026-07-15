from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from loopguard.adapters.cloud_hook_client import CloudHookClient, FallbackHookClient
from loopguard.adapters.hook_client import HookClientError
from loopguard.control.decisions import ActionTarget, PolicyDecision, TargetKind
from loopguard.control.events import ControlEvent, EventKind, SessionRef

from loopguard.adapters.claude_hooks import (
    CLAUDE_EVENTS,
    ClaudeInstallError,
    find_claude_fallback_events,
    install_claude_hooks,
    install_claude_plugin,
    materialize_claude_marketplace,
    uninstall_claude_hooks,
    verify_claude_hooks,
    verify_claude_plugin,
)
from loopguard.cli import app


runner = CliRunner()


def _commands(body: dict[str, object]) -> list[str]:
    values: list[str] = []
    for groups in body["hooks"].values():  # type: ignore[union-attr]
        for group in groups:
            for handler in group["hooks"]:
                values.append(" ".join([handler["command"], *handler.get("args", [])]))
    return values


def test_claude_project_install_uses_project_relative_entry_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    (repo / ".git").mkdir(parents=True)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"future": True, "hooks": {"Stop": []}}))

    first = install_claude_hooks(settings, scope="project", repository=repo)
    second = install_claude_hooks(settings, scope="project", repository=repo)
    text = settings.read_text()
    body = json.loads(text)

    assert first.changed is True
    assert second.changed is False
    assert body["future"] is True
    assert "${CLAUDE_PROJECT_DIR}" in text
    assert set(body["hooks"]) == set(CLAUDE_EVENTS)
    assert "FileChanged" in text
    assert "PostToolUseFailure" in text
    assert "PermissionRequest" in text
    assert (repo / ".loopguard" / "hooks" / "claude-hook").exists()


def test_claude_fallback_refuses_plugin_overlap_and_foreign_wrapper(tmp_path: Path) -> None:
    with pytest.raises(ClaudeInstallError, match="mutually exclusive"):
        install_claude_hooks(tmp_path / "settings.json", plugin_available=True)

    repo = tmp_path / "repo"
    wrapper = repo / ".loopguard" / "hooks" / "claude-hook"
    (repo / ".git").mkdir(parents=True)
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("user-owned")
    with pytest.raises(ClaudeInstallError, match="non-LoopGuard"):
        install_claude_hooks(
            repo / ".claude" / "settings.json",
            scope="project",
            repository=repo,
        )
    assert wrapper.read_text() == "user-owned"


def test_claude_uninstall_removes_only_exact_handlers(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    install_claude_hooks(path, executable="loopguard")
    body = json.loads(path.read_text())
    body["hooks"]["Stop"][0]["hooks"].append(
        {"type": "command", "command": "existing", "args": []}
    )
    path.write_text(json.dumps(body))

    removed = uninstall_claude_hooks(path, executable="loopguard")
    commands = _commands(json.loads(path.read_text()))

    assert removed.changed is True
    assert "existing" in commands
    assert find_claude_fallback_events(path) == ()


def test_claude_fallback_verification_requires_every_exact_handler(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    install_claude_hooks(path)

    healthy = verify_claude_hooks(path)
    body = json.loads(path.read_text())
    body["hooks"]["Stop"] = []
    path.write_text(json.dumps(body))
    incomplete = verify_claude_hooks(path)

    assert healthy.installed is True
    assert healthy.healthy is True
    assert healthy.status == "active"
    assert incomplete.installed is True
    assert incomplete.healthy is False
    assert incomplete.status == "incomplete"


def test_claude_marketplace_plugin_uses_exec_form_and_literal_file_watch(tmp_path: Path) -> None:
    result = materialize_claude_marketplace(tmp_path)
    hooks = json.loads((result.plugin_path / "hooks" / "hooks.json").read_text())["hooks"]
    marketplace = json.loads(
        (result.marketplace_path / ".claude-plugin" / "marketplace.json").read_text()
    )

    assert tuple(hooks) == CLAUDE_EVENTS
    assert marketplace["plugins"][0]["source"] == "./plugins/loopguard"
    for event, groups in hooks.items():
        handler = groups[0]["hooks"][0]
        assert handler["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/loopguard-hook"
        assert handler["args"] == [event]
    assert hooks["FileChanged"][0]["matcher"] == ".env|.envrc|CLAUDE.md"
    checked_in = Path(__file__).resolve().parents[2] / "integrations" / "claude-plugin"
    for relative in (
        ".claude-plugin/plugin.json",
        "hooks/hooks.json",
    ):
        assert json.loads((checked_in / relative).read_text()) == json.loads(
            (result.plugin_path / relative).read_text()
        )
    assert (checked_in / "bin/loopguard-hook").read_bytes() == (
        result.plugin_path / "bin/loopguard-hook"
    ).read_bytes()


def test_claude_plugin_install_and_verify_are_idempotent_and_policy_aware(tmp_path: Path) -> None:
    marketplace = materialize_claude_marketplace(tmp_path)
    installed_payload = [
        {
            "id": "loopguard@loopguard-local",
            "version": "0.1.0",
            "enabled": True,
            "scope": "user",
            "installPath": str(marketplace.plugin_path),
        }
    ]
    calls: list[list[str]] = []

    def fake_runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(installed_payload), stderr="")

    repeated = install_claude_plugin(tmp_path, command_runner=fake_runner)
    active = verify_claude_plugin(command_runner=fake_runner)
    policy = tmp_path / "managed-settings.json"
    policy.write_text(json.dumps({"allowManagedHooksOnly": True}))
    blocked = verify_claude_plugin(
        command_runner=fake_runner,
        managed_settings_paths=(policy,),
    )

    assert repeated.changed is False
    assert active.healthy is True
    assert blocked.healthy is False
    assert blocked.status == "unavailable"
    assert blocked.policy_source == policy
    assert all(command[1:4] == ["plugin", "list", "--json"] for command in calls)


def test_claude_committed_files_never_contain_cloud_credentials(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    install_claude_hooks(
        repo / ".claude" / "settings.json",
        scope="project",
        repository=repo,
    )

    committed = (
        (repo / ".claude" / "settings.json").read_text()
        + (repo / ".loopguard" / "hooks" / "claude-hook").read_text()
    )
    assert "LOOPGUARD_HOOK_SECRET=" not in committed
    assert "LOOPGUARD_HOOK_KEY_ID=" not in committed
    assert "CLAUDE_CODE_REMOTE" not in committed
    if os.name == "posix":
        assert os.access(repo / ".loopguard" / "hooks" / "claude-hook", os.X_OK)


def test_cloud_hook_client_requires_https_and_signs_canonical_request() -> None:
    secret = b"s" * 32
    captured = {}
    event = ControlEvent(
        event_id="evt-1",
        kind=EventKind.TOOL_CALL,
        source="claude",
        session=SessionRef(
            host_id="host",
            repo_id="repo",
            worktree_id="worktree",
            session_id="session",
        ),
        created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        payload={"tool_name": "Bash"},
    )
    decision = PolicyDecision(
        decision_id="decision-1",
        action="allow",
        reason="ok",
        target=ActionTarget(kind=TargetKind.SESSION, target_id="session"),
        state_version=1,
        state_hash="hash",
    )

    class Response:
        status = 200

        def read(self, _limit):
            return decision.model_dump_json().encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    client = CloudHookClient(
        url="https://ingest.example/v1/hook-events",
        key_id="repo-key",
        secret_b64=base64.urlsafe_b64encode(secret).decode(),
        opener=Opener(),
        clock=lambda: 1_783_742_400,
        nonce_factory=lambda: "nonce-1",
    )
    assert client.send(event).action == "allow"

    request = captured["request"]
    body_hash = hashlib.sha256(request.data).hexdigest()
    canonical = f"POST\n/v1/hook-events\n1783742400\nnonce-1\n{body_hash}"
    expected = hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-loopguard-signature"] == f"v1={expected}"
    assert headers["x-loopguard-key-id"] == "repo-key"
    assert captured["timeout"] <= 0.5
    assert secret not in request.data

    with pytest.raises(ValueError, match="HTTPS"):
        CloudHookClient(
            url="http://ingest.example/v1/hook-events",
            key_id="key",
            secret_b64=base64.urlsafe_b64encode(secret).decode(),
        )


def test_cloud_fallback_is_used_only_after_local_transport_failure() -> None:
    decision = PolicyDecision(
        decision_id="decision-1",
        action="allow",
        reason="ok",
        target=ActionTarget(kind=TargetKind.SESSION, target_id="session"),
        state_version=1,
        state_hash="hash",
    )

    class Local:
        def send(self, _event):
            raise HookClientError("daemon_unreachable")

    class Cloud:
        def __init__(self):
            self.calls = 0

        def send(self, _event):
            self.calls += 1
            return decision

    cloud = Cloud()
    client = FallbackHookClient(local=Local(), cloud=cloud)
    assert client.send(object()).action == "allow"
    assert cloud.calls == 1


def test_integrations_cli_exposes_claude_fallback_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"

    installed = runner.invoke(
        app,
        [
            "integrations",
            "install",
            "claude",
            "--fallback",
            "--settings",
            str(path),
            "--claude-version",
            "0.0.0",
            "--json",
        ],
    )
    verified = runner.invoke(
        app,
        ["integrations", "verify", "claude", "--settings", str(path), "--json"],
    )
    uninstalled = runner.invoke(
        app,
        ["integrations", "uninstall", "claude", "--settings", str(path), "--json"],
    )

    assert installed.exit_code == 0, installed.stdout
    assert json.loads(installed.stdout)["status"] == "active"
    assert verified.exit_code == 0, verified.stdout
    assert json.loads(verified.stdout)["healthy"] is True
    assert uninstalled.exit_code == 0, uninstalled.stdout
    assert json.loads(uninstalled.stdout)["changed"] is True
