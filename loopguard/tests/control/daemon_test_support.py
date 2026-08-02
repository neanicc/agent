from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.protocol import Frame, MessageType, encode_frame, read_frame


def tool_event(
    event_id: str,
    *,
    session_id: str = "session",
    command: str = "pytest",
    payload: dict[str, Any] | None = None,
) -> ControlEvent:
    return ControlEvent(
        event_id=event_id,
        kind=EventKind.TOOL_CALL,
        source="test",
        session=SessionRef(host_id="host", repo_id="repo", session_id=session_id),
        payload=payload
        or {
            "agent": "codex",
            "tool_name": "Bash",
            "arguments": {"cmd": command},
        },
    )


async def send_event(
    socket_path: Path,
    event: ControlEvent,
    *,
    request_id: str | None = None,
) -> Frame:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write(
            encode_frame(
                MessageType.EVENT,
                request_id=request_id or f"req-{event.event_id}",
                payload=event.model_dump(mode="json"),
            )
        )
        await writer.drain()
        response = await read_frame(reader)
        assert response is not None
        return response
    finally:
        writer.close()
        await writer.wait_closed()


@contextmanager
def short_socket_path() -> Iterator[Path]:
    """Create an owner-only socket path below platform AF_UNIX limits."""
    with tempfile.TemporaryDirectory(prefix="lg-") as directory:
        os.chmod(directory, 0o700)
        yield Path(directory) / "control.sock"
