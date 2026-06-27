from __future__ import annotations

from typing import Any

from loopguard.event import LoopEvent

READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


def tool_call_to_event(run_id: str, agent: str, tool_call: Any) -> LoopEvent:
    fn = getattr(tool_call, "function", None) or tool_call.get("function", {})
    name = getattr(fn, "name", None) or fn.get("name", "tool")
    args = getattr(fn, "arguments", None) or fn.get("arguments", {})
    return LoopEvent(
        run_id=run_id, agent=agent, kind="tool_call", tool_name=name, tool_args={"arguments": args}
    )
