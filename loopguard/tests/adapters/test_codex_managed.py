from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from loopguard.adapters.base import (
    Capability,
    LifecycleError,
    LifecycleErrorCode,
    ManagedRunRequest,
)
from loopguard.adapters.codex_managed import (
    CODEX_CLIENT_INFO,
    CodexManagedAdapter,
    ManagedStartEvidence,
)
from loopguard.adapters.jsonrpc import (
    JsonRpcClient,
    JsonRpcProcessExited,
    JsonRpcProtocolError,
    JsonRpcTimeout,
    decode_jsonrpc_line,
)
from loopguard.control.events import EventKind


FIXTURE = Path(__file__).parents[1] / "fixtures" / "codex_app_server.jsonl"


class FakeRpc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False
        self._turn = "turn-1"

    async def request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any:
        self.calls.append(("request", method, dict(params)))
        if method == "initialize":
            return {"userAgent": "codex-test"}
        if method == "model/list":
            return {
                "data": [
                    {
                        "id": "gpt-test",
                        "model": "gpt-test",
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "low"},
                            {"reasoningEffort": "medium"},
                        ],
                    }
                ]
            }
        if method == "modelProvider/capabilities/read":
            return {"imageGeneration": False, "namespaceTools": True, "webSearch": True}
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}, "modelProvider": "openai"}
        if method == "turn/start":
            return {"turn": {"id": self._turn, "status": "inProgress", "items": []}}
        if method in {"turn/steer", "turn/interrupt", "thread/inject_items"}:
            return {}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"], "turns": []}}
        raise AssertionError(f"unexpected method: {method}")

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        self.calls.append(("notify", method, dict(params)))

    async def respond(self, request_id: str | int, result: Mapping[str, Any]) -> None:
        self.calls.append(("response", str(request_id), dict(result)))

    async def close(self) -> None:
        self.closed = True

    @property
    def methods(self) -> list[str]:
        return [method for _kind, method, _params in self.calls]


def _request(tmp_path: Path, **updates: Any) -> ManagedRunRequest:
    values: dict[str, Any] = {
        "repository_id": "repo-1",
        "repository_root": tmp_path / "repo",
        "worktree_id": "worktree-1",
        "worktree_root": tmp_path / "worktree",
        "prompt": "Fix the verified regression.",
        "model": "gpt-test",
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


def _adapter(rpc: FakeRpc, tmp_path: Path) -> CodexManagedAdapter:
    return CodexManagedAdapter(
        rpc=rpc,
        authorize_start=_evidence,
        state_path=tmp_path / "codex-managed.sqlite3",
    )


def test_managed_codex_uses_stable_lifecycle_and_exact_turn_controls(tmp_path: Path) -> None:
    async def scenario():
        rpc = FakeRpc()
        adapter = _adapter(rpc, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        assert await adapter.steer_active_turn(session, "Run impacted tests first.") is None
        assert await adapter.interrupt(session) is None
        return rpc, adapter, session

    rpc, adapter, session = asyncio.run(scenario())

    assert adapter.capabilities.supports(Capability.SELECT_MODEL)
    assert rpc.methods == [
        "initialize",
        "initialized",
        "model/list",
        "modelProvider/capabilities/read",
        "thread/start",
        "turn/start",
        "turn/steer",
        "turn/interrupt",
    ]
    initialize = rpc.calls[0][2]
    steer = rpc.calls[-2][2]
    interrupt = rpc.calls[-1][2]
    assert initialize == {
        "clientInfo": CODEX_CLIENT_INFO,
        "capabilities": {"experimentalApi": False},
    }
    assert steer["expectedTurnId"] == session.turn_id == "turn-1"
    assert interrupt == {"threadId": "thread-1", "turnId": "turn-1"}


def test_between_turn_injection_never_substitutes_for_steering(tmp_path: Path) -> None:
    async def scenario():
        rpc = FakeRpc()
        adapter = _adapter(rpc, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        stale = await adapter.inject_between_turns(session, "not yet")
        await adapter.handle_server_message(
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
            }
        )
        injected = await adapter.inject_between_turns(session, "Previously verified context.")
        steer = await adapter.steer_active_turn(session, "too late")
        return rpc, stale, injected, steer

    rpc, stale, injected, steer = asyncio.run(scenario())

    assert isinstance(stale, LifecycleError) and stale.code is LifecycleErrorCode.STALE_STATE
    assert injected is None
    assert isinstance(steer, LifecycleError) and steer.code is LifecycleErrorCode.STALE_STATE
    assert rpc.methods.count("thread/inject_items") == 1
    params = rpc.calls[-1][2]
    assert params == {
        "threadId": "thread-1",
        "items": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Previously verified context."}],
            }
        ],
    }


def test_start_fails_closed_without_matching_lease_and_baseline(tmp_path: Path) -> None:
    async def scenario():
        rpc = FakeRpc()
        adapter = CodexManagedAdapter(
            rpc=rpc,
            authorize_start=lambda request: None,
            state_path=tmp_path / "state.sqlite3",
        )
        return rpc, await adapter.start(_request(tmp_path))

    rpc, result = asyncio.run(scenario())

    assert isinstance(result, LifecycleError)
    assert result.code is LifecycleErrorCode.STALE_STATE
    assert rpc.calls == []


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"model": "missing"}, "model"),
        ({"effort": "xhigh"}, "effort"),
    ],
)
def test_start_rejects_unadvertised_model_or_effort(tmp_path: Path, updates, match) -> None:
    async def scenario():
        adapter = _adapter(FakeRpc(), tmp_path)
        return await adapter.start(_request(tmp_path, **updates))

    result = asyncio.run(scenario())
    assert isinstance(result, LifecycleError)
    assert result.code is LifecycleErrorCode.PROTOCOL_VERSION
    assert match in result.message


def test_notifications_become_bounded_control_events_and_approvals(tmp_path: Path) -> None:
    async def scenario():
        rpc = FakeRpc()
        adapter = _adapter(rpc, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await adapter.handle_server_message(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 81,
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "command": "git push",
                    "reason": "network access",
                },
            }
        )
        await adapter.handle_server_message(
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {"id": "item-1", "type": "commandExecution"},
                },
            }
        )
        started = await anext(adapter.events(session))
        first = await anext(adapter.events(session))
        second = await anext(adapter.events(session))
        action = await adapter.next_action(session)
        return started, first, second, action

    started, first, second, action = asyncio.run(scenario())

    assert started.kind is EventKind.SESSION_STARTED
    assert first.kind is EventKind.ACTION_REQUESTED
    assert second.kind is EventKind.TOOL_CALL
    assert action.action_id.startswith("codex:")
    assert action.parameters["request_method"] == "item/commandExecution/requestApproval"
    assert action.parameters["vendor_request_id"] == 81
    assert "git push" in action.parameters["vendor_params"]["command"]


def test_malformed_jsonrpc_frames_and_unknown_shapes_fail_closed() -> None:
    with pytest.raises(JsonRpcProtocolError, match="valid JSON"):
        decode_jsonrpc_line(b"{bad\n")
    with pytest.raises(JsonRpcProtocolError, match="object"):
        decode_jsonrpc_line(b"[]\n")
    with pytest.raises(JsonRpcProtocolError, match="maximum"):
        decode_jsonrpc_line(b"{" + b'"x":"' + b"a" * 128 + b'"}\n', max_frame_bytes=32)
    with pytest.raises(JsonRpcProtocolError, match="correlation"):
        decode_jsonrpc_line(b'{"result":{}}\n')


def test_state_restart_marks_active_turn_recovering_then_reattaches(tmp_path: Path) -> None:
    async def scenario():
        state = tmp_path / "codex.sqlite3"
        first_rpc = FakeRpc()
        first = CodexManagedAdapter(rpc=first_rpc, authorize_start=_evidence, state_path=state)
        session = await first.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await first.close()

        second_rpc = FakeRpc()
        second = CodexManagedAdapter(rpc=second_rpc, authorize_start=_evidence, state_path=state)
        assert second.session_status(session.session_id) == "recovering"
        recovered = await second.attach(session)
        return second, second_rpc, recovered

    adapter, rpc, recovered = asyncio.run(scenario())

    assert recovered is None
    assert adapter.session_status("thread-1") == "reattached"
    assert "thread/resume" in rpc.methods
    assert "turn/start" not in rpc.methods


def test_protocol_fixture_matches_installed_codex_schema_when_available(tmp_path: Path) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI is not installed")
    output = tmp_path / "schema"
    completed = subprocess.run(
        [codex, "app-server", "generate-json-schema", "--out", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        pytest.skip("installed Codex CLI cannot generate app-server schemas")

    entries = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    metadata = entries[0]
    version = subprocess.run(
        [codex, "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()
    assert metadata["codex_cli_version"] in version
    assert metadata["client_info"] == CODEX_CLIENT_INFO
    for entry in entries[1:]:
        schema = json.loads((output / entry["schema"]).read_text())
        assert sorted(schema.get("required", [])) == sorted(entry["required"])


def test_action_expiry_is_aware_and_bounded(tmp_path: Path) -> None:
    async def scenario():
        adapter = _adapter(FakeRpc(), tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        before = datetime.now(timezone.utc)
        await adapter.handle_server_message(
            {
                "method": "item/fileChange/requestApproval",
                "id": "approval-1",
                "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "i"},
            }
        )
        return before, await adapter.next_action(session)

    before, action = asyncio.run(scenario())
    assert action.expires_at.tzinfo is not None
    assert 0 < (action.expires_at - before).total_seconds() <= 301


@pytest.mark.parametrize(
    ("method", "params", "expected"),
    [
        (
            "item/commandExecution/requestApproval",
            {"command": "pytest"},
            {"decision": "accept"},
        ),
        ("execCommandApproval", {"command": "pytest"}, {"decision": "approved"}),
        (
            "item/permissions/requestApproval",
            {"permissions": {"network": {"enabled": True}}},
            {"permissions": {"network": {"enabled": True}}, "scope": "turn"},
        ),
    ],
)
def test_approval_resolution_uses_each_pinned_response_shape(
    tmp_path: Path, method: str, params: dict[str, Any], expected: dict[str, Any]
) -> None:
    async def scenario():
        rpc = FakeRpc()
        adapter = _adapter(rpc, tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await adapter.handle_server_message(
            {
                "method": method,
                "id": 91,
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    **params,
                },
            }
        )
        action = await adapter.next_action(session)
        result = await adapter.resolve_action(session, action)
        return rpc, result

    rpc, result = asyncio.run(scenario())
    assert result is None
    assert rpc.calls[-1] == ("response", "91", expected)


def test_unrelated_known_server_notification_is_ignored_safely(tmp_path: Path) -> None:
    async def scenario():
        adapter = _adapter(FakeRpc(), tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await adapter.handle_server_message(
            {"method": "account/updated", "params": {"authMode": "apiKey"}}
        )
        return adapter.session_status(session.session_id)

    assert asyncio.run(scenario()) == "active"


@pytest.mark.parametrize(
    ("server", "error"),
    [
        (
            "import json,sys; r=json.loads(sys.stdin.readline()); "
            "print(json.dumps({'id': r['id'] + 1, 'result': {}}), flush=True)",
            JsonRpcProtocolError,
        ),
        (
            "import sys; sys.stdin.readline(); print('{bad', flush=True)",
            JsonRpcProtocolError,
        ),
        ("import sys; sys.stdin.readline()", JsonRpcProcessExited),
    ],
)
def test_jsonrpc_process_rejects_unknown_ids_malformed_frames_and_exit(
    server: str, error: type[Exception]
) -> None:
    async def scenario():
        async def handle(_message):
            return None

        client = await JsonRpcClient.launch(
            (sys.executable, "-c", server),
            on_message=handle,
        )
        try:
            await client.request("test", {}, timeout=2)
        finally:
            await client.close()

    with pytest.raises(error):
        asyncio.run(scenario())


def test_jsonrpc_timeout_terminates_the_ambiguous_child() -> None:
    async def scenario():
        async def handle(_message):
            return None

        client = await JsonRpcClient.launch(
            (
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.readline(); time.sleep(30)",
            ),
            on_message=handle,
        )
        try:
            with pytest.raises(JsonRpcTimeout):
                await client.request("test", {}, timeout=0.05)
            assert client.process.returncode is not None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_jsonrpc_diagnostics_and_approval_events_redact_secrets(tmp_path: Path) -> None:
    async def diagnostics():
        async def handle(_message):
            return None

        server = (
            "import json,sys; r=json.loads(sys.stdin.readline()); "
            "print('Authorization: Bearer must-not-leak', file=sys.stderr, flush=True); "
            "print(json.dumps({'id': r['id'], 'result': {}}), flush=True)"
        )
        client = await JsonRpcClient.launch((sys.executable, "-c", server), on_message=handle)
        try:
            await client.request("test", {}, timeout=2)
            await asyncio.sleep(0.05)
            return client.diagnostics
        finally:
            await client.close()

    async def approval():
        adapter = _adapter(FakeRpc(), tmp_path)
        session = await adapter.start(_request(tmp_path))
        assert not isinstance(session, LifecycleError)
        await adapter.handle_server_message(
            {
                "method": "item/commandExecution/requestApproval",
                "id": 101,
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "command": "curl -H 'Authorization: Bearer must-not-leak' example.test",
                    "environment": {"OPENAI_API_KEY": "sk-must-not-leak"},
                },
            }
        )
        return await adapter.next_action(session)

    diagnostic_lines = asyncio.run(diagnostics())
    action = asyncio.run(approval())
    rendered = action.model_dump_json()
    assert diagnostic_lines == ("Authorization: [REDACTED]",)
    assert "must-not-leak" not in rendered
    assert "[REDACTED]" in rendered
