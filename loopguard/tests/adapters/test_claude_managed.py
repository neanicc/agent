from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopguard.adapters.base import (
    Capability,
    LifecycleError,
    LifecycleErrorCode,
    ManagedRunRequest,
)
from loopguard.adapters.claude_managed import (
    ClaudeAuthentication,
    ClaudeManagedAdapter,
    ManagedStartEvidence,
    detect_claude_authentication,
)
from loopguard.control.decisions import ActionKind
from loopguard.control.events import EventKind


class FakeClaudeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.handler = None
        self.closed = False

    def set_message_handler(self, handler) -> None:
        self.handler = handler

    async def request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any:
        self.calls.append((method, dict(params)))
        if method == "start":
            return {
                "protocolVersion": 1,
                "sessionId": "claude-session-1",
                "models": [
                    {
                        "value": "claude-test",
                        "resolvedModel": "claude-test-20260701",
                        "supportsEffort": True,
                        "supportedEffortLevels": ["low", "medium", "high"],
                    }
                ],
                "authProvider": "anthropic-api",
            }
        if method in {"interrupt", "inject", "resolve_permission"}:
            return {"protocolVersion": 1, "accepted": True}
        if method == "close":
            return {"protocolVersion": 1, "closed": True}
        raise AssertionError(f"unexpected bridge method: {method}")

    async def emit(self, event: dict[str, Any]) -> None:
        assert self.handler is not None
        await self.handler(
            {
                "method": "event",
                "params": {
                    "protocolVersion": 1,
                    "sessionId": "claude-session-1",
                    "event": event,
                },
            }
        )

    async def close(self) -> None:
        self.closed = True


def _request(tmp_path: Path, **updates: Any) -> ManagedRunRequest:
    values: dict[str, Any] = {
        "repository_id": "repo-1",
        "repository_root": tmp_path / "repo",
        "worktree_id": "worktree-1",
        "worktree_root": tmp_path / "worktree",
        "prompt": "Fix the verified regression.",
        "model": "claude-test",
        "effort": "medium",
        "sandbox": "workspace-write",
        "permission_policy": "on-request",
        "proof_contract_id": "proof-1",
        "max_tokens": 20_000,
        "max_cost_usd": 2.5,
    }
    values.update(updates)
    values["repository_root"].mkdir(exist_ok=True)
    values["worktree_root"].mkdir(exist_ok=True)
    return ManagedRunRequest.model_validate(values)


def _evidence(request: ManagedRunRequest) -> ManagedStartEvidence:
    return ManagedStartEvidence(
        proof_contract_id=request.proof_contract_id,
        baseline_id="baseline-1",
        worktree_lease_id="lease-1",
        worktree_id=request.worktree_id,
        worktree_root=request.worktree_root,
        captured_before_first_mutation=True,
    )


def _adapter(bridge: FakeClaudeBridge, tmp_path: Path) -> ClaudeManagedAdapter:
    return ClaudeManagedAdapter(
        bridge=bridge,
        authorize_start=_evidence,
        authentication=ClaudeAuthentication(provider="anthropic-api", source="environment"),
        state_path=tmp_path / "claude-managed.sqlite3",
    )


def test_managed_claude_maps_sdk_events_and_declares_capabilities(tmp_path: Path) -> None:
    async def scenario():
        bridge = FakeClaudeBridge()
        adapter = _adapter(bridge, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await bridge.emit(
            {
                "kind": "tool.call",
                "eventId": "evt-tool-1",
                "turnId": "turn-1",
                "payload": {"toolName": "Bash", "input": {"command": "pytest"}},
            }
        )
        started = await anext(adapter.events(session))
        tool = await anext(adapter.events(session))
        return bridge, adapter, session, started, tool

    bridge, adapter, session, started, tool = asyncio.run(scenario())

    assert bridge.calls[0][0] == "start"
    assert bridge.calls[0][1]["cwd"].endswith("worktree")
    assert bridge.calls[0][1]["permissionMode"] == "default"
    assert bridge.calls[0][1]["sandboxPolicy"] == "workspace-write"
    assert bridge.calls[0][1]["maxBudgetUsd"] == 2.5
    assert started.kind is EventKind.SESSION_STARTED
    assert tool.kind is EventKind.TOOL_CALL
    assert tool.session.session_id == session.session_id == "claude-session-1"
    assert adapter.capabilities.supports(Capability.SELECT_EFFORT)
    assert adapter.capabilities.supports(Capability.APPROVE_TOOL)


def test_inject_is_only_between_turns_and_interrupt_targets_owned_session(tmp_path: Path) -> None:
    async def scenario():
        bridge = FakeClaudeBridge()
        adapter = _adapter(bridge, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        stale = await adapter.inject(session, "too early")
        interrupted = await adapter.interrupt(session)
        await bridge.emit(
            {
                "kind": "turn.completed",
                "eventId": "evt-result-1",
                "turnId": "turn-1",
                "payload": {
                    "usage": {"input_tokens": 20, "output_tokens": 5},
                    "reportedCostUsd": 0.01,
                    "costSource": "sdk_reported",
                },
            }
        )
        injected = await adapter.inject(session, "Verified context for the next turn.")
        return bridge, stale, interrupted, injected

    bridge, stale, interrupted, injected = asyncio.run(scenario())

    assert isinstance(stale, LifecycleError) and stale.code is LifecycleErrorCode.STALE_STATE
    assert interrupted is None
    assert injected is None
    assert [method for method, _params in bridge.calls] == ["start", "interrupt", "inject"]
    assert bridge.calls[-1][1]["text"] == "Verified context for the next turn."


def test_permission_decision_is_distinct_from_turn_injection(tmp_path: Path) -> None:
    async def scenario():
        bridge = FakeClaudeBridge()
        adapter = _adapter(bridge, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await bridge.emit(
            {
                "kind": "action.requested",
                "eventId": "evt-approval-1",
                "turnId": "turn-1",
                "payload": {
                    "permissionId": "permission-1",
                    "toolName": "Bash",
                    "input": {"command": "git push"},
                    "reason": "network access",
                },
            }
        )
        action = await adapter.next_action(session)
        injection = await adapter.inject(session, "do not use this as approval")
        resolution = await adapter.resolve_action(session, action)
        return bridge, action, injection, resolution

    bridge, action, injection, resolution = asyncio.run(scenario())

    assert action.kind is ActionKind.APPROVE
    assert action.parameters["permission_id"] == "permission-1"
    assert isinstance(injection, LifecycleError)
    assert resolution is None
    assert bridge.calls[-1] == (
        "resolve_permission",
        {
            "sessionId": "claude-session-1",
            "permissionId": "permission-1",
            "behavior": "allow",
        },
    )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"model": "missing"}, "model"),
        ({"effort": "max"}, "effort"),
    ],
)
def test_start_rejects_unadvertised_model_and_effort(
    tmp_path: Path, updates: dict[str, Any], message: str
) -> None:
    async def scenario():
        return await _adapter(FakeClaudeBridge(), tmp_path).start(_request(tmp_path, **updates))

    result = asyncio.run(scenario())
    assert isinstance(result, LifecycleError)
    assert result.code is LifecycleErrorCode.PROTOCOL_VERSION
    assert message in result.message


def test_start_fails_closed_before_bridge_without_auth_or_safety_evidence(tmp_path: Path) -> None:
    async def scenario():
        first_bridge = FakeClaudeBridge()
        no_auth = ClaudeManagedAdapter(
            bridge=first_bridge,
            authorize_start=_evidence,
            authentication=None,
            state_path=tmp_path / "one.sqlite3",
        )
        second_bridge = FakeClaudeBridge()
        no_evidence = ClaudeManagedAdapter(
            bridge=second_bridge,
            authorize_start=lambda request: None,
            authentication=ClaudeAuthentication(provider="anthropic-api", source="environment"),
            state_path=tmp_path / "two.sqlite3",
        )
        return (
            first_bridge,
            await no_auth.start(_request(tmp_path)),
            second_bridge,
            await no_evidence.start(_request(tmp_path)),
        )

    first, no_auth, second, no_evidence = asyncio.run(scenario())
    assert isinstance(no_auth, LifecycleError) and no_auth.code is LifecycleErrorCode.STALE_STATE
    assert isinstance(no_evidence, LifecycleError)
    assert first.calls == second.calls == []


@pytest.mark.parametrize(
    "provider",
    ["anthropic-api", "bedrock", "vertex", "foundry"],
)
def test_only_documented_api_and_cloud_auth_providers_are_accepted(
    tmp_path: Path, provider: str
) -> None:
    adapter = ClaudeManagedAdapter(
        bridge=FakeClaudeBridge(),
        authorize_start=_evidence,
        authentication=ClaudeAuthentication(provider=provider, source="environment"),
        state_path=tmp_path / f"{provider}.sqlite3",
    )
    assert adapter.authentication.provider == provider
    with pytest.raises(ValueError, match="subscription"):
        ClaudeAuthentication(provider="claude-ai-subscription", source="login")


def test_auth_detection_rejects_oauth_and_selects_documented_environment() -> None:
    assert detect_claude_authentication({"CLAUDE_CODE_OAUTH_TOKEN": "secret"}) is None
    assert detect_claude_authentication({"ANTHROPIC_API_KEY": "secret"}) == (
        ClaudeAuthentication(provider="anthropic-api", source="environment")
    )
    assert detect_claude_authentication({"CLAUDE_CODE_USE_BEDROCK": "1"}) == (
        ClaudeAuthentication(provider="bedrock", source="environment")
    )


def test_usage_is_preserved_as_sdk_reported_and_secrets_are_redacted(tmp_path: Path) -> None:
    async def scenario():
        bridge = FakeClaudeBridge()
        adapter = _adapter(bridge, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await bridge.emit(
            {
                "kind": "turn.completed",
                "eventId": "evt-result",
                "turnId": "turn-1",
                "payload": {
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                    "reportedCostUsd": 0.004,
                    "costSource": "sdk_reported",
                    "Authorization": "Bearer must-not-leak",
                },
            }
        )
        await anext(adapter.events(session))
        return await anext(adapter.events(session))

    event = asyncio.run(scenario())
    assert event.kind is EventKind.TURN_COMPLETED
    assert event.payload["reportedCostUsd"] == 0.004
    assert event.payload["costSource"] == "sdk_reported"
    assert "must-not-leak" not in event.model_dump_json()


def test_restart_marks_inflight_sdk_session_recovering_then_orphaned(tmp_path: Path) -> None:
    async def scenario():
        state = tmp_path / "state.sqlite3"
        first = ClaudeManagedAdapter(
            bridge=FakeClaudeBridge(),
            authorize_start=_evidence,
            authentication=ClaudeAuthentication(provider="anthropic-api", source="environment"),
            state_path=state,
        )
        session = await first.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await first.close()
        second_bridge = FakeClaudeBridge()
        second = ClaudeManagedAdapter(
            bridge=second_bridge,
            authorize_start=_evidence,
            authentication=ClaudeAuthentication(provider="anthropic-api", source="environment"),
            state_path=state,
        )
        before = second.session_status(session.session_id)
        result = await second.attach(session)
        return second, second_bridge, before, result

    adapter, bridge, before, result = asyncio.run(scenario())
    assert before == "recovering"
    assert isinstance(result, LifecycleError) and result.code is LifecycleErrorCode.STALE_STATE
    assert adapter.session_status("claude-session-1") == "orphaned"
    assert bridge.calls == []


def test_bridge_rejects_wrong_session_and_events_after_terminal_state(tmp_path: Path) -> None:
    async def scenario():
        bridge = FakeClaudeBridge()
        adapter = _adapter(bridge, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        wrong = {
            "method": "event",
            "params": {
                "protocolVersion": 1,
                "sessionId": "unknown",
                "event": {"kind": "tool.call", "eventId": "e", "payload": {}},
            },
        }
        with pytest.raises(ValueError, match="unknown session"):
            await adapter.handle_bridge_message(wrong)
        await bridge.emit(
            {"kind": "session.stopped", "eventId": "stop", "payload": {"reason": "closed"}}
        )
        with pytest.raises(ValueError, match="terminal"):
            await bridge.emit(
                {"kind": "tool.call", "eventId": "late", "payload": {"toolName": "Bash"}}
            )

    asyncio.run(scenario())
