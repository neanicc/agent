from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..agent import run_agent, run_multi_agent
from ..config import LoopGuardConfig
from ..decision import LoopDecision
from ..event import LoopEvent
from ..guard import LoopGuard
from .projects import Project, build_tools, get_project
from .schemas import (
    allowlist_message,
    auto_fix_message,
    decision_message,
    event_message,
)

PAUSE_TIMEOUT_S = 300


def apply_intervention(decision: LoopDecision, action: str, message: str | None) -> LoopDecision:
    if action == "terminate":
        decision.developer_action = "terminate"
        decision.allowed = False
    elif action == "approve":
        decision.developer_action = "inject"
        decision.allowed = True  # keep decision.suggested_message (the judge's fix)
    elif action == "inject":
        decision.developer_action = "inject"
        decision.allowed = True
        decision.suggested_message = message
    elif action == "continue_once":
        decision.developer_action = "continue_once"
        decision.allowed = True
        decision.suggested_message = None
    elif action == "allowlist":
        # Stop flagging this tool for the rest of the run; the engine adds the tool
        # behind the loop to its allowlist when it sees developer_action == "allowlist".
        decision.developer_action = "allowlist"
        decision.allowed = True
        decision.suggested_message = None
    return decision


@dataclass
class RunState:
    id: str
    project_id: str
    label: str
    kind: str
    mode: str
    model: str
    task: str
    agents: list[str] = field(default_factory=list)
    status: str = "running"
    events: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    pending: dict | None = None
    auto_actions: list[dict] = field(default_factory=list)
    allowlist_log: list[dict] = field(default_factory=list)
    allowlist: list[str] = field(default_factory=list)
    final_text: str | None = None
    stopped_by_guard: bool = False
    error: str | None = None


class Run:
    def __init__(self, id: str, project: Project, mode: str, model: str,
                 emit: Callable[[dict], None], task: str | None = None):
        self.project = project
        self.task = task or project.task
        self.state = RunState(
            id=id, project_id=project.id, label=project.label, kind=project.kind,
            mode=mode, model=model, task=self.task,
            agents=[a.name for a in project.agents],
        )
        self._emit = emit
        # Condition guards the cross-thread pause/resume handoff. `_awaiting` scopes a
        # response to exactly one pause round, so a duplicate/out-of-window intervention
        # (e.g. a double-tap, or REST + WS both delivering) cannot leak into a later one.
        self._cond = threading.Condition()
        self._response: dict | None = None
        self._awaiting = False
        self._step = 0
        self._guard: LoopGuard | None = None

    def intervene(self, action: str, message: str | None = None) -> None:
        with self._cond:
            if not self._awaiting:
                return  # ignore interventions when the run is not paused
            self._response = {"action": action, "message": message}
            self._awaiting = False
            self._cond.notify_all()

    def _totals(self) -> dict:
        return self._guard.summary() if self._guard else {}

    def _on_observe(self, event: LoopEvent, decision: LoopDecision) -> None:
        self._step += 1
        msg = event_message(event, decision, self._totals(), self._step)
        self.state.events.append(msg["data"])
        self._emit(msg)

    def _on_auto(self, decision: LoopDecision) -> LoopDecision:
        # Auto mode: apply the judge's fix with no human, and LOG it for the app.
        applied = bool(decision.suggested_message)
        if applied:
            decision.developer_action = "inject"
            decision.allowed = True
        else:
            decision.developer_action = "terminate"
            decision.allowed = False
        entry = auto_fix_message(decision, self._step + 1, applied)["data"]
        self.state.auto_actions.append(entry)
        self._emit(auto_fix_message(decision, self._step + 1, applied))
        return decision

    def _on_pause(self, decision: LoopDecision) -> LoopDecision:
        with self._cond:  # open the window before emitting, so a fast reply is never dropped
            self._awaiting = True
            self._response = None
        self.state.pending = decision_message(decision)["data"]
        self.state.status = "awaiting_decision"
        self._emit(decision_message(decision))
        with self._cond:
            ok = self._cond.wait_for(lambda: not self._awaiting, timeout=PAUSE_TIMEOUT_S)
            resp = self._response
            self._response = None
            self._awaiting = False
        if not ok or resp is None:
            decision.developer_action = "terminate"
            decision.allowed = False
        else:
            apply_intervention(decision, resp["action"], resp.get("message"))
            if resp["action"] == "allowlist":
                self._record_allowlist(decision)
        self.state.pending = None
        self.state.status = "running"
        self._emit({"type": "status", "data": {
            "status": "running",
            "summary": self._totals(),
        }})
        return decision

    def _record_allowlist(self, decision: LoopDecision) -> None:
        tools = sorted({e.tool_name for e in decision.matching_events if e.tool_name})
        # The engine adds these to its allowlist right after this handler returns; the
        # projected set is the current allowlist plus the tools behind this loop.
        current = self._guard.allowlisted_tools() if self._guard else []
        projected = sorted(set(current) | set(tools))
        self.state.allowlist = projected
        self.state.allowlist_log.append({
            "ts": time.time(),
            "tools": tools,
            "detector": decision.detector,
            "reason": decision.judge_reasoning or decision.reason,
        })
        self._emit(allowlist_message(tools, projected))

    def execute(self, provider, judge) -> None:
        action = "pause" if self.state.mode == "flag" else "auto"
        self._guard = LoopGuard(
            LoopGuardConfig(action=action, max_tool_calls=15),
            judge=judge,
            on_observe=self._on_observe,
            on_pause=self._on_pause,
            on_auto=self._on_auto,
        )
        schemas, impls = build_tools(self.project)
        try:
            if self.project.kind == "multi":
                result = run_multi_agent(
                    provider,
                    agents=[(a.name, a.system) for a in self.project.agents],
                    task=self.task,
                    tools_schema=schemas,
                    tool_impls=impls,
                    guard=self._guard,
                    run_id=self.state.id,
                    max_steps=self.project.max_steps,
                )
            else:
                agent = self.project.agents[0]
                result = run_agent(
                    provider,
                    system=agent.system,
                    task=self.task,
                    tools_schema=schemas,
                    tool_impls=impls,
                    guard=self._guard,
                    run_id=self.state.id,
                    agent_name=agent.name,
                    max_steps=self.project.max_steps,
                )
            self.state.final_text = result.final_text
            self.state.stopped_by_guard = result.stopped_by_guard
            self.state.status = "stopped" if result.stopped_by_guard else "completed"
        except Exception as exc:  # noqa: BLE001 - surface run errors, never crash the server
            self.state.status = "error"
            self.state.error = str(exc)
            self._emit({"type": "error", "data": {"message": str(exc)}})
        finally:
            self.state.summary = self._totals()
            self.state.allowlist = self._guard.allowlisted_tools() if self._guard else []
            self._emit({"type": "done", "data": {
                "summary": self.state.summary,
                "final_text": self.state.final_text,
                "stopped_by_guard": self.state.stopped_by_guard,
                "status": self.state.status,
            }})


class RunRegistry:
    def __init__(self):
        self._runs: dict[str, Run] = {}

    def create(self, id: str, project: Project, mode: str, model: str,
               emit: Callable[[dict], None], task: str | None = None) -> Run:
        run = Run(id=id, project=project, mode=mode, model=model, emit=emit, task=task)
        self._runs[id] = run
        return run

    def get(self, id: str) -> Run | None:
        return self._runs.get(id)

    def list(self) -> list[dict]:
        return [
            {"id": r.state.id, "project_id": r.state.project_id, "label": r.state.label,
             "kind": r.state.kind, "mode": r.state.mode, "model": r.state.model,
             "status": r.state.status, "events": len(r.state.events),
             "agents": r.state.agents, "summary": r.state.summary,
             "auto_fixes": len(r.state.auto_actions), "allowlist": r.state.allowlist}
            for r in self._runs.values()
        ]

    def allowlist(self) -> list[dict]:
        """Aggregate the operator's allowlist decisions across all runs (for the app)."""
        out: list[dict] = []
        for r in self._runs.values():
            for entry in r.state.allowlist_log:
                out.append({**entry, "run_id": r.state.id, "project_id": r.state.project_id,
                            "label": r.state.label})
        return sorted(out, key=lambda e: e["ts"], reverse=True)

    def autofixes(self) -> list[dict]:
        """Aggregate every auto-mode intervention across all runs (the auto-fix feed)."""
        out: list[dict] = []
        for r in self._runs.values():
            for entry in r.state.auto_actions:
                out.append({**entry, "run_id": r.state.id, "project_id": r.state.project_id,
                            "label": r.state.label})
        return out


# Re-exported so callers (and tests) can resolve projects without importing two modules.
__all__ = ["Run", "RunRegistry", "RunState", "apply_intervention", "get_project"]
