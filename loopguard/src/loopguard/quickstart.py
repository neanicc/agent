from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .control.daemon import LoopGuardDaemon
from .control.events import ControlEvent, EventKind, SessionRef
from .control.paths import ensure_private_home
from .control.protocol import MessageType, encode_frame, read_frame
from .control.store import EventStore


class QuickstartUnavailableError(Exception):
    """The real local quickstart transport is unavailable on this platform."""


@dataclass(frozen=True, slots=True)
class QuickstartResult:
    events: list[dict[str, int | str]]
    decision: dict[str, Any]
    persisted_events: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "events": self.events,
            "decision": self.decision,
            "persisted_events": self.persisted_events,
            "model_used": False,
            "api_key_used": False,
            "telemetry_sent": False,
        }


async def run_quickstart(home: str | Path | None = None) -> QuickstartResult:
    if os.name != "posix":
        raise QuickstartUnavailableError("verified Windows named-pipe support is unavailable")
    started = time.monotonic()
    timeline: list[dict[str, int | str]] = []

    def mark(name: str) -> None:
        timeline.append({"name": name, "elapsed_ms": int((time.monotonic() - started) * 1_000)})

    mark("quickstart.started")
    base = Path(home).expanduser() if home is not None else None
    if base is not None:
        ensure_private_home(base)

    decision: dict[str, Any] = {}
    persisted_events = 0
    with tempfile.TemporaryDirectory(prefix="loopguard-quickstart-", dir=base) as state_dir:
        with tempfile.TemporaryDirectory(prefix="lgq-") as socket_dir:
            os.chmod(state_dir, 0o700)
            os.chmod(socket_dir, 0o700)
            state_path = Path(state_dir)
            repository = state_path / "repository"
            repository.mkdir(mode=0o700)
            (repository / "pyproject.toml").write_text(
                '[project]\nname = "loopguard-quickstart"\nversion = "0.0.0"\n'
            )
            socket_path = Path(socket_dir) / "control.sock"
            store = EventStore(state_path / "events.db", key=secrets.token_bytes(32))
            daemon = LoopGuardDaemon(store=store, socket_path=socket_path)
            await daemon.start()
            mark("daemon.ready")
            try:
                for index in range(1, 4):
                    event = ControlEvent(
                        event_id=f"quickstart-event-{index}",
                        kind=EventKind.TOOL_CALL,
                        source="quickstart",
                        session=SessionRef(
                            host_id="quickstart-host",
                            repo_id="quickstart-repository",
                            session_id="quickstart-session",
                        ),
                        payload={
                            "agent": "quickstart-agent",
                            "tool_name": "read_file",
                            "arguments": {"path": "package.json"},
                            "error": "package.json not found",
                            "tokens": 0,
                            "cost_usd": 0,
                        },
                    )
                    response = await _send_event(socket_path, event, index)
                    if response.message_type is not MessageType.ACK:
                        raise RuntimeError("quickstart daemon rejected a valid event")
                    decision = dict(response.payload["decision"])
                persisted_events = store.count()
                mark("event.acknowledged")
                if decision.get("action") != "request_approval":
                    raise RuntimeError("quickstart did not reach the real loop detector")
                mark("loop.detected")
            finally:
                await daemon.close()
                store.close()
    mark("quickstart.cleaned")
    return QuickstartResult(
        events=timeline,
        decision=decision,
        persisted_events=persisted_events,
    )


async def _send_event(socket_path: Path, event: ControlEvent, index: int):
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write(
            encode_frame(
                MessageType.EVENT,
                request_id=f"quickstart-{index}",
                payload=event.model_dump(mode="json"),
            )
        )
        await writer.drain()
        response = await read_frame(reader)
        if response is None:
            raise RuntimeError("quickstart daemon closed without an acknowledgement")
        return response
    finally:
        writer.close()
        await writer.wait_closed()
