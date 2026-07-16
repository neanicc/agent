from __future__ import annotations

import json
from collections import defaultdict, deque
from decimal import Decimal
from pathlib import Path
from typing import Callable, TypeVar

from .config import LoopGuardConfig
from .decision import LoopDecision
from .detectors import budget, exact, pingpong, semantic
from .event import LoopEvent
from .exceptions import LoopDetectedError
from .router.budgets import BudgetReservation
from .router.judge_cache import IncidentCacheKey, JudgeIncidentCache, incident_cache_key
from .storage import export_jsonl as write_jsonl
from .ui.terminal import apply_auto, apply_flag, pause_for_action, show_warning

F = TypeVar("F", bound=Callable)


class LoopGuard:
    def __init__(
        self,
        config: LoopGuardConfig | None = None,
        judge=None,
        on_observe=None,
        on_pause=None,
        on_auto=None,
        budget_callback: Callable[[str, Decimal], BudgetReservation] | None = None,
        judge_cache: JudgeIncidentCache | None = None,
    ):
        self.config = config or LoopGuardConfig()
        self.judge = judge
        self.on_observe = on_observe
        self.on_pause = on_pause
        self.on_auto = on_auto
        self.budget_callback = budget_callback
        self._events: dict[str, deque[LoopEvent]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )
        self._all: list[LoopEvent] = []
        self._allowlisted: set[str] = set(self.config.allowlisted_tools)
        self._judge_cost: float = 0.0
        self._judge_cache = judge_cache or JudgeIncidentCache(
            ttl_seconds=self.config.judge_cache_ttl_seconds,
            max_entries=self.config.judge_cache_max_entries,
        )
        self._active_incidents: dict[tuple[str, str | None], IncidentCacheKey] = {}

    def observe(self, event: LoopEvent, task: str | None = None) -> LoopDecision:
        decision = self._evaluate(event, task)
        if self.on_observe is not None:
            self.on_observe(event, decision)
        return decision

    def _evaluate(self, event: LoopEvent, task: str | None = None) -> LoopDecision:
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
        self._clear_active_incidents(event.run_id)
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
        active_key = (run_id, decision.detector)
        key = self._active_incidents.get(active_key)
        if key is None:
            model = getattr(self.judge, "model", None)
            if not isinstance(model, str) or not model.strip():
                provider = getattr(self.judge, "provider", None)
                model = getattr(provider, "model", self.judge.__class__.__name__)
            key = incident_cache_key(
                policy_version=self.config.judge_policy_version,
                detector_kind=decision.detector or "unknown",
                events=decision.matching_events,
                judge_model=str(model),
                prompt_version=self.config.judge_prompt_version,
                task=task,
                context=getattr(self.judge, "context", None),
                normalization_config=self.config,
            )
            self._active_incidents[active_key] = key

        def compute():
            if self.budget_callback is not None:
                try:
                    reservation = self.budget_callback("judge", self.config.judge_reservation_usd)
                except Exception as exc:
                    raise _JudgeBudgetDenied("budget_callback_failed") from exc
                if not reservation.allowed:
                    raise _JudgeBudgetDenied(reservation.reason)
            return self.judge.judge(decision.matching_events, task=task, detector=decision.detector)

        try:
            result = self._judge_cache.get_or_compute(key, compute)
        except _JudgeBudgetDenied as exc:
            decision.judge_reasoning = f"judge skipped: {exc.reason}"
            return decision
        except Exception:
            decision.judge_reasoning = "judge unavailable; detector decision retained"
            return decision
        verdict = result.verdict
        if result.computed:
            self._judge_cost += verdict.cost_usd
        if not verdict.is_loop:
            self._active_incidents.pop(active_key, None)
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
            if self.on_auto is not None:  # non-terminal front-end (e.g. cloud server)
                return self.on_auto(decision)
            return apply_auto(decision)
        handler = self.on_pause or pause_for_action
        decision = handler(decision)
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
            self._active_incidents.clear()
            self._judge_cache.clear()
            self._judge_cost = 0.0
        else:
            self._events.pop(run_id, None)
            self._all = [e for e in self._all if e.run_id != run_id]
            self._clear_active_incidents(run_id)

    def allowlisted_tools(self) -> list[str]:
        return sorted(self._allowlisted)

    def summary(self) -> dict[str, float | int]:
        return {
            "events": len(self._all),
            "tokens": sum(e.tokens for e in self._all),
            "cost_usd": sum(e.cost_usd for e in self._all),
            "judge_cost_usd": self._judge_cost,
        }

    def export_jsonl(self, path: str | Path) -> None:
        write_jsonl(self._all, path)

    def _clear_active_incidents(self, run_id: str) -> None:
        self._active_incidents = {
            key: value for key, value in self._active_incidents.items() if key[0] != run_id
        }

    @classmethod
    def from_config_file(cls, path: str | Path) -> "LoopGuard":
        return cls(LoopGuardConfig(**json.loads(Path(path).read_text())))


class _JudgeBudgetDenied(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
