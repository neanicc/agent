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
from .ui.terminal import apply_auto, apply_flag, pause_for_action, show_warning

F = TypeVar("F", bound=Callable)


class LoopGuard:
    def __init__(self, config: LoopGuardConfig | None = None, judge=None):
        self.config = config or LoopGuardConfig()
        self.judge = judge
        self._events: dict[str, deque[LoopEvent]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )
        self._all: list[LoopEvent] = []
        self._allowlisted: set[str] = set(self.config.allowlisted_tools)
        self._judge_cost: float = 0.0
        self._judge_cache: dict[tuple[str, str | None], object] = {}

    def observe(self, event: LoopEvent, task: str | None = None) -> LoopDecision:
        self._events[event.run_id].append(event)
        self._all.append(event)
        events = list(self._events[event.run_id])
        if event.tool_name in self._allowlisted:
            return LoopDecision(reason="allowlisted")
        # Hard total-spend ceiling (includes judge cost). A real cap the judge may NOT override.
        ceiling = self._cost_ceiling(event.run_id)
        if ceiling is not None:
            return self._handle(ceiling)
        for enabled, detector in [
            (self.config.enable_budget, lambda: budget.detect(events, self.config)),
            (self.config.enable_exact, lambda: exact.detect(events, self.config)),
            (self.config.enable_pingpong, lambda: pingpong.detect(events, self.config)),
            (self.config.enable_semantic, lambda: semantic.detect(events, self.config)),
        ]:
            if enabled:
                decision = detector()
                if decision.tripped:
                    # Budget is a hard resource cap: never let the judge wave it away.
                    if decision.detector == "budget":
                        return self._handle(decision)
                    decision = self._consult_judge(decision, task)
                    if not decision.tripped:
                        return decision
                    return self._handle(decision)
        return LoopDecision()

    def _cost_ceiling(self, run_id: str) -> LoopDecision | None:
        if not self.config.enable_budget or self.config.max_cost_usd is None:
            return None
        total = sum(e.cost_usd for e in self._all if e.run_id == run_id) + self._judge_cost
        if total > self.config.max_cost_usd:
            tail = list(self._events[run_id])[-self.config.trip_count :]
            return LoopDecision(
                allowed=False,
                tripped=True,
                reason=f"Total cost budget exceeded: ${total:.4f}>${self.config.max_cost_usd:.4f}",
                detector="budget",
                matching_events=tail,
            )
        return None

    def _consult_judge(self, decision: LoopDecision, task: str | None) -> LoopDecision:
        if not (self.config.enable_judge and self.judge is not None):
            return decision
        run_id = decision.matching_events[0].run_id if decision.matching_events else "default"
        key = (run_id, decision.detector)
        # Judge once per (run, detector): an ongoing loop must not re-bill the judge each step.
        verdict = self._judge_cache.get(key)
        if verdict is None:
            verdict = self.judge.judge(
                decision.matching_events, task=task, detector=decision.detector
            )
            self._judge_cost += verdict.cost_usd
            self._judge_cache[key] = verdict
        if not verdict.is_loop:
            return LoopDecision(
                allowed=True,
                tripped=False,
                reason="judge: false positive suppressed",
                detector=decision.detector,
                judged=True,
                judge_reasoning=verdict.reasoning,
                judge_confidence=verdict.confidence,
            )
        decision.judged = True
        decision.judge_reasoning = verdict.reasoning
        decision.judge_confidence = verdict.confidence
        if verdict.suggested_correction:
            decision.suggested_message = verdict.suggested_correction
        return decision

    def _handle(self, decision: LoopDecision) -> LoopDecision:
        if self.config.action == "raise":
            raise LoopDetectedError(decision)
        if self.config.action == "warn":
            show_warning(decision)
            decision.allowed = True
            return decision
        if self.config.action == "flag":
            return apply_flag(decision)
        if self.config.action == "auto":
            return apply_auto(decision)
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
            self._judge_cache.clear()
            self._judge_cost = 0.0
        else:
            self._events.pop(run_id, None)
            self._all = [e for e in self._all if e.run_id != run_id]
            self._judge_cache = {k: v for k, v in self._judge_cache.items() if k[0] != run_id}

    def summary(self) -> dict[str, float | int]:
        return {
            "events": len(self._all),
            "tokens": sum(e.tokens for e in self._all),
            "cost_usd": sum(e.cost_usd for e in self._all),
            "judge_cost_usd": self._judge_cost,
        }

    def export_jsonl(self, path: str | Path) -> None:
        write_jsonl(self._all, path)

    @classmethod
    def from_config_file(cls, path: str | Path) -> "LoopGuard":
        return cls(LoopGuardConfig(**json.loads(Path(path).read_text())))
