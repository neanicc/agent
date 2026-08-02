from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from loopguard.cli import app
from loopguard.context.capability import (
    ALL_CONTEXT_TOOLS,
    CapabilityIssuer,
    ContextCapability,
)
from loopguard.context.digest import DigestBuilder
from loopguard.context.handoff import Handoff, HandoffStore
from loopguard.context.index import SymbolIndex
from loopguard.context.journal import ChangeJournal
from loopguard.context.leases import LeaseManager
from loopguard.context.mcp_server import ContextServices, build_context_server
from loopguard.context.models import ChangeObservation
from loopguard.control.daemon import DaemonServices


def _capability(*, tools=ALL_CONTEXT_TOOLS) -> ContextCapability:
    now = datetime.now(timezone.utc)
    return ContextCapability(
        host_id="host",
        repo_id="repo",
        session_id="session",
        allowed_tools=sorted(tools),
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        nonce="a" * 64,
    )


def _services(tmp_path: Path, *, verification=None) -> ContextServices:
    return ContextServices(
        journal=ChangeJournal(tmp_path / "context.db"),
        leases=LeaseManager(tmp_path / "leases.db"),
        handoffs=HandoffStore(tmp_path / "handoffs.db"),
        digest=DigestBuilder(),
        verification=verification,
    )


def _record(services: ContextServices, seq: int, path: str, *, patch: str | None = None) -> None:
    services.journal.record(
        ChangeObservation(
            observation_id=f"observation-{seq}",
            control_event_id=f"event-{seq}",
            repo_id="repo",
            repo_seq=seq,
            worktree_id="worktree",
            path=path,
            actor="codex:session",
            before_hash="old",
            after_hash="new",
            patch=patch,
            symbols=[f"symbol_{seq}"],
            verification_ids=[f"verify-{seq}"],
        )
    )


def test_get_changes_since_returns_cursor_redacted_records_and_no_spoof_fields(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path)
    _record(services, 4, "src/a.py", patch="Authorization: Bearer abcdefghijklmnop")
    _record(services, 5, "tests/test_a.py")
    server = build_context_server(services, capability=_capability())

    result = server.invoke_tool("get_changes_since", {"repo_seq": 3})

    assert result["next_repo_seq"] == 5
    assert [item["path"] for item in result["changes"]] == [
        "src/a.py",
        "tests/test_a.py",
    ]
    assert "abcdefghijklmnop" not in json.dumps(result)
    tools = asyncio.run(server.fastmcp.list_tools())
    schema = next(tool.inputSchema for tool in tools if tool.name == "get_changes_since")
    assert "repo_id" not in schema["properties"]
    assert "session_id" not in schema["properties"]
    with pytest.raises(ValueError, match="arguments"):
        server.invoke_tool("get_changes_since", {"repo_seq": 3, "repo_id": "other"})


def test_claim_release_and_handoff_use_capability_identities(tmp_path: Path) -> None:
    services = _services(tmp_path)
    _record(services, 1, "src/auth.py")
    server = build_context_server(services, capability=_capability())

    claimed = server.invoke_tool(
        "claim_work",
        {"scopes": [{"kind": "file", "path": "src/auth.py"}], "ttl_seconds": 60},
    )
    created = server.invoke_tool("create_handoff")
    restored = server.invoke_tool("read_handoff", {"handoff_id": created["handoff_id"]})
    released = server.invoke_tool("release_work")

    assert claimed["results"][0]["owner_session_id"] == "session"
    assert restored["repo_id"] == "repo"
    assert restored["handoff"]["changed_paths"] == ["src/auth.py"]
    assert released == {"released": 1}


def test_capability_only_registers_and_allows_selected_tools(tmp_path: Path) -> None:
    server = build_context_server(
        _services(tmp_path),
        capability=_capability(tools=["get_repo_state"]),
    )

    tools = asyncio.run(server.fastmcp.list_tools())

    assert [tool.name for tool in tools] == ["get_repo_state"]
    with pytest.raises(PermissionError, match="does not allow"):
        server.invoke_tool("release_work")


def test_verification_and_handoff_reads_reject_other_repository(tmp_path: Path) -> None:
    class Verification:
        def metadata(self, run_id: str):
            return SimpleNamespace(repository_id="other")

        def read(self, run_id: str):
            raise AssertionError("cross-repository verification must not be read")

    services = _services(tmp_path, verification=Verification())
    foreign = services.handoffs.create(
        "other",
        "session",
        Handoff(
            goal="Foreign work",
            repo_seq=0,
            changed_paths=[],
            verification_ids=[],
            accepted_decisions=[],
            unresolved=[],
            risks=[],
        ),
    )
    server = build_context_server(services, capability=_capability())

    with pytest.raises(KeyError, match="verification not found"):
        server.invoke_tool(
            "get_verification_status",
            {"verification_id": "foreign-run"},
        )
    with pytest.raises(KeyError, match="handoff not found"):
        server.invoke_tool("read_handoff", {"handoff_id": foreign.handoff_id})


def test_daemon_registry_activates_every_available_context_service(tmp_path: Path) -> None:
    services = _services(tmp_path)
    index = SymbolIndex(tmp_path / "symbols.db")
    watcher = object()
    launcher = object()
    registry = DaemonServices(
        change_journal=services.journal,
        change_watcher=watcher,
        symbol_index=index,
        lease_manager=services.leases,
        digest_service=services.digest,
        handoff_store=services.handoffs,
        context_mcp_launcher=launcher,
    )

    active = registry.context_services()

    assert active.journal is services.journal
    assert active.watcher is watcher
    assert active.symbol_index is index
    assert active.leases is services.leases
    assert active.handoffs is services.handoffs
    assert active.mcp_launcher is launcher


def test_hidden_cli_entrypoint_consumes_capability_before_stdio_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "state"
    home.mkdir(mode=0o700)
    with CapabilityIssuer(home) as issuer:
        token_file = issuer.write_token(
            issuer.issue(host_id="host", repo_id="repo", session_id="session")
        )
    started: list[bool] = []
    monkeypatch.setattr(
        "loopguard.context.mcp_server.ContextMCPServer.run",
        lambda self: started.append(True),
    )

    result = CliRunner().invoke(
        app,
        ["context-mcp", "--home", str(home)],
        env={"LOOPGUARD_CONTEXT_CAPABILITY_FILE": str(token_file)},
    )

    assert result.exit_code == 0
    assert started == [True]
    assert not token_file.exists()
