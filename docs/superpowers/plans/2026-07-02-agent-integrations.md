# Codex and Claude Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically attach LoopGuard to native Codex and Claude sessions and provide managed adapters for surfaces that expose full session control.

**Architecture:** Every integration normalizes provider events into `ControlEvent` and declares capabilities. Attached adapters communicate with `loopguardd` through a fast hook client; managed adapters implement start, event stream, injection, interruption, permissions, model, and effort through official programmatic interfaces.

**Tech Stack:** Python 3.11+, asyncio subprocesses, JSONL/JSON-RPC, Codex hooks/app-server, Claude hooks/Agent SDK, pytest

---

### Task 1: Define adapter capabilities and protocol

**Files:**
- Create: `loopguard/src/loopguard/adapters/__init__.py`
- Create: `loopguard/src/loopguard/adapters/base.py`
- Test: `loopguard/tests/adapters/test_base.py`

- [ ] **Step 1: Write failing capability tests**

```python
from loopguard.adapters.base import AdapterCapabilities, Capability


def test_capability_set_is_explicit():
    caps = AdapterCapabilities(
        surface="codex-hooks",
        supported={Capability.OBSERVE, Capability.BLOCK_TOOL},
    )
    assert caps.supports(Capability.OBSERVE)
    assert not caps.supports(Capability.SELECT_MODEL)
    assert caps.require(Capability.SELECT_MODEL).code == "capability_unavailable"
```

- [ ] **Step 2: Run the test and verify import failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_base.py`
Expected: FAIL because `loopguard.adapters` does not exist.

- [ ] **Step 3: Implement the contract**

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import AsyncIterator, Protocol

from loopguard.control.events import ControlEvent, SessionRef


class Capability(StrEnum):
    OBSERVE = "observe"
    BLOCK_TOOL = "block_tool"
    INJECT_CONTEXT = "inject_context"
    INTERRUPT = "interrupt"
    SELECT_MODEL = "select_model"
    SELECT_EFFORT = "select_effort"
    APPROVE_TOOL = "approve_tool"


@dataclass(frozen=True)
class CapabilityError:
    code: str
    capability: Capability
    surface: str


@dataclass(frozen=True)
class AdapterCapabilities:
    surface: str
    supported: set[Capability]

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported

    def require(self, capability: Capability) -> CapabilityError | None:
        if self.supports(capability):
            return None
        return CapabilityError("capability_unavailable", capability, self.surface)


class AgentAdapter(Protocol):
    capabilities: AdapterCapabilities

    async def events(self, session: SessionRef) -> AsyncIterator[ControlEvent]: ...
```

- [ ] **Step 4: Run the focused test**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_base.py`
Expected: PASS.

- [ ] **Step 5: Commit the adapter contract**

```bash
git add loopguard/src/loopguard/adapters loopguard/tests/adapters/test_base.py
git commit -m "feat: define agent adapter capabilities"
```

### Task 2: Add the low-latency hook client and normalizer

**Files:**
- Create: `loopguard/src/loopguard/adapters/hook_client.py`
- Create: `loopguard/src/loopguard/adapters/normalize_hook.py`
- Create: `loopguard/src/loopguard/adapters/hook_entry.py`
- Test: `loopguard/tests/adapters/test_hook_entry.py`

- [ ] **Step 1: Write failing normalization and fail-open tests**

```python
import json

from loopguard.adapters.hook_entry import process_hook


def test_codex_tool_hook_normalizes_event(fake_socket):
    result = process_hook(
        vendor="codex",
        hook_name="PreToolUse",
        raw={"session_id": "s", "cwd": "/repo", "tool_name": "Bash",
             "tool_input": {"cmd": "pytest"}},
        client=fake_socket,
    )
    assert result.exit_code == 0
    sent = json.loads(fake_socket.lines[0])
    assert sent["kind"] == "tool.call"
    assert sent["payload"]["tool_name"] == "Bash"


def test_observation_hook_fails_open_when_daemon_is_down(unreachable_socket):
    result = process_hook(
        vendor="claude",
        hook_name="PostToolUse",
        raw={"session_id": "s", "cwd": "/repo"},
        client=unreachable_socket,
    )
    assert result.exit_code == 0
```

- [ ] **Step 2: Verify the missing hook implementation**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_hook_entry.py`
Expected: FAIL because `process_hook` is missing.

- [ ] **Step 3: Implement normalization and a 100 ms socket budget**

`normalize_hook.py` must:

```python
def normalize_hook(vendor: str, hook_name: str, raw: dict) -> ControlEvent:
    kind = {
        "SessionStart": EventKind.SESSION_STARTED,
        "UserPromptSubmit": EventKind.PROMPT_SUBMITTED,
        "PreToolUse": EventKind.TOOL_CALL,
        "PostToolUse": EventKind.TOOL_RESULT,
        "PostToolUseFailure": EventKind.TOOL_RESULT,
        "FileChanged": EventKind.FILE_CHANGED,
        "Stop": EventKind.SESSION_STOPPED,
    }[hook_name]
    # Resolve repo identity from the canonical Git root, not cwd text alone.
```

`HookClient.send()` must connect to the configured local socket, write one JSON line, read one
acknowledgement, and time out after 100 ms by default. `process_hook()` returns exit 0 on
observation failure and a typed blocking response only when a configured fail-closed policy
explicitly requires it.

- [ ] **Step 4: Run hook tests and a latency test**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_hook_entry.py`
Expected: PASS, including a test asserting p95 fake-client processing below 20 ms.

- [ ] **Step 5: Commit the hook transport**

```bash
git add loopguard/src/loopguard/adapters loopguard/tests/adapters
git commit -m "feat: normalize native agent hooks"
```

### Task 3: Install Codex hooks without overwriting user configuration

**Files:**
- Create: `loopguard/src/loopguard/adapters/codex_hooks.py`
- Create: `loopguard/src/loopguard/adapters/templates/codex-hooks.json`
- Create: `loopguard/src/loopguard/adapters/templates/codex-hook`
- Modify: `loopguard/src/loopguard/cli.py`
- Test: `loopguard/tests/adapters/test_codex_install.py`

- [ ] **Step 1: Write failing merge/idempotency tests**

```python
import json

from loopguard.adapters.codex_hooks import install_codex_hooks


def test_codex_install_preserves_existing_hooks(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command",
        "command": "existing"}]}]}}))
    first = install_codex_hooks(path, executable="/usr/local/bin/loopguard")
    second = install_codex_hooks(path, executable="/usr/local/bin/loopguard")
    body = json.loads(path.read_text())
    commands = str(body)
    assert "existing" in commands
    assert commands.count("hook-entry codex") == 6
    assert first.changed is True
    assert second.changed is False


def test_codex_project_install_uses_committed_git_root_wrapper(tmp_path):
    repo = make_git_repo(tmp_path)
    result = install_codex_hooks(repo / ".codex" / "hooks.json", scope="project")
    assert result.changed is True
    assert (repo / ".loopguard" / "hooks" / "codex-hook").exists()
    assert "git rev-parse --show-toplevel" in (repo / ".codex" / "hooks.json").read_text()
```

- [ ] **Step 2: Run the test and verify failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_codex_install.py`
Expected: FAIL because the installer does not exist.

- [ ] **Step 3: Implement LoopGuard entries with stable command signatures**

Support user scope at `~/.codex/hooks.json` and project scope at `.codex/hooks.json`. Create entries
only for the currently documented Codex events `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `PreCompact`, and `Stop`; do not invent `FileChanged` or
`PostToolUseFailure` events for Codex. Identify LoopGuard-owned handlers by an exact versioned
command prefix and wrapper path supported by the documented schema; do not add private fields to
Codex hook objects. Replace only those exact handlers on upgrade. Write through a temporary sibling
and `os.replace()` so a failed write cannot corrupt the user's file.

Project scope installs a committed `.loopguard/hooks/codex-hook` wrapper and resolves it from the
canonical Git root, matching Codex's documented project-hook trust behavior. The wrapper first
tries the local socket. If the socket is absent, it may send a signed HTTPS event only when an
explicit LoopGuard ingest URL and short-lived token are present in the environment; otherwise it
exits 0. Treat Codex cloud execution of repo-local hooks as conditional until a compatibility
smoke test against the installed/current Codex surface proves it. Never advertise live cloud
observation merely because the files were committed.

Codex requires users to review and trust non-managed hook definitions. Preserve that boundary:
installation reports `trust_required` and tells the user to review the definition in `/hooks`;
verification remains non-healthy until Codex reports the exact hook hash trusted. Never invoke
`--dangerously-bypass-hook-trust` or edit Codex trust state behind the user's back.

Expose:

```bash
loopguard integrations install codex
loopguard integrations verify codex --json
loopguard integrations uninstall codex
```

Uninstall must remove only handlers with the exact LoopGuard command signature and leave their
containing matcher group intact when it still contains unrelated handlers.

- [ ] **Step 4: Run installer and CLI tests**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_codex_install.py tests/test_cli_smoke.py`
Expected: PASS.

- [ ] **Step 5: Commit Codex attached mode**

```bash
git add loopguard/src/loopguard loopguard/tests/adapters
git commit -m "feat: install codex lifecycle hooks"
```

### Task 4: Install Claude hooks for local and cloud sessions

**Files:**
- Create: `loopguard/src/loopguard/adapters/claude_hooks.py`
- Create: `loopguard/src/loopguard/adapters/templates/claude-settings-fragment.json`
- Test: `loopguard/tests/adapters/test_claude_install.py`
- Modify: `loopguard/README.md`

- [ ] **Step 1: Write failing user/project scope tests**

```python
from loopguard.adapters.claude_hooks import install_claude_hooks


def test_claude_project_install_uses_project_relative_entry(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    result = install_claude_hooks(settings, scope="project")
    text = settings.read_text()
    assert result.changed is True
    assert "${CLAUDE_PROJECT_DIR}" in text
    assert "FileChanged" in text
    assert "PostToolUseFailure" in text
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_claude_install.py`
Expected: FAIL because `install_claude_hooks` is missing.

- [ ] **Step 3: Implement user and project installers**

Local user scope writes to `~/.claude/settings.json`. Project scope writes to
`.claude/settings.json` so hooks are available in Claude cloud sessions. Include
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `FileChanged`, `PreCompact`, and `Stop`. Preserve unrelated settings and
LoopGuard-independent hook groups.

Project hooks call a committed thin script:

```bash
"${CLAUDE_PROJECT_DIR}/.loopguard/hooks/claude-hook"
```

The script first tries the local socket. In cloud mode it sends a signed HTTPS event only when
`LOOPGUARD_CLOUD_INGEST_URL` and `LOOPGUARD_CLOUD_TOKEN` are configured; otherwise it exits 0.
Use Claude's documented `CLAUDE_CODE_REMOTE=true` signal only to label cloud execution, not as an
authentication mechanism. `FileChanged` covers explicitly watched literal files only; the
Change Journal filesystem watcher remains the source of truth for arbitrary source-file changes.

- [ ] **Step 4: Run local/cloud fixture tests**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_claude_install.py tests/adapters/test_hook_entry.py`
Expected: PASS.

- [ ] **Step 5: Commit Claude attached mode**

```bash
git add loopguard/src/loopguard loopguard/tests/adapters loopguard/README.md
git commit -m "feat: install claude local and cloud hooks"
```

### Task 5: Implement managed Codex through app-server

**Files:**
- Create: `loopguard/src/loopguard/adapters/jsonrpc.py`
- Create: `loopguard/src/loopguard/adapters/codex_managed.py`
- Test: `loopguard/tests/adapters/test_codex_managed.py`
- Test fixture: `loopguard/tests/fixtures/codex_app_server.jsonl`

- [ ] **Step 1: Write failing protocol tests against a fake process**

```python
from loopguard.adapters.base import Capability
from loopguard.adapters.codex_managed import CodexManagedAdapter


def test_managed_codex_declares_and_uses_turn_controls(fake_codex_process):
    adapter = CodexManagedAdapter(process=fake_codex_process)
    session = run(adapter.start(model="gpt-test", effort="medium", cwd="/repo"))
    run(adapter.inject(session, "Run impacted tests first."))
    run(adapter.interrupt(session))
    assert adapter.capabilities.supports(Capability.SELECT_MODEL)
    assert fake_codex_process.methods == [
        "initialize", "initialized", "thread/start", "turn/start",
        "turn/steer", "turn/interrupt",
    ]
```

- [ ] **Step 2: Verify the missing adapter**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_codex_managed.py`
Expected: FAIL because `CodexManagedAdapter` does not exist.

- [ ] **Step 3: Implement JSON-RPC lifecycle**

Start `codex app-server --listen stdio://`, perform `initialize`/`initialized`, then map:

- `thread/start` to session creation.
- `turn/start` to model, effort, `cwd`, sandbox, and initial input.
- `turn/steer` to bounded context injection.
- `turn/interrupt` to interruption.
- `item/*` and `turn/*` notifications to `ControlEvent`.
- approval server requests to typed `ActionRequest` records.

Generate and pin protocol fixtures from the installed Codex CLI in a compatibility test; do not
hand-maintain assumptions that generated schemas can verify.

- [ ] **Step 4: Run fake-server and malformed-frame tests**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_codex_managed.py`
Expected: PASS for normal lifecycle, unknown notification, process exit, malformed JSON, and
unsupported field cases.

- [ ] **Step 5: Commit the managed Codex adapter**

```bash
git add loopguard/src/loopguard/adapters loopguard/tests/adapters \
  loopguard/tests/fixtures/codex_app_server.jsonl
git commit -m "feat: manage codex through app server"
```

### Task 6: Implement managed Claude through a narrow SDK bridge

**Files:**
- Create: `loopguard/src/loopguard/adapters/claude_bridge.py`
- Create: `loopguard/src/loopguard/adapters/claude_managed.py`
- Test: `loopguard/tests/adapters/test_claude_managed.py`
- Create: `loopguard/integrations/claude-bridge/package.json`
- Create: `loopguard/integrations/claude-bridge/src/index.ts`
- Create: `loopguard/integrations/claude-bridge/src/index.test.ts`

- [ ] **Step 1: Write failing Python bridge tests**

```python
from loopguard.adapters.base import Capability
from loopguard.adapters.claude_managed import ClaudeManagedAdapter


def test_managed_claude_maps_sdk_events(fake_claude_bridge):
    adapter = ClaudeManagedAdapter(bridge=fake_claude_bridge)
    session = run(adapter.start(model="sonnet", effort="low", cwd="/repo"))
    events = collect(adapter.events(session), count=2)
    assert [event.kind.value for event in events] == ["session.started", "tool.call"]
    assert adapter.capabilities.supports(Capability.SELECT_EFFORT)
```

- [ ] **Step 2: Verify the bridge is absent**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_claude_managed.py`
Expected: FAIL because the managed Claude adapter does not exist.

- [ ] **Step 3: Implement a version-pinned Node bridge**

The Node process accepts JSONL commands:

```ts
type BridgeCommand =
  | { id: string; method: "start"; params: { cwd: string; model: string; effort: string; prompt: string } }
  | { id: string; method: "interrupt"; params: { sessionId: string } }
  | { id: string; method: "inject"; params: { sessionId: string; text: string } };
```

It uses the official `@anthropic-ai/claude-agent-sdk`, emits normalized JSONL events, and never
exposes SDK objects directly to Python. Pin the SDK version in `package-lock.json`. The Python
adapter owns process lifecycle, timeout, restart, and `ControlEvent` validation.

- [ ] **Step 4: Run Python and TypeScript tests**

Run:

```bash
cd loopguard
python -m pytest -q tests/adapters/test_claude_managed.py
cd integrations/claude-bridge
npm test
npx tsc --noEmit
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the managed Claude adapter**

```bash
git add loopguard/src/loopguard/adapters loopguard/tests/adapters \
  loopguard/integrations/claude-bridge
git commit -m "feat: manage claude through sdk bridge"
```

### Task 7: Add integration doctor and compatibility matrix

**Files:**
- Create: `loopguard/src/loopguard/adapters/doctor.py`
- Create: `loopguard/docs/integration-compatibility.md`
- Test: `loopguard/tests/adapters/test_doctor.py`
- Modify: `loopguard/src/loopguard/cli.py`

- [ ] **Step 1: Write a failing capability-report test**

```python
from loopguard.adapters.doctor import build_report


def test_report_never_claims_cloud_model_control():
    report = build_report(codex_version="test", claude_version="test")
    cloud = report.surface("codex-cloud")
    assert cloud.capabilities["observe"] in {"supported", "conditional"}
    assert cloud.capabilities["select_model"] == "unavailable"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_doctor.py`
Expected: FAIL because `build_report` is missing.

- [ ] **Step 3: Implement executable/version/config checks**

Report installed versions, hook locations, trust status where observable, daemon reachability,
bridge health, and every capability as `supported`, `conditional`, `experimental`, or
`unavailable`. A configured repo-local hook is not sufficient evidence that a hosted surface
executes it: retain `codex-cloud.observe=conditional` until the compatibility smoke test records a
real event. Render the same data in CLI JSON and `integration-compatibility.md`.

- [ ] **Step 4: Run all adapter tests**

Run: `cd loopguard && python -m pytest -q tests/adapters`
Expected: PASS without provider credentials or network access.

- [ ] **Step 5: Commit diagnostics and compatibility documentation**

```bash
git add loopguard/src/loopguard loopguard/tests/adapters \
  loopguard/docs/integration-compatibility.md
git commit -m "feat: report agent integration capabilities"
```

### Task 8: Install the daemon as a user service

**Files:**
- Create: `loopguard/src/loopguard/adapters/service_install.py`
- Create: `loopguard/src/loopguard/adapters/templates/com.loopguard.daemon.plist`
- Create: `loopguard/src/loopguard/adapters/templates/loopguardd.service`
- Test: `loopguard/tests/adapters/test_service_install.py`
- Modify: `loopguard/src/loopguard/cli.py`

- [ ] **Step 1: Write failing platform and idempotency tests**

```python
from loopguard.adapters.service_install import render_user_service


def test_macos_service_runs_foreground_daemon_at_login(tmp_path):
    rendered = render_user_service(
        platform="darwin", executable="/opt/loopguard/bin/loopguard", home=tmp_path
    )
    assert "RunAtLoad" in rendered.body
    assert "<string>daemon</string>" in rendered.body
    assert "<string>start</string>" in rendered.body
    assert "<string>--foreground</string>" in rendered.body


def test_linux_service_restarts_on_failure(tmp_path):
    rendered = render_user_service(
        platform="linux", executable="/opt/loopguard/bin/loopguard", home=tmp_path
    )
    assert "Restart=on-failure" in rendered.body
    assert "WantedBy=default.target" in rendered.body
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_service_install.py`
Expected: FAIL because the service installer is missing.

- [ ] **Step 3: Implement user-scoped installation**

On macOS, atomically write `~/Library/LaunchAgents/com.loopguard.daemon.plist`, validate with
`plutil -lint`, then use `launchctl bootstrap gui/<uid>` and `kickstart`. On Linux, write
`~/.config/systemd/user/loopguardd.service`, run `systemctl --user daemon-reload`, then
`enable --now`. Render an absolute executable and state path; never depend on an interactive
shell `PATH`.

Expose:

```bash
loopguard daemon install
loopguard daemon status --json
loopguard daemon uninstall
```

Uninstall stops and removes only the LoopGuard-owned service definition. Windows remains
capability-reported as unavailable until a separately tested named-pipe/service implementation is
added; the installer must not pretend success there.

- [ ] **Step 4: Run renderer and mocked-command tests**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_service_install.py`
Expected: PASS for install, repeat install, upgrade, uninstall, malformed template, and unsupported
platform cases.

- [ ] **Step 5: Commit automatic daemon startup**

```bash
git add loopguard/src/loopguard/adapters loopguard/tests/adapters \
  loopguard/src/loopguard/cli.py
git commit -m "feat: start loopguard daemon as a user service"
```

## Completion gate

Run:

```bash
cd loopguard
python -m pytest -q tests/adapters tests/control
loopguard integrations verify codex --json
loopguard integrations verify claude --json
```

Expected: unit tests pass, installers are idempotent, attached hooks remain fail-open when the
daemon is unavailable, and managed adapters report precise capabilities.
