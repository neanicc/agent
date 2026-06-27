from __future__ import annotations

from loopguard.config import LoopGuardConfig
from loopguard.decision import LoopDecision
from loopguard.event import LoopEvent


def detect(events: list[LoopEvent], config: LoopGuardConfig) -> LoopDecision:
    tool_calls = sum(1 for e in events if e.kind == "tool_call")
    cost = sum(e.cost_usd for e in events)
    if config.max_tool_calls is not None and tool_calls > config.max_tool_calls:
        return LoopDecision(
            allowed=False,
            tripped=True,
            reason=f"Tool-call budget exceeded: {tool_calls}>{config.max_tool_calls}",
            detector="budget",
            matching_events=events[-config.trip_count :],
        )
    if config.max_cost_usd is not None and cost > config.max_cost_usd:
        return LoopDecision(
            allowed=False,
            tripped=True,
            reason=f"Cost budget exceeded: ${cost:.3f}>${config.max_cost_usd:.3f}",
            detector="budget",
            matching_events=events[-config.trip_count :],
        )
    return LoopDecision()
