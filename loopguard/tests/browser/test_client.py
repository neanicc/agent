from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from pathlib import Path

import pytest

from loopguard.browser.client import BrowserBrokerClient, LocalBrokerFactory
from loopguard.browser.service import BrowserService
from loopguard.control.daemon import DaemonServices
from loopguard.control.decisions import ActionTarget, PolicyDecision, TargetKind
from loopguard.control.events import ControlEvent, EventKind, SessionRef


def run(coroutine):
    return asyncio.run(coroutine)


class FakeConnection:
    def __init__(self) -> None:
        self.alive = True
        self.hang = False
        self.conflicting_id = False
        self.closed = False

    def is_alive(self) -> bool:
        return self.alive

    async def request(self, body: dict, timeout_seconds: float) -> dict:
        if self.hang:
            await asyncio.Event().wait()
        if body.get("type") == "hello":
            return {"id": "handshake", "ok": True, "result": {"protocolVersion": 1}}
        request_id = "wrong" if self.conflicting_id else body["id"]
        if body["method"] == "health":
            return {
                "id": request_id,
                "ok": True,
                "result": {"activeContexts": 0, "browsers": {"chromium": "fake-1"}},
            }
        if body["method"] == "context.create":
            return {
                "id": request_id,
                "ok": True,
                "result": {"contextId": "c" * 64, "artifactDirectory": "/safe/artifacts/a"},
            }
        return {"id": request_id, "ok": True, "result": {"artifacts": []}}

    async def close(self) -> None:
        self.closed = True
        self.alive = False

    def exit(self) -> None:
        self.alive = False


class FakeBrokerFactory:
    def __init__(self) -> None:
        self.starts = 0
        self.current: FakeConnection | None = None
        self.capabilities: list[str] = []

    async def start(self, capability: str) -> FakeConnection:
        self.starts += 1
        self.capabilities.append(capability)
        self.current = FakeConnection()
        return self.current


def test_client_restarts_broker_after_process_exit() -> None:
    async def scenario() -> None:
        factory = FakeBrokerFactory()
        delays: list[float] = []
        client = BrowserBrokerClient(
            factory=factory,
            backoff_base_seconds=0.01,
            sleeper=lambda delay: _record_delay(delays, delay),
        )
        first = await client.health()
        assert factory.current is not None
        factory.current.exit()
        second = await client.health()
        assert first.ok and second.ok
        assert factory.starts == 2
        assert client.generation == 2
        assert delays == [0.01]
        assert factory.capabilities[0] != factory.capabilities[1]
        await client.close()

    run(scenario())


def test_page_command_has_hard_timeout() -> None:
    async def scenario() -> None:
        factory = FakeBrokerFactory()
        client = BrowserBrokerClient(factory=factory)
        await client.health()
        assert factory.current is not None
        factory.current.hang = True
        result = await client.run_page(
            "c" * 64,
            actions=[],
            timeout_seconds=0.01,
        )
        assert not result.ok
        assert result.code == "timeout"
        assert factory.current.closed

    run(scenario())


def test_response_correlation_failure_closes_untrusted_connection() -> None:
    async def scenario() -> None:
        factory = FakeBrokerFactory()
        client = BrowserBrokerClient(factory=factory)
        await client.health()
        assert factory.current is not None
        factory.current.conflicting_id = True
        result = await client.health()
        assert not result.ok
        assert result.code == "protocol_error"
        assert factory.current.closed

    run(scenario())


def test_service_marks_context_lost_after_broker_or_daemon_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = FakeBrokerFactory()
        client = BrowserBrokerClient(factory=factory)
        service = BrowserService(tmp_path / "browser-leases.json", client=client)
        await service.session_started("session-a")
        lease = await service.create_context(
            "session-a", browser="chromium", allowed_origins=["https://example.test"]
        )
        assert lease.status == "active"
        state = tmp_path / "browser-leases.json"
        assert stat.S_IMODE(state.stat().st_mode) == 0o600
        assert "c" * 64 not in state.read_text()
        assert factory.current is not None
        factory.current.exit()
        assert (await client.health()).ok
        await service.reconcile()
        assert service.lease("session-a").status == "lost"
        await service.close()

        restarted = BrowserService(
            tmp_path / "browser-leases.json", client=BrowserBrokerClient(factory=FakeBrokerFactory())
        )
        assert restarted.lease("session-a").status == "lost"
        assert restarted.lease("session-a").context_id is None
        await restarted.close()

    run(scenario())


def test_daemon_registry_exposes_browser_service(tmp_path: Path) -> None:
    service = BrowserService(
        tmp_path / "browser-leases.json",
        client=BrowserBrokerClient(factory=FakeBrokerFactory()),
    )
    assert DaemonServices(browser_service=service).browser_service is service
    run(service.close())


def test_daemon_session_lifecycle_registers_and_closes_browser_lease(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = BrowserService(
            tmp_path / "browser-leases.json",
            client=BrowserBrokerClient(factory=FakeBrokerFactory()),
        )
        registry = DaemonServices(browser_service=service)
        session = SessionRef(host_id="host", repo_id="repo", session_id="session-a")
        core = PolicyDecision(
            decision_id="core",
            action="allow",
            reason="safe",
            target=ActionTarget(kind=TargetKind.SESSION, target_id="session-a"),
            state_version=1,
            state_hash="a" * 64,
        )
        await registry.apply_policy(
            ControlEvent(
                event_id="start",
                kind=EventKind.SESSION_STARTED,
                source="test",
                session=session,
            ),
            core,
        )
        assert service.lease("session-a").status == "registered"
        await registry.apply_policy(
            ControlEvent(
                event_id="stop",
                kind=EventKind.SESSION_STOPPED,
                source="test",
                session=session,
            ),
            core,
        )
        with pytest.raises(KeyError):
            service.lease("session-a")
        await service.close()

    run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="real broker smoke uses the Unix transport")
def test_local_factory_supervises_real_pinned_broker() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory(prefix="lg-browser-") as directory:
            home = Path(directory)
            home.chmod(0o700)
            client = BrowserBrokerClient(factory=LocalBrokerFactory(home=home))
            result = await client.health()
            assert result.ok
            assert result.result == {"activeContexts": 0, "browsers": {}}
            await client.close()
            assert list((home / "browser").glob("*.sock")) == []

    run(scenario())


async def _record_delay(delays: list[float], delay: float) -> None:
    delays.append(delay)
