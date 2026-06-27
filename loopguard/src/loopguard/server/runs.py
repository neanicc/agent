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
        self._decision_event = threading.Event()
        self._response: dict | None = None
        self._guard: LoopGuard | None = None

    def intervene(self, action: str, message: str | None = None) -> None:
        self._response = {"action": action, "message": message}
        self._decision_event.set()

    def _on_observe(self, event: LoopEvent, decision: LoopDecision) -> None:
        totals = self._guard.summary() if self._guard else {}
        msg = event_message(event, decision, totals)
        self.state.events.append(msg["data"])
        self._emit(msg)

    def _on_pause(self, decision: LoopDecision) -> LoopDecision:
        self.state.status = "awaiting_decision"
        self.state.pending = decision_message(decision)["data"]
        self._emit(decision_message(decision))
        got = self._decision_event.wait(timeout=PAUSE_TIMEOUT_S)
        self._decision_event.clear()
        if not got or self._response is None:
            decision.developer_action = "terminate"
            decision.allowed = False
        else:
            apply_intervention(decision, self._response["action"], self._response.get("message"))
        self._response = None
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
