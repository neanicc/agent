from __future__ import annotations

from loopguard import LoopEvent


def tool_event(path: str = "./package.json", **kwargs):
    return LoopEvent(
        run_id="r",
        agent="a",
        kind="tool_call",
        tool_name="read_file",
        tool_args={"path": path, **kwargs},
        error="package.json not found",
    )
