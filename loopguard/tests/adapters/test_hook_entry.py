from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopguard.adapters.hook_client import HookClient, HookClientError
from loopguard.adapters.hook_entry import process_hook, process_hook_input
from loopguard.adapters.normalize_hook import (
    CODEX_PRE_TOOL_COVERAGE,
    HookNormalizationError,
    RepositoryIdentity,
    normalize_hook,
)
from loopguard.control.daemon import LoopGuardDaemon
from loopguard.control.decisions import ActionRequest, ActionTarget, PolicyDecision, TargetKind
from loopguard.control.events import EventKind
from loopguard.control.paths import ControlPaths
from loopguard.control.store import EventStore

from tests.control.daemon_test_support import short_socket_path


def test_codex_tool_hook_normalizes_untrusted_input_and_redacts_before_send(tmp_path):
    repository = _make_git_repo(tmp_path)
    client = _DecisionClient("allow")

    result = process_hook(
        vendor="codex",
        hook_name="PreToolUse",
        raw={
            "session_id": "vendor-session",
            "turn_id": "vendor-turn",
            "cwd": str(repository),
            "tool_name": "Bash",
            "tool_use_id": "tool-1",
            "tool_input": {
                "command": "pytest",
                "Authorization": "Bearer must-not-leak",
            },
        },
        client=client,
    )

    assert result.exit_code == 0
    assert result.blocked is False
    event = client.events[0]
    assert event.kind is EventKind.TOOL_CALL
    assert event.payload["tool_name"] == "Bash"
    assert event.payload["arguments"]["command"] == "pytest"
    assert event.payload["arguments"]["Authorization"] == "[REDACTED]"
    assert event.payload["coverage"] == CODEX_PRE_TOOL_COVERAGE
    assert event.session.session_id != "vendor-session"
    assert event.session.repo_id != str(repository)
    assert "must-not-leak" not in event.model_dump_json()


def test_hook_client_uses_a_bounded_trusted_local_transport_configuration(tmp_path):
    paths = ControlPaths.from_home(tmp_path)
    client = HookClient(paths=paths)

    assert client.paths is paths
    assert client.timeout_seconds == 0.1
    with pytest.raises(ValueError, match="no greater than one second"):
        HookClient(paths=paths, timeout_seconds=1.01)
    with pytest.raises(ValueError, match="either trusted control paths"):
        HookClient(paths=paths, home=tmp_path)


def test_stop_is_turn_completion_and_never_claims_session_termination(tmp_path):
    repository = _make_git_repo(tmp_path)

    normalized = normalize_hook(
        "claude",
        "Stop",
        {
            "session_id": "vendor-session",
            "cwd": str(repository),
            "stop_hook_active": False,
        },
    )

    assert normalized.event.kind is EventKind.TURN_COMPLETED
    assert normalized.event.kind is not EventKind.SESSION_STOPPED


@pytest.mark.parametrize("vendor", ["codex", "claude"])
def test_pre_compact_is_normalized_without_claiming_turn_completion(tmp_path, vendor):
    repository = _make_git_repo(tmp_path)

    normalized = normalize_hook(
        vendor,
        "PreCompact",
        {
            "session_id": "vendor-session",
            "cwd": str(repository),
            "trigger": "auto",
        },
    )

    assert normalized.event.kind is EventKind.CONTEXT_COMPACTED
    assert normalized.event.kind is not EventKind.TURN_COMPLETED


def test_claude_remote_signal_only_labels_execution_environment(tmp_path, monkeypatch):
    repository = _make_git_repo(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")

    normalized = normalize_hook(
        "claude",
        "SessionStart",
        {"session_id": "vendor-session", "cwd": str(repository)},
    )

    assert normalized.event.payload["execution_environment"] == "cloud"


def test_permission_request_is_a_typed_action_request_not_a_tool_call(tmp_path):
    repository = _make_git_repo(tmp_path)

    normalized = normalize_hook(
        "codex",
        "PermissionRequest",
        {
            "session_id": "vendor-session",
            "turn_id": "turn-1",
            "cwd": str(repository),
            "tool_name": "Bash",
            "tool_input": {"command": "git push", "description": "network access"},
        },
        now=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert normalized.event.kind is EventKind.ACTION_REQUESTED
    assert normalized.action_request is not None
    assert isinstance(normalized.action_request, ActionRequest)
    assert normalized.event.payload["action_request"]["action_id"] == (
        normalized.action_request.action_id
    )
    assert "tool.call" not in normalized.event.model_dump_json()


def test_codex_rejects_hook_events_it_does_not_officially_expose(tmp_path):
    repository = _make_git_repo(tmp_path)

    with pytest.raises(HookNormalizationError, match="unsupported_hook"):
        normalize_hook(
            "codex",
            "FileChanged",
            {"session_id": "s", "cwd": str(repository), "file_path": "x"},
        )


@pytest.mark.parametrize("hook_name", ["PostToolUse", "PostToolUseFailure", "FileChanged"])
def test_observation_hooks_fail_open_when_daemon_is_down(tmp_path, hook_name):
    repository = _make_git_repo(tmp_path)
    result = process_hook(
        vendor="claude",
        hook_name=hook_name,
        raw=_raw_for(repository, hook_name),
        client=_FailingClient(HookClientError("daemon_unreachable")),
        fail_closed=True,
    )

    assert result.exit_code == 0
    assert result.blocked is False
    assert result.stdout == ""
    assert result.warning == "daemon_unreachable"


def test_pre_tool_daemon_timeout_blocks_only_under_explicit_fail_closed_policy(tmp_path):
    repository = _make_git_repo(tmp_path)
    raw = _raw_for(repository, "PreToolUse")
    client = _FailingClient(HookClientError("daemon_timeout", retryable=True))

    open_result = process_hook(
        vendor="claude",
        hook_name="PreToolUse",
        raw=raw,
        client=client,
        fail_closed=False,
    )
    closed_result = process_hook(
        vendor="claude",
        hook_name="PreToolUse",
        raw=raw,
        client=client,
        fail_closed=True,
    )

    assert open_result.blocked is False
    assert open_result.stdout == ""
    assert closed_result.blocked is True
    assert json.loads(closed_result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_request_approval_is_visible_but_never_rendered_as_unsupported_ask(tmp_path):
    repository = _make_git_repo(tmp_path)

    result = process_hook(
        vendor="codex",
        hook_name="PreToolUse",
        raw=_raw_for(repository, "PreToolUse"),
        client=_DecisionClient("request_approval"),
    )

    assert result.exit_code == 0
    assert result.blocked is False
    assert "ask" not in result.stdout
    assert "approval" in json.loads(result.stdout)["systemMessage"].lower()
    assert result.warning == "capability_unavailable:request_approval"


def test_pause_decision_uses_the_vendor_supported_block_shape(tmp_path):
    repository = _make_git_repo(tmp_path)

    result = process_hook(
        vendor="codex",
        hook_name="PreToolUse",
        raw=_raw_for(repository, "PreToolUse"),
        client=_DecisionClient("pause"),
    )

    body = json.loads(result.stdout)
    assert result.blocked is True
    assert body["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_attached_pre_tool_hook_never_claims_tool_denial_is_a_session_interrupt(tmp_path):
    repository = _make_git_repo(tmp_path)

    result = process_hook(
        vendor="codex",
        hook_name="PreToolUse",
        raw=_raw_for(repository, "PreToolUse"),
        client=_DecisionClient("interrupt"),
    )

    assert result.blocked is False
    assert result.warning == "capability_unavailable:interrupt"
    assert "permissionDecision" not in result.stdout


def test_permission_request_never_turns_missing_stale_or_duplicate_decision_into_approval(tmp_path):
    repository = _make_git_repo(tmp_path)
    raw = {
        "session_id": "session",
        "turn_id": "turn",
        "cwd": str(repository),
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
    }
    client = _DecisionClient("allow")
    stale_client = _ApprovalDecisionClient(stale=True)

    first = process_hook("codex", "PermissionRequest", raw, client=client)
    duplicate = process_hook("codex", "PermissionRequest", raw, client=client)
    stale = process_hook("codex", "PermissionRequest", raw, client=stale_client)
    timeout = process_hook(
        "codex",
        "PermissionRequest",
        raw,
        client=_FailingClient(HookClientError("daemon_timeout", retryable=True)),
    )

    assert first.stdout == duplicate.stdout == stale.stdout == timeout.stdout == ""
    assert first.blocked is duplicate.blocked is stale.blocked is timeout.blocked is False
    assert client.events[0].event_id == client.events[1].event_id
    assert stale.warning == "stale_approval_decision"
    assert timeout.warning == "daemon_timeout"


@pytest.mark.parametrize(("behavior", "blocked"), [("allow", False), ("deny", True)])
def test_exact_correlated_permission_resolution_uses_supported_vendor_shape(
    tmp_path,
    behavior,
    blocked,
):
    repository = _make_git_repo(tmp_path)
    raw = {
        "session_id": "session",
        "turn_id": "turn",
        "cwd": str(repository),
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
    }

    result = process_hook(
        "codex",
        "PermissionRequest",
        raw,
        client=_ApprovalDecisionClient(stale=False, behavior=behavior),
    )

    decision = json.loads(result.stdout)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == behavior
    assert result.blocked is blocked


def test_malformed_utf8_json_and_oversized_input_fail_open_without_contacting_daemon(tmp_path):
    client = _DecisionClient("allow")
    malformed_utf8 = process_hook_input("claude", "PostToolUse", b"\xff", client=client)
    malformed_json = process_hook_input("claude", "PostToolUse", b"{", client=client)
    oversized = process_hook_input(
        "claude",
        "PostToolUse",
        b"{" + (b" " * 262_145) + b"}",
        client=client,
    )

    assert {malformed_utf8.warning, malformed_json.warning, oversized.warning} == {
        "invalid_utf8",
        "invalid_json",
        "input_too_large",
    }
    assert all(result.exit_code == 0 for result in (malformed_utf8, malformed_json, oversized))
    assert client.events == []


@pytest.mark.skipif(os.name != "posix", reason="symlink behavior is POSIX-specific")
def test_symlinked_working_directory_and_deleted_repository_fail_safely(tmp_path):
    repository = _make_git_repo(tmp_path / "real")
    (repository / "nested").mkdir()
    link = tmp_path / "link"
    link.symlink_to(repository, target_is_directory=True)

    with pytest.raises(HookNormalizationError, match="symlinked_cwd"):
        normalize_hook("claude", "SessionStart", {"session_id": "s", "cwd": str(link)})
    with pytest.raises(HookNormalizationError, match="symlinked_cwd"):
        normalize_hook(
            "claude",
            "SessionStart",
            {"session_id": "s", "cwd": str(link / "nested")},
        )
    with pytest.raises(HookNormalizationError, match="invalid_cwd"):
        normalize_hook("claude", "SessionStart", {"session_id": "s", "cwd": "."})
    normalize_hook("claude", "SessionStart", {"session_id": "s", "cwd": str(repository)})
    (repository / ".git").rename(repository / ".git-removed")
    with pytest.raises(HookNormalizationError, match="repository_unavailable"):
        normalize_hook(
            "claude",
            "SessionStart",
            {"session_id": "s", "cwd": str(repository)},
        )


def test_unknown_daemon_decision_fails_open_with_visible_warning(tmp_path):
    repository = _make_git_repo(tmp_path)

    result = process_hook(
        "codex",
        "PreToolUse",
        _raw_for(repository, "PreToolUse"),
        client=_UnknownDecisionClient(),
    )

    assert result.exit_code == 0
    assert result.blocked is False
    assert result.warning == "invalid_decision"
    assert "invalid" in json.loads(result.stdout)["systemMessage"].lower()


def test_hook_output_is_bounded_and_redacted(tmp_path):
    repository = _make_git_repo(tmp_path)
    client = _DecisionClient("warn")
    client.reason = "Authorization: Bearer must-not-leak " + ("x" * 100_000)

    result = process_hook(
        "codex",
        "PreToolUse",
        _raw_for(repository, "PreToolUse"),
        client=client,
    )

    assert len(result.stdout.encode("utf-8")) <= 16_384
    assert "must-not-leak" not in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="real daemon uses a Unix socket")
def test_repeated_hook_tool_event_reaches_real_daemon_and_existing_detector(tmp_path):
    async def scenario():
        repository = _make_git_repo(tmp_path / "repo")
        with short_socket_path() as socket_path:
            paths = ControlPaths(
                home=tmp_path,
                events_db=tmp_path / "events.db",
                socket=socket_path,
                pid=tmp_path / "loopguard.pid",
                config=tmp_path / "config.json",
                integration_trust=tmp_path / "integration-trust.json",
            )
            store = EventStore.for_test(paths.events_db)
            first_daemon = LoopGuardDaemon(store=store, socket_path=paths.socket)
            await first_daemon.start()
            client = HookClient(paths=paths)
            try:
                results = []
                for index in range(2):
                    raw = _raw_for(repository, "PreToolUse")
                    raw["tool_use_id"] = f"tool-{index}"
                    results.append(
                        await asyncio.to_thread(
                            process_hook,
                            "codex",
                            "PreToolUse",
                            raw,
                            client=client,
                        )
                    )
            finally:
                await first_daemon.close()

            restarted_daemon = LoopGuardDaemon(store=store, socket_path=paths.socket)
            await restarted_daemon.start()
            try:
                raw = _raw_for(repository, "PreToolUse")
                raw["tool_use_id"] = "tool-2"
                results.append(
                    await asyncio.to_thread(
                        process_hook,
                        "codex",
                        "PreToolUse",
                        raw,
                        client=client,
                    )
                )
                assert results[-1].warning == "capability_unavailable:request_approval"
                assert store.count() == 3
            finally:
                await restarted_daemon.close()
                store.close()

    asyncio.run(scenario())


def test_fake_client_processing_p95_is_below_twenty_milliseconds(tmp_path):
    identity = RepositoryIdentity(
        root=tmp_path,
        repo_id="repo",
        worktree_id="worktree",
    )
    client = _DecisionClient("allow")
    durations = []
    for index in range(100):
        raw = _raw_for(tmp_path, "PreToolUse")
        raw["tool_use_id"] = f"tool-{index}"
        started = time.perf_counter()
        result = process_hook(
            "codex",
            "PreToolUse",
            raw,
            client=client,
            identity_resolver=lambda _cwd: identity,
            host_id="host",
        )
        durations.append((time.perf_counter() - started) * 1_000)
        assert result.exit_code == 0

    assert sorted(durations)[94] < 20


class _DecisionClient:
    def __init__(self, action: str):
        self.action = action
        self.reason = "test decision"
        self.events = []

    def send(self, event):
        self.events.append(event)
        return PolicyDecision(
            decision_id=f"decision-{len(self.events)}",
            action=self.action,
            reason=self.reason,
            target=ActionTarget(kind=TargetKind.SESSION, target_id=event.session.session_id),
            state_version=len(self.events),
            state_hash=f"state-{len(self.events)}",
        )


class _FailingClient:
    def __init__(self, error):
        self.error = error

    def send(self, event):
        raise self.error


class _UnknownDecisionClient:
    def send(self, event):
        return {
            "schema_version": 1,
            "decision_id": "decision-invalid",
            "action": "invented-control",
            "reason": "invalid",
            "target": {"kind": "session", "target_id": event.session.session_id},
            "state_version": 1,
            "state_hash": "state",
            "metadata": {},
        }


class _ApprovalDecisionClient:
    def __init__(self, *, stale: bool, behavior: str = "allow"):
        self.stale = stale
        self.behavior = behavior

    def send(self, event):
        request = event.payload["action_request"]
        action_id = "stale-action" if self.stale else request["action_id"]
        return PolicyDecision(
            decision_id="approval-decision",
            action="allow" if self.behavior == "allow" else "interrupt",
            reason="approval",
            target=ActionTarget(kind=TargetKind.SESSION, target_id=event.session.session_id),
            state_version=1,
            state_hash="state",
            metadata={
                "approval": {
                    "action_id": action_id,
                    "nonce": request["nonce"],
                    "expected_state_version": request["expected_state_version"],
                    "expected_state_hash": request["expected_state_hash"],
                    "behavior": self.behavior,
                }
            },
        )


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return path.resolve()


def _raw_for(repository: Path, hook_name: str) -> dict:
    raw = {
        "session_id": "vendor-session",
        "turn_id": "vendor-turn",
        "cwd": str(repository),
        "hook_event_name": hook_name,
    }
    if hook_name in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
        raw.update(
            {
                "tool_name": "Bash",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "pytest"},
            }
        )
    if hook_name == "PostToolUse":
        raw["tool_response"] = {"stdout": "ok"}
    if hook_name == "PostToolUseFailure":
        raw["error"] = "failed"
    if hook_name == "FileChanged":
        raw.update({"file_path": str(repository / "file.py"), "event": "change"})
    return raw
