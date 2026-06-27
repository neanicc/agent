from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, TypeVar

from .config import LoopGuardConfig
from .decision import LoopDecision
from .detectors import budget, exact, pingpong, semantic
from .event import LoopEvent
from .exceptions import LoopDetectedError
from .storage import export_jsonl as write_jsonl
from .ui.terminal import pause_for_action, show_warning

F = TypeVar("F", bound=Callable)


class LoopGuard:
    def __init__(self, config: LoopGuardConfig | None = None):
        self.config = config or LoopGuardConfig()
        self._events: dict[str, deque[LoopEvent]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )
        self._all: list[LoopEvent] = []
        self._allowlisted: set[str] = set(self.config.allowlisted_tools)

    def observe(self, event: LoopEvent) -> LoopDecision:
        self._events[event.run_id].append(event)
        self._all.append(event)
        events = list(self._events[event.run_id])
        if event.tool_name in self._allowlisted:
            return LoopDecision(reason="allowlisted")
        for enabled, detector in [
            (self.config.enable_budget, lambda: budget.detect(events, self.config)),
            (self.config.enable_exact, lambda: exact.detect(events, self.config)),
            (self.config.enable_pingpong, lambda: pingpong.detect(events, self.config)),
            (self.config.enable_semantic, lambda: semantic.detect(events, self.config)),
        ]:
            if enabled:
                decision = detector()
                if decision.tripped:
                    return self._handle(decision)
        return LoopDecision()

    def _handle(self, decision: LoopDecision) -> LoopDecision:
        if self.config.action == "raise":
            raise LoopDetectedError(decision)
        if self.config.action == "warn":
            show_warning(decision)
            decision.allowed = True
            return decision
        decision = pause_for_action(decision)
        if decision.developer_action == "allowlist":
            for e in decision.matching_events:
                if e.tool_name:
                    self._allowlisted.add(e.tool_name)
        return decision

    def wrap_tool(self, tool_name: str, fn: F) -> F:
        def wrapped(*args, **kwargs):
            event = LoopEvent(
                run_id=kwargs.pop("run_id", "default"),
                agent=kwargs.pop("agent", "agent"),
                kind="tool_call",
                tool_name=tool_name,
                tool_args={"args": args, **kwargs},
            )
            decision = self.observe(event)
            if not decision.allowed:
                raise LoopDetectedError(decision)
            return fn(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    def reset(self, run_id: str | None = None) -> None:
        if run_id is None:
            self._events.clear()
            self._all.clear()
        else:
            self._events.pop(run_id, None)
            self._all = [e for e in self._all if e.run_id != run_id]

    def summary(self) -> dict[str, float | int]:
        return {
            "events": len(self._all),
            "tokens": sum(e.tokens for e in self._all),
            "cost_usd": sum(e.cost_usd for e in self._all),
        }

    def export_jsonl(self, path: str | Path) -> None:
        write_jsonl(self._all, path)

    @classmethod
    def from_config_file(cls, path: str | Path) -> "LoopGuard":
        return cls(LoopGuardConfig(**json.loads(Path(path).read_text())))
