from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agent import run_agent
from ..config import LoopGuardConfig
from ..decision import LoopDecision
from ..event import LoopEvent
from ..guard import LoopGuard
from ..tools import LIST_DIR_SCHEMA, READ_FILE_SCHEMA, list_dir, read_file
from .schemas import decision_message, event_message

SYSTEM = (
    "You are a JavaScript repo inspector. This is an npm project. Find and read the "
    "package.json file using the read_file tool. The file must be named package.json — "
    "keep trying different directory paths until you find it."
)
TASK = "Find package.json for this npm project."

PAUSE_TIMEOUT_S = 300


def _repo_root() -> Path:
    # src/loopguard/server/runs.py -> repo root is three parents up from src/loopguard.
    return Path(__file__).resolve().parents[3]


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
    return decision


@dataclass
class RunState:
    id: str
    mode: str
    model: str
    status: str = "running"
    events: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    pending: dict | None = None
    final_text: str | None = None
    stopped_by_guard: bool = False
    error: str | None = None


class Run:
    def __init__(self, id: str, mode: str, model: str, emit: Callable[[dict], None],
                 root: str | None = None):
        self.state = RunState(id=id, mode=mode, model=model)
        self._emit = emit
        self._root = Path(root) if root else _repo_root()
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

    def _on_observe(self, event: LoopEvent, decision: LoopDecision) -> None:
        self._step += 1
        totals = self._guard.summary() if self._guard else {}
        msg = event_message(event, decision, totals, self._step)
        self.state.events.append(msg["data"])
        self._emit(msg)

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
        self.state.pending = None
        self.state.status = "running"
        self._emit({"type": "status", "data": {
            "status": "running",
            "summary": self._guard.summary() if self._guard else {},
        }})
        return decision

    def execute(self, provider, judge) -> None:
        action = "pause" if self.state.mode == "flag" else "auto"
        self._guard = LoopGuard(
            LoopGuardConfig(action=action, max_tool_calls=15),
            judge=judge,
            on_observe=self._on_observe,
            on_pause=self._on_pause,
        )
        try:
            result = run_agent(
                provider,
                system=SYSTEM,
                task=TASK,
                tools_schema=[READ_FILE_SCHEMA, LIST_DIR_SCHEMA],
                tool_impls={
                    "read_file": lambda path: read_file(path, root=self._root),
                    "list_dir": lambda path=".": list_dir(path, root=self._root),
                },
                guard=self._guard,
                run_id=self.state.id,
                agent_name="repo-inspector",
                max_steps=15,
            )
            self.state.final_text = result.final_text
            self.state.stopped_by_guard = result.stopped_by_guard
            self.state.status = "stopped" if result.stopped_by_guard else "completed"
        except Exception as exc:  # noqa: BLE001 - surface run errors, never crash the server
            self.state.status = "error"
            self.state.error = str(exc)
            self._emit({"type": "error", "data": {"message": str(exc)}})
        finally:
            self.state.summary = self._guard.summary() if self._guard else {}
            self._emit({"type": "done", "data": {
                "summary": self.state.summary,
                "final_text": self.state.final_text,
                "stopped_by_guard": self.state.stopped_by_guard,
                "status": self.state.status,
            }})


class RunRegistry:
    def __init__(self):
        self._runs: dict[str, Run] = {}

    def create(self, id: str, mode: str, model: str, emit: Callable[[dict], None]) -> Run:
        run = Run(id=id, mode=mode, model=model, emit=emit)
        self._runs[id] = run
        return run

    def get(self, id: str) -> Run | None:
        return self._runs.get(id)

    def list(self) -> list[dict]:
        return [
            {"id": r.state.id, "mode": r.state.mode, "model": r.state.model,
             "status": r.state.status, "events": len(r.state.events),
             "summary": r.state.summary}
            for r in self._runs.values()
        ]
