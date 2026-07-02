# Control-Plane Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned canonical event stream, durable local store, daemon protocol, and compatibility projection without changing existing loop-detector behavior.

**Architecture:** The existing `LoopEvent`, `LoopGuard`, and `LoopDecision` remain stable. New
control-plane types live under `loopguard.control`; adapters emit `ControlEvent` objects, the
daemon validates and persists them before evaluation, and selected payloads project into
`LoopEvent`. A `DaemonServices` composition root owns per-session guards and registered subsystem
handlers so ingestion cannot silently stop at storage.

**Tech Stack:** Python 3.11+, Pydantic 2, asyncio Unix sockets, Windows named pipes, standard-library
SQLite, `cryptography` AES-GCM, platform credential storage, Typer, pytest

**Canonical cursor rule:** names encode domains and are never substituted for one another:

- `local_log_seq`: host-wide durable append position.
- `repo_seq`: repository-scoped change/event position.
- `session_seq`: session-scoped event position.
- `cloud_ingest_seq`: server-assigned durable ingest position.
- `client_stream_seq`: subscription-specific delivery position.

Every API, database field, protocol frame, and client reducer uses the full name. Tests interleave
repositories and sessions to prove one stream cannot create a false gap in another.

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

from loopguard.control.decisions import (
    ActionKind,
    ActionRequest,
    ActionTarget,
    PolicyDecision,
    TargetKind,
)


def test_policy_decision_and_action_request_share_session_state():
    decision = PolicyDecision(
        decision_id="dec_1",
        action="request_approval",
        reason="loop detected",
        target=ActionTarget(kind=TargetKind.SESSION, target_id="s_1"),
        state_version=4,
        state_hash="sha256:state-4",
    )
    action = ActionRequest(
        action_id="act_1",
        target=decision.target,
        kind=ActionKind.INTERRUPT,
        expected_state_version=decision.state_version,
        expected_state_hash=decision.state_hash,
        nonce="nonce_1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert action.expected_state_version == 4
    assert action.target.target_id == "s_1"
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


class TargetKind(StrEnum):
    SESSION = "session"
    REPAIR = "repair"
    HOST = "host"
    VERIFICATION = "verification"
    REPOSITORY = "repository"


class ActionTarget(BaseModel):
    kind: TargetKind
    target_id: str


class PolicyDecision(BaseModel):
    schema_version: int = 1
    decision_id: str
    action: Literal["allow", "warn", "pause", "interrupt", "inject", "request_approval"]
    reason: str
    target: ActionTarget
    state_version: int
    state_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    schema_version: int = 1
    action_id: str
    target: ActionTarget
    kind: ActionKind
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_state_version: int
    expected_state_hash: str
    nonce: str
    expires_at: datetime
```

Reject unsupported schema versions at every boundary. Compatibility means accepting documented
older versions through explicit migrations, not letting Pydantic ignore unknown semantics. Add
tests for unknown event/decision/action versions, invalid target kinds, state-hash mismatch, and
JSON round-trips for every enum member.

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
- Create: `loopguard/src/loopguard/control/crypto.py`
- Create: `loopguard/src/loopguard/control/migrations.py`
- Modify: `loopguard/pyproject.toml`
- Test: `loopguard/tests/control/test_store.py`
- Test: `loopguard/tests/control/test_store_recovery.py`
- Test: `loopguard/tests/control/test_crypto.py`

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
    store = EventStore.for_test(tmp_path / "events.db")
    assert store.append(_event("evt_1")).local_log_seq == 1
    assert store.append(_event("evt_1")).local_log_seq == 1
    assert store.append(_event("evt_2")).repo_seq == 2
    rows = store.read_repo_after(repo_id="r", repo_seq=1, limit=10)
    assert [row.event.event_id for row in rows] == ["evt_2"]
    assert rows[0].repo_seq == 2


def test_interleaved_repositories_have_independent_repo_sequences(tmp_path):
    store = EventStore.for_test(tmp_path / "events.db")
    a1 = store.append(_event_for("a", "a1"))
    b1 = store.append(_event_for("b", "b1"))
    a2 = store.append(_event_for("a", "a2"))
    assert [a1.repo_seq, a2.repo_seq] == [1, 2]
    assert b1.repo_seq == 1
    assert a2.local_log_seq == 3


def test_body_is_encrypted_and_restart_replays_acknowledged_event(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore.for_test(path, key=b"test-key-material")
    position = store.append(_event("evt_secret"))
    store.close()
    assert b"evt_secret" not in path.read_bytes()
    reopened = EventStore.for_test(path, key=b"test-key-material")
    assert reopened.read_local_after(position.local_log_seq - 1, 10)[0].event.event_id == "evt_secret"
```

- [ ] **Step 2: Run and observe the missing store**

Run: `cd loopguard && python -m pytest -q tests/control/test_store.py`
Expected: FAIL because `EventStore` is undefined.

- [ ] **Step 3: Implement migrated, encrypted WAL storage and explicit cursor domains**

Create a migration-owned schema with:

```sql
events(
  local_log_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  repo_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  repo_seq INTEGER NOT NULL,
  session_seq INTEGER NOT NULL,
  schema_version INTEGER NOT NULL,
  key_id TEXT NOT NULL,
  nonce BLOB NOT NULL,
  ciphertext BLOB NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(repo_id, repo_seq),
  UNIQUE(session_id, session_seq)
)
```

Allocate repository/session sequences transactionally with explicit sequence tables; never derive
them from `local_log_seq`. Return a `StoredPosition(local_log_seq, repo_seq, session_seq)` and expose
separate `read_local_after`, `read_repo_after`, and `read_session_after` methods.

Use SQLite WAL, foreign keys, a five-second busy timeout, bounded retries for `SQLITE_BUSY`, explicit
transactions, `quick_check` at startup, and migration version checks. Encrypt the serialized,
already-redacted body with AES-256-GCM using the stable metadata as additional authenticated data.
Store the data key through a `PlatformKeyStore` backed by macOS Keychain, Windows Credential
Manager, or Linux Secret Service; test with an injected in-memory key store. A missing/corrupt key
or failed integrity check is a named startup error, never a new empty database.

Create the LoopGuard home directory as owner-only (`0700` where POSIX applies), the database/key
metadata as owner-only (`0600`), and validate permissions before opening existing state. Add
recovery tests for locked databases, abrupt process exit after commit, corrupt WAL/database,
missing key, wrong key, migration failure, idempotent duplicate append, and exact replay after
restart.

- [ ] **Step 4: Run storage tests twice to catch stale-state assumptions**

Run: `cd loopguard && python -m pytest -q tests/control/test_store.py tests/control/test_store_recovery.py tests/control/test_crypto.py && python -m pytest -q tests/control/test_store.py`
Expected: both runs PASS; the on-disk database does not contain fixture event bodies in plaintext.

- [ ] **Step 5: Commit durable local storage**

```bash
git add loopguard/src/loopguard/control loopguard/tests/control loopguard/pyproject.toml
git commit -m "feat: persist encrypted control events with explicit cursors"
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
- Create: `loopguard/src/loopguard/control/dispatch.py`
- Create: `loopguard/src/loopguard/control/transport.py`
- Create: `loopguard/src/loopguard/control/daemon.py`
- Test: `loopguard/tests/control/test_daemon.py`
- Test: `loopguard/tests/control/test_daemon_integration.py`
- Test: `loopguard/tests/control/test_protocol_security.py`
- Test: `loopguard/tests/control/test_windows_transport.py`
- Create: `loopguard/tests/control/daemon_test_support.py`

- [ ] **Step 1: Write a failing socket ingestion test**

```python
import asyncio

from loopguard.control.daemon import LoopGuardDaemon
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore


def test_daemon_acknowledges_only_after_persistence(tmp_path):
    async def scenario():
        socket_path = tmp_path / "loopguard.sock"
        store = EventStore.for_test(tmp_path / "events.db")
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
        response = json.loads(await reader.readline())
        assert response["ok"] is True
        assert response["position"]["local_log_seq"] == 1
        assert store.read_local_after(0, 10)[0].event.event_id == "evt_1"
        writer.close()
        await writer.wait_closed()
        await daemon.close()

    asyncio.run(scenario())


def test_tool_event_reaches_guard_and_ack_contains_policy_decision(tmp_path):
    response = ingest_through_real_daemon(
        tmp_path,
        repeated_tool_event(session_id="s", event_id="evt_4"),
        prior_events=three_matching_tool_events(session_id="s"),
    )
    assert response["decision"]["action"] in {"warn", "pause", "interrupt", "request_approval"}
    assert response["decision"]["target"] == {"kind": "session", "target_id": "s"}


def test_oversized_frame_and_wrong_peer_are_rejected_without_persistence(tmp_path):
    daemon = daemon_fixture(tmp_path, max_frame_bytes=1_048_576)
    assert send_frame(daemon, b"x" * 1_048_577).error_code == "frame_too_large"
    assert connect_as_other_user(daemon).error_code == "peer_identity_rejected"
    assert daemon.store.count() == 0
```

- [ ] **Step 2: Verify the daemon test fails**

Run: `cd loopguard && python -m pytest -q tests/control/test_daemon.py`
Expected: FAIL because `LoopGuardDaemon` does not exist.

- [ ] **Step 3: Implement secure framed transport and the daemon composition path**

Use a versioned length-prefixed JSON protocol rather than unbounded newline reads. The header
contains protocol version, frame length, message type, and request ID. Reject unknown versions,
frames over 1 MiB, invalid UTF-8/JSON, unsupported event schema versions, and trailing data with
stable error codes. Bound per-connection queues, idle time, write time, and concurrent clients.

Define a transport interface with:

- POSIX Unix socket implementation: owner-only parent directory and socket mode, stale-socket
  ownership/type checks before unlink, and peer UID verification (`SO_PEERCRED`/`getpeereid`).
- Windows named-pipe implementation: current-user ACL, peer SID verification, bounded message
  mode, and a Windows CI integration test. Do not advertise Windows support until this test passes.

Create `DaemonServices` as the one composition root:

```text
validated frame
  -> redact
  -> EventStore.append (durable commit)
  -> EventDispatcher.dispatch
       ├─ project eligible event with to_loop_event
       ├─ session GuardRegistry.get_or_create(session_id).observe
       └─ enqueue registered bounded handlers
            context / verification / relay / metrics
  -> ack(position + normalized PolicyDecision + handler status)
```

The guard path runs synchronously after persistence so the integration receives the immediate
policy decision. Secondary handlers consume a bounded durable dispatch queue, retry idempotently,
and expose per-handler lag/errors; their failure does not erase the event or fabricate a success
status. `GuardRegistry` isolates detector/judge state per session and evicts completed idle
sessions. The dispatcher logs named errors without serializing exception strings or secret data to
clients.

Startup replays committed events whose core dispatch marker is absent before accepting new
connections. The commit/dispatch boundary is idempotent: a crash after persistence but before ack
returns the original positions and does not double-apply a detector event or handler side effect.
Implement every helper referenced by the tests in `daemon_test_support.py`; no pseudocode-only
fixture names may remain in committed tests.

- [ ] **Step 4: Run daemon, store, and projection tests**

Run: `cd loopguard && python -m pytest -q tests/control`
Expected: PASS, including hook-shaped event → real daemon → existing `LoopGuard` → normalized
policy-decision integration, crash replay, backpressure, malicious-frame, POSIX peer, and Windows
named-pipe tests.

- [ ] **Step 5: Commit the daemon protocol**

```bash
git add loopguard/src/loopguard/control loopguard/tests/control
git commit -m "feat: add durable local control daemon"
```

### Task 6: Add daemon CLI lifecycle, zero-key quickstart, and diagnostics

**Files:**
- Create: `loopguard/src/loopguard/control/paths.py`
- Create: `loopguard/src/loopguard/control/errors.py`
- Create: `loopguard/src/loopguard/quickstart.py`
- Create: `loopguard/src/loopguard/configuration.py`
- Modify: `loopguard/src/loopguard/cli.py`
- Modify: `loopguard/pyproject.toml`
- Create: `.github/workflows/control-foundation.yml`
- Test: `loopguard/tests/control/test_cli_daemon.py`
- Test: `loopguard/tests/control/test_quickstart.py`
- Test: `loopguard/tests/control/test_error_contract.py`
- Modify: `README.md`
- Modify: `loopguard/README.md`
- Create: `docs/getting-started/quickstart.md`
- Create: `docs/reference/cli.md`
- Create: `docs/reference/configuration.md`
- Create: `docs/reference/errors.md`

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


def test_quickstart_reaches_real_guard_without_key_or_permanent_install(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    result = runner.invoke(app, ["quickstart", "--home", str(tmp_path)])
    assert result.exit_code == 0
    assert "Loop detected before another paid turn" in result.stdout
    assert "No model or API key used" in result.stdout
    assert "Next: loopguard setup" in result.stdout
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
service definition added by the integration plan rather than an unmanaged double-fork. `doctor`
reports protocol/schema versions, peer-identity enforcement, state permissions, encryption/key
availability, migration/quick-check status, dispatch lag, last acknowledged positions by cursor
domain, handler failures, and integration reachability without printing secrets.

Add a `control` optional dependency group for `cryptography`, platform credential-store adapters,
and Windows named-pipe support, plus an `all-dev` group that installs every product extra required
at this point. Every later plan that adds an optional group must also extend `all-dev`; a final
metadata test resolves the extra and asserts it contains every declared product group. Add
`build>=1.2` to `dev`. The CI workflow runs the foundation suite on current
Ubuntu, macOS, and Windows runners; Windows support remains capability-disabled until its real
named-pipe tests pass.

Add one golden-path command:

```bash
loopguard quickstart
```

It creates an isolated temporary state/repository, starts the real daemon in-process, sends
hook-shaped repeated tool events through the real protocol/store/dispatcher/existing guard, prints
the loop decision and avoided-next-turn explanation, then cleans up. It uses no model, provider
key, cloud account, Docker, or permanent hooks and completes in a p95 target under 90 seconds from
an already installed package. `--json` emits stable event names/timings for tests; no telemetry is
sent without opt-in.

Rewrite the root and package READMEs around the production product while keeping demo/prototype
links clearly labelled. The first path is install -> `loopguard quickstart` -> `loopguard setup`;
live Cerebras/LiteLLM demos move to an optional section. Show exact expected output and distinguish
“offline magical moment under two minutes” from “protected real Codex/Claude session under five
minutes.”

Define one structured error contract for CLI/daemon/API adapters:
`code`, `message`, `cause`, `suggested_commands`, `doc_url`, `request_id`, `retryable`, and safe
details. Human output puts the fix first; `--json` is stable and never contains secrets or internal
tracebacks by default. `loopguard explain <code>` opens/prints the matching local reference.
`--verbose` adds redacted diagnostics. Test at least daemon unreachable, unsafe state permissions,
schema migration needed, integration trust pending, capability unavailable, and missing optional
dependency.

Add `loopguard config path|show|validate` with source/precedence output and secret redaction.
Defaults work locally; every setting has an environment/config/CLI override where appropriate.
Generate `docs/reference/cli.md` from Typer help in CI and fail on drift. Every command example in
getting-started/reference docs runs as a doctest/snapshot against the built CLI.

- [ ] **Step 4: Run CLI and complete existing test suite**

Run: `cd loopguard && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit the CLI and migration documentation**

```bash
git add README.md loopguard/src/loopguard loopguard/tests/control loopguard/README.md \
  loopguard/pyproject.toml .github/workflows/control-foundation.yml \
  docs/getting-started docs/reference
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
every acknowledged event from encrypted SQLite. Run the foundation matrix on Ubuntu, macOS, and
Windows; verify distinct cursor domains under interleaved streams; verify owner/peer controls and
frame bounds; and prove a repeated tool event travels through persistence, projection, the real
guard, and a policy decision in the acknowledgement. A fresh installed package reaches the
offline quickstart result without a key and every tested failure prints problem, cause, fix, and
error-code reference.
