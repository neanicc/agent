# Control-Plane Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned canonical event stream, durable local store, daemon protocol, and compatibility projection without changing existing loop-detector behavior.

**Architecture:** The existing `LoopEvent`, `LoopGuard`, and `LoopDecision` remain stable. New control-plane types live under `loopguard.control`; adapters emit `ControlEvent` objects, the daemon persists them before evaluation, and selected payloads project into `LoopEvent`.

**Tech Stack:** Python 3.11+, Pydantic 2, asyncio Unix sockets, standard-library SQLite, Typer, pytest

---

### Task 1: Define canonical identifiers and event envelope

**Files:**
- Create: `loopguard/src/loopguard/control/__init__.py`
- Create: `loopguard/src/loopguard/control/events.py`
- Create: `loopguard/src/loopguard/control/decisions.py`
- Test: `loopguard/tests/control/test_events.py`
- Test: `loopguard/tests/control/test_decisions.py`

- [ ] **Step 1: Write failing serialization and version tests**

```python
from loopguard.control.events import ControlEvent, EventKind, SessionRef


def test_control_event_round_trips_with_version():
    event = ControlEvent(
        event_id="evt_01",
        kind=EventKind.TOOL_CALL,
        source="codex",
        session=SessionRef(host_id="host_1", repo_id="repo_1", session_id="s_1"),
        payload={"tool_name": "Bash", "arguments": {"cmd": "pytest"}},
    )
    restored = ControlEvent.model_validate_json(event.model_dump_json())
    assert restored.schema_version == 1
    assert restored.session.repo_id == "repo_1"
    assert restored.payload["tool_name"] == "Bash"
```

```python
from datetime import datetime, timedelta, timezone

from loopguard.control.decisions import ActionKind, ActionRequest, PolicyDecision


def test_policy_decision_and_action_request_share_session_state():
    decision = PolicyDecision(
        decision_id="dec_1", action="request_approval", reason="loop detected",
        session_id="s_1", state_version=4,
    )
    action = ActionRequest(
        action_id="act_1", session_id=decision.session_id, kind=ActionKind.INTERRUPT,
        expected_state_version=decision.state_version, nonce="nonce_1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert action.expected_state_version == 4
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `cd loopguard && python -m pytest -q tests/control/test_events.py tests/control/test_decisions.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'loopguard.control'`.

- [ ] **Step 3: Implement the minimal event types**

```python
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    SESSION_STARTED = "session.started"
    SESSION_STOPPED = "session.stopped"
    PROMPT_SUBMITTED = "prompt.submitted"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    FILE_CHANGED = "file.changed"
    TEST_COMPLETED = "test.completed"
    PIPELINE_FAILED = "pipeline.failed"
    ACTION_REQUESTED = "action.requested"
    ACTION_RESOLVED = "action.resolved"


class SessionRef(BaseModel):
    host_id: str
    repo_id: str
    session_id: str
    worktree_id: str | None = None
    turn_id: str | None = None


class ControlEvent(BaseModel):
    schema_version: int = 1
    event_id: str
    kind: EventKind
    source: str
    session: SessionRef
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

Create `decisions.py`:

```python
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActionKind(StrEnum):
    INTERRUPT = "interrupt"
    APPROVE = "approve"
    CONTINUE_ONCE = "continue_once"
    INJECT = "inject"
    PUBLISH_REPAIR = "publish_repair"


class PolicyDecision(BaseModel):
    schema_version: int = 1
    decision_id: str
    action: Literal["allow", "warn", "block", "request_approval", "inject"]
    reason: str
    session_id: str
    state_version: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    schema_version: int = 1
    action_id: str
    session_id: str
    kind: ActionKind
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_state_version: int
    nonce: str
    expires_at: datetime
```

- [ ] **Step 4: Run the focused test**

Run: `cd loopguard && python -m pytest -q tests/control/test_events.py tests/control/test_decisions.py`
Expected: PASS.

- [ ] **Step 5: Commit the event contract**

```bash
git add loopguard/src/loopguard/control loopguard/tests/control/test_events.py \
  loopguard/tests/control/test_decisions.py
git commit -m "feat: add versioned control event contract"
```

### Task 2: Project control events into the existing loop detector

**Files:**
- Create: `loopguard/src/loopguard/control/projection.py`
- Test: `loopguard/tests/control/test_projection.py`
- Modify: `loopguard/src/loopguard/control/__init__.py`

- [ ] **Step 1: Write failing projection tests**

```python
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.projection import to_loop_event


def test_tool_call_projects_to_loop_event():
    event = ControlEvent(
        event_id="evt_1",
        kind=EventKind.TOOL_CALL,
        source="claude",
        session=SessionRef(host_id="h", repo_id="r", session_id="s"),
        payload={"agent": "claude", "tool_name": "Bash", "arguments": {"cmd": "pytest"}},
    )
    projected = to_loop_event(event)
    assert projected is not None
    assert projected.run_id == "s"
    assert projected.tool_name == "Bash"
    assert projected.metadata["control_event_id"] == "evt_1"


def test_file_change_does_not_project_to_loop_event():
    event = ControlEvent(
        event_id="evt_2",
        kind=EventKind.FILE_CHANGED,
        source="filesystem",
        session=SessionRef(host_id="h", repo_id="r", session_id="s"),
        payload={"path": "src/app.py"},
    )
    assert to_loop_event(event) is None
```

- [ ] **Step 2: Verify failure before implementation**

Run: `cd loopguard && python -m pytest -q tests/control/test_projection.py`
Expected: FAIL because `loopguard.control.projection` does not exist.

- [ ] **Step 3: Implement an explicit projection map**

```python
from loopguard.event import LoopEvent

from .events import ControlEvent, EventKind


def to_loop_event(event: ControlEvent) -> LoopEvent | None:
    if event.kind not in {EventKind.TOOL_CALL, EventKind.TOOL_RESULT}:
        return None
    payload = event.payload
    return LoopEvent(
        run_id=event.session.session_id,
        agent=str(payload.get("agent", event.source)),
        kind="tool_call" if event.kind == EventKind.TOOL_CALL else "tool_result",
        tool_name=payload.get("tool_name"),
        tool_args=payload.get("arguments"),
        error=payload.get("error"),
        tokens=int(payload.get("tokens", 0)),
        cost_usd=float(payload.get("cost_usd", 0.0)),
        metadata={
            "control_event_id": event.event_id,
            "repo_id": event.session.repo_id,
            "worktree_id": event.session.worktree_id,
        },
    )
```

- [ ] **Step 4: Run projection and existing guard tests**

Run: `cd loopguard && python -m pytest -q tests/control/test_projection.py tests/test_guard.py`
Expected: PASS.

- [ ] **Step 5: Commit the compatibility boundary**

```bash
git add loopguard/src/loopguard/control loopguard/tests/control/test_projection.py
git commit -m "feat: project control events into loop detection"
```

### Task 3: Add the durable SQLite event store

**Files:**
- Create: `loopguard/src/loopguard/control/store.py`
- Test: `loopguard/tests/control/test_store.py`

- [ ] **Step 1: Write failing idempotency and replay tests**

```python
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore


def _event(event_id: str) -> ControlEvent:
    return ControlEvent(
        event_id=event_id,
        kind=EventKind.SESSION_STARTED,
        source="test",
        session=SessionRef(host_id="h", repo_id="r", session_id="s"),
    )


def test_append_is_idempotent_and_replay_uses_cursor(tmp_path):
    store = EventStore(tmp_path / "events.db")
    assert store.append(_event("evt_1")) == 1
    assert store.append(_event("evt_1")) == 1
    assert store.append(_event("evt_2")) == 2
    rows = store.read_after(repo_id="r", cursor=1, limit=10)
    assert [row.event.event_id for row in rows] == ["evt_2"]
    assert rows[0].cursor == 2
```

- [ ] **Step 2: Run and observe the missing store**

Run: `cd loopguard && python -m pytest -q tests/control/test_store.py`
Expected: FAIL because `EventStore` is undefined.

- [ ] **Step 3: Implement WAL storage and uniqueness**

```python
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .events import ControlEvent


@dataclass(frozen=True)
class StoredEvent:
    cursor: int
    event: ControlEvent


class EventStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                repo_id TEXT NOT NULL,
                body TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def append(self, event: ControlEvent) -> int:
        self._conn.execute(
            "INSERT OR IGNORE INTO events(event_id, repo_id, body) VALUES (?, ?, ?)",
            (event.event_id, event.session.repo_id, event.model_dump_json()),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT cursor FROM events WHERE event_id = ?", (event.event_id,)
        ).fetchone()
        return int(row[0])

    def read_after(self, repo_id: str, cursor: int, limit: int) -> list[StoredEvent]:
        rows = self._conn.execute(
            "SELECT cursor, body FROM events WHERE repo_id = ? AND cursor > ? "
            "ORDER BY cursor LIMIT ?",
            (repo_id, cursor, limit),
        ).fetchall()
        return [StoredEvent(int(c), ControlEvent.model_validate_json(b)) for c, b in rows]
```

- [ ] **Step 4: Run storage tests twice to catch stale-state assumptions**

Run: `cd loopguard && python -m pytest -q tests/control/test_store.py && python -m pytest -q tests/control/test_store.py`
Expected: both runs PASS.

- [ ] **Step 5: Commit durable local storage**

```bash
git add loopguard/src/loopguard/control/store.py loopguard/tests/control/test_store.py
git commit -m "feat: persist control events in sqlite"
```

### Task 4: Redact payloads before storage

**Files:**
- Create: `loopguard/src/loopguard/control/redaction.py`
- Modify: `loopguard/src/loopguard/control/store.py`
- Test: `loopguard/tests/control/test_redaction.py`

- [ ] **Step 1: Write failing secret-redaction tests**

```python
from loopguard.control.redaction import redact


def test_redact_masks_keys_and_inline_tokens():
    value = {
        "Authorization": "Bearer abcdefghijklmnop",
        "cmd": "curl -H 'x-api-key: sk-test-secret-value' https://example.test",
        "nested": {"password": "hunter2"},
    }
    result = redact(value)
    assert "abcdefghijklmnop" not in str(result)
    assert "sk-test-secret-value" not in str(result)
    assert "hunter2" not in str(result)
    assert result["nested"]["password"] == "[REDACTED]"
```

- [ ] **Step 2: Confirm failure**

Run: `cd loopguard && python -m pytest -q tests/control/test_redaction.py`
Expected: FAIL because `redact` does not exist.

- [ ] **Step 3: Implement recursive key and pattern redaction**

```python
import re
from typing import Any

_SECRET_KEYS = {"authorization", "password", "secret", "token", "api_key", "apikey"}
_INLINE = re.compile(
    r"(?i)(bearer\s+|x-api-key[:=]\s*|sk-)[A-Za-z0-9._-]{8,}"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in _SECRET_KEYS else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(child) for child in value]
    if isinstance(value, str):
        return _INLINE.sub(lambda match: match.group(1) + "[REDACTED]", value)
    return value
```

Update `EventStore.append()` to copy the event with
`event.model_copy(update={"payload": redact(event.payload)})` before serialization.

- [ ] **Step 4: Run redaction and storage tests**

Run: `cd loopguard && python -m pytest -q tests/control/test_redaction.py tests/control/test_store.py`
Expected: PASS.

- [ ] **Step 5: Commit pre-persistence redaction**

```bash
git add loopguard/src/loopguard/control loopguard/tests/control
git commit -m "feat: redact secrets before event persistence"
```

### Task 5: Implement the local daemon protocol

**Files:**
- Create: `loopguard/src/loopguard/control/protocol.py`
- Create: `loopguard/src/loopguard/control/daemon.py`
- Test: `loopguard/tests/control/test_daemon.py`

- [ ] **Step 1: Write a failing socket ingestion test**

```python
import asyncio

from loopguard.control.daemon import LoopGuardDaemon
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore


def test_daemon_acknowledges_only_after_persistence(tmp_path):
    async def scenario():
        socket_path = tmp_path / "loopguard.sock"
        store = EventStore(tmp_path / "events.db")
        daemon = LoopGuardDaemon(store=store, socket_path=socket_path)
        await daemon.start()
        reader, writer = await asyncio.open_unix_connection(socket_path)
        event = ControlEvent(
            event_id="evt_1",
            kind=EventKind.SESSION_STARTED,
            source="test",
            session=SessionRef(host_id="h", repo_id="r", session_id="s"),
        )
        writer.write((event.model_dump_json() + "\n").encode())
        await writer.drain()
        assert await reader.readline() == b'{"cursor":1,"ok":true}\n'
        assert store.read_after("r", 0, 10)[0].event.event_id == "evt_1"
        writer.close()
        await writer.wait_closed()
        await daemon.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Verify the daemon test fails**

Run: `cd loopguard && python -m pytest -q tests/control/test_daemon.py`
Expected: FAIL because `LoopGuardDaemon` does not exist.

- [ ] **Step 3: Implement newline-delimited request/ack handling**

```python
import asyncio
import json
from pathlib import Path

from .events import ControlEvent
from .store import EventStore


class LoopGuardDaemon:
    def __init__(self, store: EventStore, socket_path: Path):
        self.store = store
        self.socket_path = socket_path
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := await reader.readline():
            try:
                cursor = self.store.append(ControlEvent.model_validate_json(line))
                response = {"cursor": cursor, "ok": True}
            except Exception as exc:
                response = {"error": type(exc).__name__, "ok": False}
            writer.write(
                (json.dumps(response, separators=(",", ":"), sort_keys=True) + "\n").encode()
            )
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self.socket_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run daemon, store, and projection tests**

Run: `cd loopguard && python -m pytest -q tests/control`
Expected: PASS.

- [ ] **Step 5: Commit the daemon protocol**

```bash
git add loopguard/src/loopguard/control loopguard/tests/control
git commit -m "feat: add durable local control daemon"
```

### Task 6: Add daemon CLI lifecycle and diagnostics

**Files:**
- Create: `loopguard/src/loopguard/control/paths.py`
- Modify: `loopguard/src/loopguard/cli.py`
- Test: `loopguard/tests/control/test_cli_daemon.py`
- Modify: `loopguard/README.md`

- [ ] **Step 1: Write failing CLI help and doctor tests**

```python
from typer.testing import CliRunner

from loopguard.cli import app


runner = CliRunner()


def test_daemon_commands_are_discoverable():
    result = runner.invoke(app, ["daemon", "--help"])
    assert result.exit_code == 0
    assert "start" in result.stdout
    assert "doctor" in result.stdout


def test_doctor_json_reports_missing_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPGUARD_HOME", str(tmp_path))
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    assert '"daemon":"unreachable"' in result.stdout
```

- [ ] **Step 2: Verify missing commands**

Run: `cd loopguard && python -m pytest -q tests/control/test_cli_daemon.py`
Expected: FAIL because the daemon command group is absent.

- [ ] **Step 3: Add paths and commands**

Define `loopguard_home()` as `$LOOPGUARD_HOME` or `~/.loopguard`, with `events.db`,
`loopguard.sock`, and `loopguard.pid` children. Add Typer commands:

```python
daemon_app = typer.Typer(help="Manage the local LoopGuard daemon.")
app.add_typer(daemon_app, name="daemon")


@daemon_app.command("start")
def daemon_start(foreground: bool = typer.Option(False)) -> None:
    """Start loopguardd; foreground mode is used by launchd/systemd and tests."""


@app.command("doctor")
def doctor(as_json: bool = typer.Option(False, "--json")) -> None:
    """Check store, socket, schema version, and integration status."""
```

The foreground implementation must use `asyncio.run()`. Background startup must use a platform
service definition added by the integration plan rather than an unmanaged double-fork.
Add `build>=1.2` to the `dev` optional dependency because the completion gate builds both package
artifacts.

- [ ] **Step 4: Run CLI and complete existing test suite**

Run: `cd loopguard && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit the CLI and migration documentation**

```bash
git add loopguard/src/loopguard loopguard/tests/control loopguard/README.md
git commit -m "feat: expose daemon lifecycle and diagnostics"
```

## Completion gate

Run:

```bash
cd loopguard
python -m pytest -q
ruff check src tests
python -m build
```

Expected: all commands exit 0, existing demo behavior is unchanged, and a daemon restart can replay
every acknowledged event from SQLite.
