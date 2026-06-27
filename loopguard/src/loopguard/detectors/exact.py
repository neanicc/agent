from __future__ import annotations

from loopguard.config import LoopGuardConfig
from loopguard.decision import LoopDecision
from loopguard.event import LoopEvent
from loopguard.normalize import exact_signature


def detect(events: list[LoopEvent], config: LoopGuardConfig) -> LoopDecision:
    n = config.exact_threshold or config.trip_count
    if len(events) < n:
        return LoopDecision()
    tail = events[-n:]
    sigs = [exact_signature(e, config) for e in tail]
    if len(set(sigs)) == 1:
        return LoopDecision(
            allowed=False,
            tripped=True,
            reason=f"Exact loop detected across last {n} events",
            detector="exact",
            similarity=1.0,
            matching_events=tail,
        )
    return LoopDecision()
