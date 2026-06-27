# LoopGuard Cloud (Mobile App + Server) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React Native (Expo) "mission control" app that monitors a real LoopGuard-guarded agent live and lets a human intervene (terminate / approve the judge's fix / send a custom prompt / ignore once), backed by a local FastAPI server wrapping the real engine.

**Architecture:** Two tiny engine hooks (`on_observe`, `on_pause`) let a non-terminal front-end stream a run and control a loop-trip. A FastAPI server runs the existing live agent in a background thread, streams events + judge verdicts over a WebSocket, and resumes a paused run when the app sends an intervention. The Expo app renders the feed, a cost meter, and a decision card.

**Tech Stack:** Python (FastAPI, uvicorn, pydantic v2, existing loopguard engine, pytest, Starlette TestClient); TypeScript + Expo (React Native, native WebSocket, AsyncStorage, @expo-google-fonts, jest-expo).

## Global Constraints

- Python source under `loopguard/src/loopguard/`, tests under `loopguard/tests/` (pytest `pythonpath = ["src"]`). Expo app under `cloud-app/` (its own package.json; NOT inside the Python package).
- The core engine (`detectors/`, `judge.py`, `guard.py`, `providers/`) must stay free of terminal/print code; only `ui/terminal.py`, `cli.py`, and demo modules print. The server module must not print to stdout except via uvicorn/logging.
- New optional extra (verbatim): `server = ["fastapi>=0.110", "uvicorn[standard]>=0.29"]`.
- Single backend test command: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest <path>::<name> -v`. Full suite: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/ -q`. Reinstall after structural changes: `python3 -m pip install -e ".[dev,server]" -q`.
- App commands run from `cloud-app/`: typecheck `npx tsc --noEmit`; unit tests `npm test`; web bundle `npx expo export --platform web`.
- WebSocket protocol is the backend⇄app contract — message `type` strings and `data` field names must match exactly between Task 4 (server) and Tasks 6–7 (app): server→client types `event`, `decision_required`, `status`, `done`, `error`; client→server type `intervene` with `action` in `terminate|approve|inject|continue_once`.
- Action-mode mapping: app "flag" → guard `action="pause"` + server `on_pause`; app "auto" → guard `action="auto"`.
- All backend tests run with NO API key and NO network (inject fake providers/judges). The native mobile UI is verified by the user, not in CI.
- Commit footer (both lines) on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Kegf7sKtfWgJ93DkThr1Lk
  ```

## File Structure

**Create (backend):**
- `src/loopguard/server/__init__.py` — exports `create_app`.
- `src/loopguard/server/schemas.py` — `StartRunRequest`, `InterveneRequest`, message builders.
- `src/loopguard/server/runs.py` — `RunState`, `Run` (orchestration, pause/resume), `RunRegistry`, `apply_intervention`, scenario constants.
- `src/loopguard/server/app.py` — `create_app()`: FastAPI routes + WebSocket + thread-safe emit.
- Tests: `tests/test_engine_hooks.py`, `tests/test_server_runs.py`, `tests/test_server_app.py`.

**Modify (backend):**
- `src/loopguard/guard.py` — add `on_observe`/`on_pause`; fire `on_observe` on every `observe()`; use `on_pause` in `_handle`.
- `src/loopguard/cli.py` — add `serve` command.
- `pyproject.toml` — add `server` extra.

**Create (app, under `cloud-app/`):**
- `package.json`, `app.json`, `tsconfig.json`, `babel.config.js`, `jest.config.js`.
- `App.tsx` — root screen state machine.
- `src/theme.ts` — colors, spacing, typography tokens.
- `src/client.ts` — `LoopGuardClient` (fetch + WebSocket wrapper).
- `src/runReducer.ts` — pure state reducer over WS messages (unit-tested).
- `src/screens/ConnectScreen.tsx`, `src/screens/StartScreen.tsx`, `src/screens/MissionControlScreen.tsx`.
- `src/components/EventRow.tsx`, `src/components/DecisionCard.tsx`, `src/components/Meter.tsx`.
- `src/__tests__/runReducer.test.ts`.

---

### Task 1: Engine hooks (`on_observe`, `on_pause`)

**Files:**
- Modify: `src/loopguard/guard.py`
- Test: `tests/test_engine_hooks.py`

**Interfaces:**
- Consumes: existing `LoopGuard`, `LoopEvent`, `LoopDecision`.
- Produces:
  - `LoopGuard(config=None, judge=None, on_observe=None, on_pause=None)`.
  - `on_observe: Callable[[LoopEvent, LoopDecision], None]` fires after every `observe()`.
  - `on_pause: Callable[[LoopDecision], LoopDecision]` replaces `pause_for_action` when `action == "pause"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_hooks.py
from loopguard import LoopGuard, LoopGuardConfig
from loopguard.judge import JudgeVerdict

from conftest import tool_event


def test_on_observe_fires_for_every_event():
    seen = []
    guard = LoopGuard(
        LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False),
        on_observe=lambda e, d: seen.append((e.tool_name, d.tripped)),
    )
    guard.observe(tool_event("a"))
    guard.observe(tool_event("b"))
    assert len(seen) == 2
    assert seen[0] == ("read_file", False)


class _StubJudge:
    def judge(self, events, task=None, detector=None):
        return JudgeVerdict(is_loop=True, reasoning="stuck", suggested_correction="fix it")


def test_on_pause_overrides_terminal_and_is_honored():
    captured = {}

    def fake_pause(decision):
        captured["reason"] = decision.judge_reasoning
        decision.developer_action = "terminate"
        decision.allowed = False
        return decision

    guard = LoopGuard(
        LoopGuardConfig(action="pause", enable_exact=False, enable_budget=False),
        judge=_StubJudge(),
        on_pause=fake_pause,
    )
    d = None
    for path in ["./package.json", "package.json", "/app/package.json"]:
        d = guard.observe(tool_event(path), task="t")
    assert captured["reason"] == "stuck"  # on_pause saw the judged decision
    assert d.allowed is False and d.developer_action == "terminate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_engine_hooks.py -v`
Expected: FAIL (`LoopGuard.__init__` has no `on_observe`/`on_pause`).

- [ ] **Step 3: Write minimal implementation**

In `src/loopguard/guard.py`, change `__init__` signature and body to add the hooks:
```python
    def __init__(self, config: LoopGuardConfig | None = None, judge=None,
                 on_observe=None, on_pause=None):
        self.config = config or LoopGuardConfig()
        self.judge = judge
        self.on_observe = on_observe
        self.on_pause = on_pause
        self._events: dict[str, deque[LoopEvent]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )
        self._all: list[LoopEvent] = []
        self._allowlisted: set[str] = set(self.config.allowlisted_tools)
        self._judge_cost: float = 0.0
        self._judge_cache: dict[tuple[str, str | None], object] = {}
```

Rename the current `observe` body to `_evaluate`, and add a thin `observe` that fires the hook. Replace the existing `def observe(self, event, task=None):` line with `def _evaluate(self, event, task=None) -> LoopDecision:` (keep its body unchanged), then add directly above it:
```python
    def observe(self, event: LoopEvent, task: str | None = None) -> LoopDecision:
        decision = self._evaluate(event, task)
        if self.on_observe is not None:
            self.on_observe(event, decision)
        return decision
```

In `_handle`, replace the pause line `decision = pause_for_action(decision)` with:
```python
        handler = self.on_pause or pause_for_action
        decision = handler(decision)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_engine_hooks.py tests/ -q`
Expected: PASS (all green, existing tests unaffected).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/guard.py loopguard/tests/test_engine_hooks.py && git commit -m "feat(guard): on_observe/on_pause hooks for non-terminal front-ends"
```

---

### Task 2: Server schemas + intervention mapping

**Files:**
- Create: `src/loopguard/server/__init__.py`, `src/loopguard/server/schemas.py`
- Test: `tests/test_server_runs.py` (created here, expanded in Task 3)

**Interfaces:**
- Produces:
  - `StartRunRequest(mode: Literal["flag","auto"]="flag", model: str="cerebras/gpt-oss-120b", provider: str="auto")` (pydantic).
  - `InterveneRequest(action: Literal["terminate","approve","inject","continue_once"], message: str | None = None)` (pydantic).
  - `event_message(event, decision, totals) -> dict`, `decision_message(decision) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server_runs.py
from loopguard.server.schemas import StartRunRequest, InterveneRequest, decision_message
from loopguard.decision import LoopDecision


def test_start_run_defaults():
    r = StartRunRequest()
    assert r.mode == "flag" and r.model == "cerebras/gpt-oss-120b" and r.provider == "auto"


def test_intervene_request_validates_action():
    assert InterveneRequest(action="approve").message is None
    assert InterveneRequest(action="inject", message="do x").message == "do x"


def test_decision_message_shape():
    d = LoopDecision(
        tripped=True, detector="semantic", similarity=0.94, reason="loop",
        judged=True, judge_reasoning="stuck", judge_confidence=0.9,
        suggested_message="read pyproject.toml",
    )
    msg = decision_message(d)
    assert msg["type"] == "decision_required"
    assert msg["data"]["detector"] == "semantic"
    assert msg["data"]["judge_reasoning"] == "stuck"
    assert msg["data"]["suggested_message"] == "read pyproject.toml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_server_runs.py -v`
Expected: FAIL (`loopguard.server` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/server/__init__.py
from .app import create_app

__all__ = ["create_app"]
```

```python
# src/loopguard/server/schemas.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..decision import LoopDecision
from ..event import LoopEvent


class StartRunRequest(BaseModel):
    mode: Literal["flag", "auto"] = "flag"
    model: str = "cerebras/gpt-oss-120b"
    provider: str = "auto"


class InterveneRequest(BaseModel):
    action: Literal["terminate", "approve", "inject", "continue_once"]
    message: str | None = None


def event_message(event: LoopEvent, decision: LoopDecision, totals: dict) -> dict:
    is_err = bool(event.error)
    return {
        "type": "event",
        "data": {
            "tool": event.tool_name,
            "args": event.tool_args or {},
            "output": (event.error or event.output_text or "")[:300],
            "is_error": is_err,
            "tokens": event.tokens,
            "cost_usd": event.cost_usd,
            "total_tokens": totals.get("tokens", 0),
            "total_cost": totals.get("cost_usd", 0.0),
            "tripped": decision.tripped,
        },
    }


def decision_message(decision: LoopDecision) -> dict:
    return {
        "type": "decision_required",
        "data": {
            "detector": decision.detector,
            "similarity": decision.similarity,
            "reason": decision.reason,
            "judge_reasoning": decision.judge_reasoning,
            "judge_confidence": decision.judge_confidence,
            "suggested_message": decision.suggested_message,
        },
    }
```

Note: `create_app` does not exist yet, so importing `loopguard.server` would fail. For this task, make `__init__.py` import lazily — replace its body with:
```python
# src/loopguard/server/__init__.py
__all__ = ["create_app"]


def create_app(*args, **kwargs):  # lazy import; real impl arrives in Task 4
    from .app import create_app as _factory

    return _factory(*args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_server_runs.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/server/__init__.py loopguard/src/loopguard/server/schemas.py loopguard/tests/test_server_runs.py && git commit -m "feat(server): request/message schemas for the cloud API"
```

---

### Task 3: Run orchestration (`runs.py`) — pause/resume + interventions

**Files:**
- Create: `src/loopguard/server/runs.py`
- Test: `tests/test_server_runs.py` (extend)

**Interfaces:**
- Consumes: `LoopGuard` + hooks (Task 1), `run_agent` (existing), `read_file`/`list_dir` + schemas (`READ_FILE_SCHEMA`, `LIST_DIR_SCHEMA`), `event_message`/`decision_message` (Task 2).
- Produces:
  - `RunState` dataclass: `id, mode, model, status, events:list[dict], summary:dict, pending:dict|None, final_text:str|None, stopped_by_guard:bool, error:str|None`.
  - `Run(id, mode, model, emit, root=None)` with `.state`, `.intervene(action, message=None)`, `.execute(provider, judge)`.
  - `RunRegistry()` with `.create(id, mode, model, emit)`, `.get(id)`, `.list()`.
  - `apply_intervention(decision, action, message)`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_server_runs.py
import threading
import time

from loopguard.judge import JudgeVerdict
from loopguard.providers.base import LLMResult
from loopguard.server.runs import Run, RunRegistry, apply_intervention
from loopguard.decision import LoopDecision


class _TC:
    def __init__(self, i):
        self.id = f"c{i}"
        self.type = "function"
        self.function = type("F", (), {"name": "read_file",
                                       "arguments": '{"path": "package.json"}'})()


class _LoopProvider:
    model = "fake/model"

    def __init__(self):
        self.n = 0

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512,
                 response_format=None):
        # Stop once a correction was injected as a user message.
        for m in messages[2:]:
            if m.get("role") == "user" and "STOP" in str(m.get("content", "")):
                return LLMResult(text="done", prompt_tokens=1, completion_tokens=1, total_tokens=2)
        self.n += 1
        return LLMResult(text="", tool_calls=[_TC(self.n)], prompt_tokens=10,
                         completion_tokens=2, total_tokens=12, cost_usd=0.001)


class _Judge:
    def judge(self, events, task=None, detector=None):
        return JudgeVerdict(is_loop=True, reasoning="stuck", suggested_correction="STOP now",
                            cost_usd=0.0005)


def test_apply_intervention_maps_actions():
    d = LoopDecision(tripped=True, suggested_message="judge fix")
    apply_intervention(d, "approve", None)
    assert d.allowed and d.developer_action == "inject" and d.suggested_message == "judge fix"

    d2 = LoopDecision(tripped=True)
    apply_intervention(d2, "inject", "custom")
    assert d2.allowed and d2.suggested_message == "custom"

    d3 = LoopDecision(tripped=True)
    apply_intervention(d3, "terminate", None)
    assert d3.allowed is False and d3.developer_action == "terminate"

    d4 = LoopDecision(tripped=True, suggested_message="x")
    apply_intervention(d4, "continue_once", None)
    assert d4.allowed and d4.suggested_message is None


def test_flag_run_pauses_then_resumes_on_approve(tmp_path):
    messages = []
    run = Run(id="r1", mode="flag", model="fake/model", emit=messages.append, root=str(tmp_path))
    t = threading.Thread(target=run.execute, args=(_LoopProvider(), _Judge()), daemon=True)
    t.start()

    # Wait until the run is awaiting a decision.
    for _ in range(100):
        if run.state.status == "awaiting_decision":
            break
        time.sleep(0.02)
    assert run.state.status == "awaiting_decision"
    assert any(m["type"] == "decision_required" for m in messages)

    run.intervene("approve")  # inject the judge's "STOP now" correction
    t.join(timeout=5)
    assert run.state.status in ("completed", "stopped")
    assert any(m["type"] == "done" for m in messages)


def test_terminate_stops_the_run(tmp_path):
    run = Run(id="r2", mode="flag", model="fake/model", emit=lambda m: None, root=str(tmp_path))
    t = threading.Thread(target=run.execute, args=(_LoopProvider(), _Judge()), daemon=True)
    t.start()
    for _ in range(100):
        if run.state.status == "awaiting_decision":
            break
        time.sleep(0.02)
    run.intervene("terminate")
    t.join(timeout=5)
    assert run.state.stopped_by_guard is True
    assert run.state.status == "stopped"


def test_registry_create_get_list():
    reg = RunRegistry()
    run = reg.create("abc", "auto", "m", emit=lambda m: None)
    assert reg.get("abc") is run
    assert reg.get("missing") is None
    assert any(s["id"] == "abc" for s in reg.list())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_server_runs.py -v`
Expected: FAIL (`loopguard.server.runs` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/server/runs.py
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
        self._emit({"type": "status", "data": {"status": "running",
                                               "summary": self._guard.summary() if self._guard else {}}})
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_server_runs.py -v`
Expected: PASS (all run-orchestration tests green).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/server/runs.py loopguard/tests/test_server_runs.py && git commit -m "feat(server): run orchestration with pause/resume + interventions"
```

---

### Task 4: FastAPI app + WebSocket + `loopguard serve`

**Files:**
- Create: `src/loopguard/server/app.py`
- Modify: `src/loopguard/server/__init__.py` (point to real factory), `src/loopguard/cli.py`, `pyproject.toml`
- Test: `tests/test_server_app.py`

**Interfaces:**
- Consumes: `RunRegistry`/`Run` (Task 3), `StartRunRequest`/`InterveneRequest` (Task 2), `make_provider`/`LLMJudge` (existing).
- Produces: `create_app(provider_factory=None, judge_factory=None) -> FastAPI`. Factories default to the real `make_provider` / `LLMJudge`; tests inject fakes. Routes: `GET /health`, `POST /runs`, `GET /runs`, `GET /runs/{id}`, `POST /runs/{id}/intervene`, `WS /runs/{id}/ws`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server_app.py
from fastapi.testclient import TestClient

from loopguard.providers.base import LLMResult
from loopguard.judge import JudgeVerdict
from loopguard.server.app import create_app


class _QuickProvider:
    """Returns a final answer immediately: a fast, no-loop run for transport tests."""

    model = "fake/model"

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512,
                 response_format=None):
        return LLMResult(text="Found it.", prompt_tokens=5, completion_tokens=2, total_tokens=7)


class _Judge:
    def judge(self, events, task=None, detector=None):
        return JudgeVerdict(is_loop=True, reasoning="x")


def _app():
    return create_app(provider_factory=lambda model, provider: _QuickProvider(),
                      judge_factory=lambda provider: _Judge())


def test_health():
    client = TestClient(_app())
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_start_run_returns_id_and_lists():
    client = TestClient(_app())
    r = client.post("/runs", json={"mode": "auto", "model": "fake/model"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert client.get(f"/runs/{run_id}").status_code == 200
    assert any(s["id"] == run_id for s in client.get("/runs").json())


def test_unknown_run_404():
    client = TestClient(_app())
    assert client.get("/runs/nope").status_code == 404


def test_websocket_streams_until_done():
    client = TestClient(_app())
    with client.websocket_connect("/runs/auto-run/ws?start=auto&model=fake/model") as ws:
        types = []
        for _ in range(20):
            msg = ws.receive_json()
            types.append(msg["type"])
            if msg["type"] == "done":
                break
        assert "event" in types
        assert types[-1] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pip install -e ".[dev,server]" -q && python3 -m pytest tests/test_server_app.py -v`
Expected: FAIL (`create_app` not implemented / import error).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/server/app.py
from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from ..judge import LLMJudge
from ..providers import make_provider
from .runs import Run, RunRegistry
from .schemas import InterveneRequest, StartRunRequest


def create_app(provider_factory: Callable | None = None,
               judge_factory: Callable | None = None) -> FastAPI:
    app = FastAPI(title="LoopGuard Cloud")
    registry = RunRegistry()
    subscribers: dict[str, set[WebSocket]] = {}
    state: dict = {"loop": None}

    make_prov = provider_factory or (lambda model, provider: make_provider(model, provider))
    make_judge = judge_factory or (lambda provider: LLMJudge(provider))

    @app.on_event("startup")
    async def _capture_loop():
        state["loop"] = asyncio.get_running_loop()

    def emit_for(run_id: str) -> Callable[[dict], None]:
        # Thread-safe: schedule the broadcast on the app's event loop.
        def emit(message: dict) -> None:
            loop = state["loop"]
            if loop is None:
                return
            asyncio.run_coroutine_threadsafe(_broadcast(run_id, message), loop)
        return emit

    async def _broadcast(run_id: str, message: dict) -> None:
        for ws in list(subscribers.get(run_id, set())):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - drop dead sockets
                subscribers.get(run_id, set()).discard(ws)

    def _spawn(run_id: str, req: StartRunRequest) -> Run:
        provider = make_prov(req.model, req.provider)  # may raise -> caller maps to 400
        judge = make_judge(provider)
        run = registry.create(run_id, req.mode, req.model, emit_for(run_id))
        threading.Thread(target=run.execute, args=(provider, judge), daemon=True).start()
        return run

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/runs")
    async def start_run(req: StartRunRequest):
        run_id = uuid.uuid4().hex[:12]
        try:
            _spawn(run_id, req)
        except Exception as exc:  # noqa: BLE001 - bad key/provider -> 400
            raise HTTPException(status_code=400, detail=str(exc))
        return {"run_id": run_id}

    @app.get("/runs")
    async def list_runs():
        return registry.list()

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        s = run.state
        return {
            "id": s.id, "mode": s.mode, "model": s.model, "status": s.status,
            "events": s.events, "summary": s.summary, "pending": s.pending,
            "final_text": s.final_text, "stopped_by_guard": s.stopped_by_guard,
            "error": s.error,
        }

    @app.post("/runs/{run_id}/intervene")
    async def intervene(run_id: str, req: InterveneRequest):
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        run.intervene(req.action, req.message)
        return {"ok": True}

    @app.websocket("/runs/{run_id}/ws")
    async def run_ws(ws: WebSocket, run_id: str, start: str | None = None,
                     model: str = "cerebras/gpt-oss-120b"):
        await ws.accept()
        subscribers.setdefault(run_id, set()).add(ws)
        # Optionally start a run on connect (used by the app's one-shot flow / tests).
        if start in ("flag", "auto") and registry.get(run_id) is None:
            try:
                _spawn(run_id, StartRunRequest(mode=start, model=model))
            except Exception as exc:  # noqa: BLE001
                await ws.send_json({"type": "error", "data": {"message": str(exc)}})
        else:
            run = registry.get(run_id)
            if run is not None:
                for ev in run.state.events:  # replay current state on (re)connect
                    await ws.send_json({"type": "event", "data": ev})
                if run.state.pending is not None:
                    await ws.send_json({"type": "decision_required", "data": run.state.pending})
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "intervene":
                    run = registry.get(run_id)
                    if run is not None:
                        run.intervene(msg.get("action"), msg.get("message"))
        except WebSocketDisconnect:
            subscribers.get(run_id, set()).discard(ws)
        except Exception:  # noqa: BLE001 - ignore malformed client messages, keep socket
            subscribers.get(run_id, set()).discard(ws)

    return app
```

Update `src/loopguard/server/__init__.py`:
```python
from .app import create_app

__all__ = ["create_app"]
```

Add the `serve` command to `src/loopguard/cli.py` (after `init_config`):
```python
@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn

    from .server import create_app

    uvicorn.run(create_app(), host=host, port=port)
```

Add the extra to `pyproject.toml` optional-dependencies:
```toml
server = ["fastapi>=0.110", "uvicorn[standard]>=0.29"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pip install -e ".[dev,server]" -q && python3 -m pytest tests/test_server_app.py tests/ -q`
Expected: PASS (all green).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/server/app.py loopguard/src/loopguard/server/__init__.py loopguard/src/loopguard/cli.py loopguard/pyproject.toml loopguard/tests/test_server_app.py && git commit -m "feat(server): FastAPI app, WebSocket stream, and loopguard serve"
```

---

### Task 5: Expo app scaffold, theme, and pure run reducer (unit-tested)

**Files:**
- Create: `cloud-app/package.json`, `cloud-app/app.json`, `cloud-app/tsconfig.json`, `cloud-app/babel.config.js`, `cloud-app/jest.config.js`, `cloud-app/src/theme.ts`, `cloud-app/src/runReducer.ts`, `cloud-app/src/__tests__/runReducer.test.ts`

**Interfaces:**
- Produces:
  - `RunUiState` type + `initialRunState` + `runReducer(state, message) -> RunUiState`. `message` is a server WS message `{type, data}`. The reducer accumulates events, updates totals, sets `pending` on `decision_required`, clears it on `status`/`done`, sets `status`/`final`/`error`.

- [ ] **Step 1: Write the failing test**

```ts
// cloud-app/src/__tests__/runReducer.test.ts
import { initialRunState, runReducer } from "../runReducer";

test("accumulates events and totals", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "event", data: { tool: "read_file", output: "x", is_error: true, total_tokens: 12, total_cost: 0.001 } });
  s = runReducer(s, { type: "event", data: { tool: "read_file", output: "y", is_error: true, total_tokens: 24, total_cost: 0.002 } });
  expect(s.events.length).toBe(2);
  expect(s.totalTokens).toBe(24);
  expect(s.totalCost).toBeCloseTo(0.002);
});

test("sets and clears the pending decision", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "decision_required", data: { detector: "semantic", judge_reasoning: "stuck", suggested_message: "fix" } });
  expect(s.pending?.judge_reasoning).toBe("stuck");
  expect(s.status).toBe("awaiting_decision");
  s = runReducer(s, { type: "status", data: { status: "running" } });
  expect(s.pending).toBeNull();
  expect(s.status).toBe("running");
});

test("done sets final summary and status", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "done", data: { status: "completed", final_text: "done", summary: { events: 3 } } });
  expect(s.status).toBe("completed");
  expect(s.finalText).toBe("done");
});
```

- [ ] **Step 2: Scaffold + run test to verify it fails**

Run:
```bash
cd /Users/ayanbinsaif/agent/cloud-app && npm install
npm test
```
Expected: FAIL (`../runReducer` missing). (If `npm install` is needed first, the scaffold files below must exist before this step — create them in Step 3, then run.)

- [ ] **Step 3: Write the scaffold + implementation**

`cloud-app/package.json`:
```json
{
  "name": "loopguard-cloud",
  "version": "1.0.0",
  "main": "node_modules/expo/AppEntry.js",
  "scripts": {
    "start": "expo start",
    "ios": "expo start --ios",
    "web": "expo start --web",
    "test": "jest"
  },
  "dependencies": {
    "expo": "~51.0.0",
    "expo-status-bar": "~1.12.0",
    "react": "18.2.0",
    "react-native": "0.74.5",
    "@react-native-async-storage/async-storage": "1.23.1",
    "@expo-google-fonts/inter": "^0.2.3",
    "expo-font": "~12.0.0"
  },
  "devDependencies": {
    "@types/react": "~18.2.45",
    "typescript": "~5.3.3",
    "jest": "^29.7.0",
    "jest-expo": "~51.0.0",
    "@types/jest": "^29.5.12"
  },
  "jest": { "preset": "jest-expo" },
  "private": true
}
```

`cloud-app/app.json`:
```json
{
  "expo": {
    "name": "LoopGuard Cloud",
    "slug": "loopguard-cloud",
    "version": "1.0.0",
    "orientation": "portrait",
    "userInterfaceStyle": "dark",
    "splash": { "backgroundColor": "#0A0A0A" },
    "ios": { "supportsTablet": true },
    "web": { "bundler": "metro" }
  }
}
```

`cloud-app/tsconfig.json`:
```json
{
  "extends": "expo/tsconfig.base",
  "compilerOptions": { "strict": true }
}
```

`cloud-app/babel.config.js`:
```js
module.exports = function (api) {
  api.cache(true);
  return { presets: ["babel-preset-expo"] };
};
```

`cloud-app/jest.config.js`:
```js
module.exports = {
  preset: "jest-expo",
  transformIgnorePatterns: [
    "node_modules/(?!((jest-)?react-native|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg))",
  ],
};
```

`cloud-app/src/theme.ts`:
```ts
export const theme = {
  bg: "#0A0A0A",
  surface: "#141414",
  surfaceHigh: "#1C1C1F",
  border: "#262629",
  text: "#EDEDED",
  textDim: "#8A8A8F",
  accent: "#3B82F6",
  ok: "#22C55E",
  warn: "#F59E0B",
  danger: "#EF4444",
  radius: 14,
  space: (n: number) => n * 4,
  mono: "Inter_400Regular",
};
```

`cloud-app/src/runReducer.ts`:
```ts
export type ServerMessage = { type: string; data?: any };

export type RunEvent = {
  tool?: string;
  args?: Record<string, any>;
  output?: string;
  is_error?: boolean;
};

export type PendingDecision = {
  detector?: string;
  similarity?: number;
  reason?: string;
  judge_reasoning?: string;
  judge_confidence?: number;
  suggested_message?: string | null;
};

export type RunUiState = {
  status: string;
  events: RunEvent[];
  totalTokens: number;
  totalCost: number;
  pending: PendingDecision | null;
  finalText: string | null;
  error: string | null;
  summary: Record<string, any>;
};

export function initialRunState(): RunUiState {
  return {
    status: "connecting",
    events: [],
    totalTokens: 0,
    totalCost: 0,
    pending: null,
    finalText: null,
    error: null,
    summary: {},
  };
}

export function runReducer(state: RunUiState, msg: ServerMessage): RunUiState {
  const d = msg.data || {};
  switch (msg.type) {
    case "event":
      return {
        ...state,
        status: state.status === "connecting" ? "running" : state.status,
        events: [...state.events, d as RunEvent],
        totalTokens: d.total_tokens ?? state.totalTokens,
        totalCost: d.total_cost ?? state.totalCost,
      };
    case "decision_required":
      return { ...state, status: "awaiting_decision", pending: d as PendingDecision };
    case "status":
      return { ...state, status: d.status ?? state.status, pending: null };
    case "done":
      return {
        ...state,
        status: d.status ?? "completed",
        pending: null,
        finalText: d.final_text ?? state.finalText,
        summary: d.summary ?? state.summary,
      };
    case "error":
      return { ...state, status: "error", error: d.message ?? "unknown error" };
    default:
      return state;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/cloud-app && npm test`
Expected: PASS (3 reducer tests). Then `npx tsc --noEmit` → no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && printf 'node_modules/\n.expo/\ndist/\nweb-build/\n' > cloud-app/.gitignore && git add cloud-app/package.json cloud-app/app.json cloud-app/tsconfig.json cloud-app/babel.config.js cloud-app/jest.config.js cloud-app/src/theme.ts cloud-app/src/runReducer.ts cloud-app/src/__tests__/runReducer.test.ts cloud-app/.gitignore && git commit -m "feat(app): Expo scaffold, theme, and unit-tested run reducer"
```

---

### Task 6: App client + screens + components (UI)

**Files:**
- Create: `cloud-app/App.tsx`, `cloud-app/src/client.ts`, `cloud-app/src/screens/ConnectScreen.tsx`, `cloud-app/src/screens/StartScreen.tsx`, `cloud-app/src/screens/MissionControlScreen.tsx`, `cloud-app/src/components/EventRow.tsx`, `cloud-app/src/components/DecisionCard.tsx`, `cloud-app/src/components/Meter.tsx`

**Interfaces:**
- Consumes: `theme` (Task 5), `runReducer`/`initialRunState` (Task 5).
- Produces: `LoopGuardClient` (fetch `/health`, `POST /runs`, open WS, send interventions); a 3-screen flow (`connect` → `start` → `mission`).

- [ ] **Step 1: Write `src/client.ts`**

```ts
// cloud-app/src/client.ts
export type StartOpts = { mode: "flag" | "auto"; model: string };

export class LoopGuardClient {
  constructor(public baseUrl: string) {}

  private http() {
    return this.baseUrl.replace(/\/+$/, "");
  }
  private wsBase() {
    return this.http().replace(/^http/, "ws");
  }

  async health(): Promise<boolean> {
    try {
      const r = await fetch(`${this.http()}/health`, { method: "GET" });
      return r.ok;
    } catch {
      return false;
    }
  }

  async startRun(opts: StartOpts): Promise<string> {
    const r = await fetch(`${this.http()}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    return (await r.json()).run_id as string;
  }

  openSocket(runId: string, onMessage: (m: any) => void, onError: (e: string) => void): WebSocket {
    const ws = new WebSocket(`${this.wsBase()}/runs/${runId}/ws`);
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data as string));
      } catch {
        /* ignore */
      }
    };
    ws.onerror = () => onError("WebSocket error");
    return ws;
  }

  static intervene(ws: WebSocket, action: string, message?: string) {
    ws.send(JSON.stringify({ type: "intervene", action, message }));
  }
}
```

- [ ] **Step 2: Write the components**

`cloud-app/src/components/Meter.tsx`:
```tsx
import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";

export function Meter({ tokens, cost }: { tokens: number; cost: number }) {
  return (
    <View style={{ flexDirection: "row", gap: theme.space(4) }}>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>
        <Text style={{ color: theme.text, fontWeight: "600" }}>{tokens}</Text> tokens
      </Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>
        <Text style={{ color: theme.text, fontWeight: "600" }}>${cost.toFixed(4)}</Text>
      </Text>
    </View>
  );
}
```

`cloud-app/src/components/EventRow.tsx`:
```tsx
import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";
import { RunEvent } from "../runReducer";

export function EventRow({ event, index }: { event: RunEvent; index: number }) {
  const color = event.is_error ? theme.danger : theme.ok;
  const path = event.args?.path ?? "";
  return (
    <View
      style={{
        flexDirection: "row",
        gap: theme.space(2),
        paddingVertical: theme.space(2),
        borderBottomWidth: 1,
        borderBottomColor: theme.border,
      }}
    >
      <Text style={{ color: theme.textDim, width: 22, fontSize: 12 }}>{index + 1}</Text>
      <View style={{ flex: 1 }}>
        <Text style={{ color: theme.text, fontSize: 13 }}>
          <Text style={{ color: theme.accent }}>{event.tool}</Text>
          {path ? `("${path}")` : "()"}
        </Text>
        <Text numberOfLines={1} style={{ color, fontSize: 12, marginTop: 2 }}>
          {event.output}
        </Text>
      </View>
    </View>
  );
}
```

`cloud-app/src/components/DecisionCard.tsx`:
```tsx
import React, { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { PendingDecision } from "../runReducer";

const Btn = ({ label, color, onPress }: { label: string; color: string; onPress: () => void }) => (
  <Pressable
    onPress={onPress}
    style={{
      backgroundColor: color,
      paddingVertical: theme.space(3),
      paddingHorizontal: theme.space(4),
      borderRadius: theme.radius,
      flexGrow: 1,
      alignItems: "center",
    }}
  >
    <Text style={{ color: "#0A0A0A", fontWeight: "700", fontSize: 14 }}>{label}</Text>
  </Pressable>
);

export function DecisionCard({
  pending,
  onAction,
}: {
  pending: PendingDecision;
  onAction: (action: string, message?: string) => void;
}) {
  const [custom, setCustom] = useState("");
  return (
    <View
      style={{
        backgroundColor: theme.surfaceHigh,
        borderWidth: 1,
        borderColor: theme.warn,
        borderRadius: theme.radius,
        padding: theme.space(4),
        gap: theme.space(3),
      }}
    >
      <Text style={{ color: theme.warn, fontWeight: "700", fontSize: 15 }}>
        ⚠ Loop detected · {pending.detector}
        {pending.similarity ? ` (${pending.similarity.toFixed(2)})` : ""}
      </Text>
      {pending.judge_reasoning ? (
        <Text style={{ color: theme.text, fontSize: 13, lineHeight: 19 }}>
          <Text style={{ color: theme.textDim }}>Judge: </Text>
          {pending.judge_reasoning}
        </Text>
      ) : null}
      {pending.suggested_message ? (
        <Text style={{ color: theme.text, fontSize: 13, lineHeight: 19 }}>
          <Text style={{ color: theme.textDim }}>Suggested fix: </Text>
          {pending.suggested_message}
        </Text>
      ) : null}
      <View style={{ flexDirection: "row", gap: theme.space(2) }}>
        {pending.suggested_message ? (
          <Btn label="Approve fix" color={theme.ok} onPress={() => onAction("approve")} />
        ) : null}
        <Btn label="Ignore once" color={theme.textDim} onPress={() => onAction("continue_once")} />
        <Btn label="Terminate" color={theme.danger} onPress={() => onAction("terminate")} />
      </View>
      <TextInput
        value={custom}
        onChangeText={setCustom}
        placeholder="Or send a custom correction…"
        placeholderTextColor={theme.textDim}
        style={{
          color: theme.text,
          borderWidth: 1,
          borderColor: theme.border,
          borderRadius: theme.radius,
          padding: theme.space(3),
          fontSize: 13,
        }}
        onSubmitEditing={() => custom.trim() && onAction("inject", custom.trim())}
      />
    </View>
  );
}
```

- [ ] **Step 3: Write the screens + App root**

`cloud-app/src/screens/ConnectScreen.tsx`:
```tsx
import React, { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient } from "../client";

export function ConnectScreen({ onConnected }: { onConnected: (url: string) => void }) {
  const [url, setUrl] = useState("http://localhost:8000");
  const [status, setStatus] = useState<string | null>(null);

  async function test() {
    setStatus("Checking…");
    const ok = await new LoopGuardClient(url).health();
    setStatus(ok ? "ok" : "unreachable");
    if (ok) onConnected(url);
  }

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg, padding: theme.space(6), justifyContent: "center", gap: theme.space(4) }}>
      <Text style={{ color: theme.text, fontSize: 28, fontWeight: "800" }}>LoopGuard</Text>
      <Text style={{ color: theme.textDim, fontSize: 14 }}>Connect to your LoopGuard server.</Text>
      <TextInput
        value={url}
        onChangeText={setUrl}
        autoCapitalize="none"
        style={{ color: theme.text, borderWidth: 1, borderColor: theme.border, borderRadius: theme.radius, padding: theme.space(4), fontSize: 15 }}
      />
      <Pressable onPress={test} style={{ backgroundColor: theme.accent, padding: theme.space(4), borderRadius: theme.radius, alignItems: "center" }}>
        <Text style={{ color: "#fff", fontWeight: "700" }}>Connect</Text>
      </Pressable>
      {status && status !== "ok" ? (
        <Text style={{ color: status === "unreachable" ? theme.danger : theme.textDim }}>{status}</Text>
      ) : null}
    </View>
  );
}
```

`cloud-app/src/screens/StartScreen.tsx`:
```tsx
import React, { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { theme } from "../theme";

export function StartScreen({ onStart }: { onStart: (mode: "flag" | "auto") => void }) {
  const [mode, setMode] = useState<"flag" | "auto">("flag");
  const Opt = ({ m, label, desc }: { m: "flag" | "auto"; label: string; desc: string }) => (
    <Pressable
      onPress={() => setMode(m)}
      style={{
        borderWidth: 1,
        borderColor: mode === m ? theme.accent : theme.border,
        backgroundColor: mode === m ? theme.surfaceHigh : theme.surface,
        borderRadius: theme.radius,
        padding: theme.space(4),
        gap: theme.space(1),
      }}
    >
      <Text style={{ color: theme.text, fontWeight: "700", fontSize: 16 }}>{label}</Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>{desc}</Text>
    </Pressable>
  );
  return (
    <View style={{ flex: 1, backgroundColor: theme.bg, padding: theme.space(6), gap: theme.space(4), justifyContent: "center" }}>
      <Text style={{ color: theme.text, fontSize: 22, fontWeight: "800" }}>New run</Text>
      <Opt m="flag" label="Flag mode" desc="Pause on a loop and ask me what to do." />
      <Opt m="auto" label="Auto mode" desc="Auto-apply the judge's fix and keep going." />
      <Pressable onPress={() => onStart(mode)} style={{ backgroundColor: theme.accent, padding: theme.space(4), borderRadius: theme.radius, alignItems: "center", marginTop: theme.space(2) }}>
        <Text style={{ color: "#fff", fontWeight: "700" }}>Start run</Text>
      </Pressable>
    </View>
  );
}
```

`cloud-app/src/screens/MissionControlScreen.tsx`:
```tsx
import React, { useEffect, useReducer, useRef } from "react";
import { ScrollView, Text, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient } from "../client";
import { initialRunState, runReducer } from "../runReducer";
import { EventRow } from "../components/EventRow";
import { DecisionCard } from "../components/DecisionCard";
import { Meter } from "../components/Meter";

export function MissionControlScreen({
  client,
  mode,
}: {
  client: LoopGuardClient;
  mode: "flag" | "auto";
}) {
  const [state, dispatch] = useReducer(runReducer, undefined, initialRunState);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    (async () => {
      try {
        const runId = await client.startRun({ mode, model: "cerebras/gpt-oss-120b" });
        ws = client.openSocket(runId, dispatch, (e) => dispatch({ type: "error", data: { message: e } }));
        wsRef.current = ws;
      } catch (e: any) {
        dispatch({ type: "error", data: { message: String(e?.message || e) } });
      }
    })();
    return () => ws?.close();
  }, []);

  const onAction = (action: string, message?: string) => {
    if (wsRef.current) LoopGuardClient.intervene(wsRef.current, action, message);
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg, padding: theme.space(5), paddingTop: theme.space(14) }}>
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: theme.space(3) }}>
        <Text style={{ color: theme.text, fontSize: 18, fontWeight: "800" }}>Mission Control</Text>
        <Text style={{ color: theme.textDim, fontSize: 12 }}>{state.status}</Text>
      </View>
      <Meter tokens={state.totalTokens} cost={state.totalCost} />
      <ScrollView style={{ flex: 1, marginTop: theme.space(3) }}>
        {state.events.map((e, i) => (
          <EventRow key={i} event={e} index={i} />
        ))}
        {state.finalText ? (
          <Text style={{ color: theme.ok, marginTop: theme.space(4), fontSize: 14 }}>Agent: {state.finalText}</Text>
        ) : null}
        {state.error ? (
          <Text style={{ color: theme.danger, marginTop: theme.space(4) }}>{state.error}</Text>
        ) : null}
      </ScrollView>
      {state.pending ? (
        <View style={{ marginTop: theme.space(3) }}>
          <DecisionCard pending={state.pending} onAction={onAction} />
        </View>
      ) : null}
    </View>
  );
}
```

`cloud-app/App.tsx`:
```tsx
import React, { useState } from "react";
import { StatusBar } from "expo-status-bar";
import { LoopGuardClient } from "./src/client";
import { ConnectScreen } from "./src/screens/ConnectScreen";
import { StartScreen } from "./src/screens/StartScreen";
import { MissionControlScreen } from "./src/screens/MissionControlScreen";

export default function App() {
  const [client, setClient] = useState<LoopGuardClient | null>(null);
  const [mode, setMode] = useState<"flag" | "auto" | null>(null);

  return (
    <>
      <StatusBar style="light" />
      {!client ? (
        <ConnectScreen onConnected={(url) => setClient(new LoopGuardClient(url))} />
      ) : !mode ? (
        <StartScreen onStart={setMode} />
      ) : (
        <MissionControlScreen client={client} mode={mode} />
      )}
    </>
  );
}
```

- [ ] **Step 4: Verify it typechecks and the reducer tests still pass**

Run:
```bash
cd /Users/ayanbinsaif/agent/cloud-app && npx tsc --noEmit && npm test
```
Expected: tsc reports no errors; reducer tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add cloud-app/App.tsx cloud-app/src/client.ts cloud-app/src/screens cloud-app/src/components && git commit -m "feat(app): client, mission-control screens, and decision card UI"
```

---

### Task 7: End-to-end verification, web bundle, docs

**Files:**
- Create: `cloud-app/README.md`
- Modify: `loopguard/README.md`

**Interfaces:** none (verification + docs).

- [ ] **Step 1: Backend live smoke (real Cerebras, requires key)**

Run (server in background, then drive it):
```bash
cd /Users/ayanbinsaif/agent/loopguard && set -a && . ./.env && set +a && python3 -m pip install -e ".[dev,server]" -q
( loopguard serve --port 8000 & ) ; sleep 3
curl -s localhost:8000/health
curl -s -X POST localhost:8000/runs -H 'content-type: application/json' -d '{"mode":"auto","model":"cerebras/gpt-oss-120b"}'
sleep 25
curl -s localhost:8000/runs
```
Expected: `/health` returns ok; `POST /runs` returns a `run_id`; after a pause, `GET /runs` shows the run with accumulated events and a real summary (tokens + cost). Stop the server: `pkill -f "loopguard serve"`.

- [ ] **Step 2: App web bundle builds**

Run:
```bash
cd /Users/ayanbinsaif/agent/cloud-app && npm install && npx expo export --platform web
```
Expected: a `dist/` web bundle is produced with no errors (proves the app compiles and bundles). Add `dist/` to `cloud-app/.gitignore` if not already present.

- [ ] **Step 3: Write `cloud-app/README.md`**

Include: what it is; prerequisites (Node, Expo, a running `loopguard serve`); how to run (`npm install`, `npx expo start`, press `i` for iOS simulator or scan the QR with Expo Go); the server-URL note (use the laptop's LAN IP from a physical phone); flag vs auto; and the honest line that the cloud demo runs the server locally over the network (production = deploy the same server, change the URL).

- [ ] **Step 4: Update `loopguard/README.md`**

Add a "Cloud app" section: `pip install "loopguard[server]"`, `loopguard serve`, then point the Expo app (`cloud-app/`) at the server; one-line description of monitor + flag/auto interventions; link to `cloud-app/README.md`.

- [ ] **Step 5: Full backend suite green + commit**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/ -q`
Expected: PASS (all backend tests green).

```bash
cd /Users/ayanbinsaif/agent && git add cloud-app/README.md cloud-app/.gitignore loopguard/README.md && git commit -m "docs: cloud app + server usage; verify e2e and web bundle"
```

---

## Self-Review

**Spec coverage:**
- §2A engine hooks → Task 1. ✓
- §2B backend (schemas, runs, app, serve, extra) → Tasks 2, 3, 4. ✓
- §2C app (scaffold, theme, reducer, client, screens, components) → Tasks 5, 6. ✓
- §3 data flow → Tasks 3 (pause/resume) + 4 (WS) + 6 (client/screens). ✓
- §4 error handling → 400 on bad key (Task 4), pause timeout (Task 3), WS reconnect/resync via GET (Task 4 replay), app connect error (Task 6 ConnectScreen). ✓
- §5 testing → engine-hook tests (1), run unit tests incl. pause/intervene (3), FastAPI+WS transport tests (4), reducer jest test (5), typecheck+web export (6,7). Honest mobile-UI limitation stated. ✓
- §6 deps/layout (server extra, serve, cloud-app/) → Tasks 4, 5. ✓
- §7 out of scope (external agents) → not built; no task. ✓
- §8 demo → Task 7 smoke mirrors it. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code. The Task 5 Step 2 "fails" note depends on Step 3 files existing for `npm install` — sequencing is called out explicitly.

**Type consistency:** WebSocket `type` strings (`event`/`decision_required`/`status`/`done`/`error`) and intervene `action`s (`terminate`/`approve`/`inject`/`continue_once`) match across `schemas.py` (Task 2), `runs.py` (Task 3), `app.py` (Task 4), `runReducer.ts` (Task 5), `client.ts`/`DecisionCard.tsx` (Task 6). `Run(id, mode, model, emit, root)`, `RunRegistry.create/get/list`, `apply_intervention(decision, action, message)`, `create_app(provider_factory, judge_factory)`, `LoopGuardClient` methods, `runReducer`/`initialRunState` are consistent across tasks. ✓

**Note for executor:** Task 5 builds the Expo scaffold; `npm install` requires network access to the npm registry. If the Expo SDK 51 pin drifts, run `npx expo install --fix` after `npm install`. The backend WebSocket integration test (Task 4) covers transport + a no-loop run to `done`; the pause/intervene correctness is covered at the `Run` unit level (Task 3) because driving a cross-thread pause through Starlette's sync TestClient WebSocket is flaky — this split is intentional.
