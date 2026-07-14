# Codex and Claude Integrations Implementation Plan

> **For implementation agents:** Execute this plan task-by-task and track every checkbox. In Codex, use the native plan, debugging, review, and verification tools available in the host. In Claude Code, use `superpowers:subagent-driven-development` or `superpowers:executing-plans` when installed. A missing named workflow is never a blocker; perform the equivalent TDD and verification steps directly.

> **Command convention:** Resolve one absolute, supported virtualenv interpreter as `$PY`. In every shell snippet, read bare `python` as `$PY`, `python -m pip` as `$PY -m pip`, and `ruff` as `$PY -m ruff`; never assume those executables are on `PATH`.

**Goal:** Automatically attach LoopGuard to native Codex and Claude sessions and provide managed adapters for surfaces that expose full session control.

**Architecture:** Every integration normalizes provider events into `ControlEvent` and declares capabilities. Attached adapters communicate with `loopguardd` through a fast hook client; managed adapters implement start, event stream, injection, interruption, permissions, model, and effort through official programmatic interfaces.

**Tech Stack:** Python 3.11+, asyncio subprocesses, JSONL/JSON-RPC, Codex hooks/app-server, Claude hooks/Agent SDK, pytest

**Execution tranches:** Tasks 1–4 are the attached-integration tranche and run immediately after
the foundation. The proof plan and context/worktree plan then establish their prerequisites before
Tasks 5–8 add managed execution, final compatibility reporting, and service installation. Do not
start a managed adapter without a recorded proof contract/baseline and a leased worktree.

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

from loopguard.control.decisions import ActionRequest
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

    async def attach(self, session: SessionRef) -> None: ...
    async def start(self, request: ManagedRunRequest) -> SessionRef: ...
    async def interrupt(self, session: SessionRef) -> CapabilityError | None: ...
    async def inject(self, session: SessionRef, context: str) -> CapabilityError | None: ...
    async def resolve_action(
        self, session: SessionRef, action: ActionRequest
    ) -> CapabilityError | None: ...
    async def events(self, session: SessionRef) -> AsyncIterator[ControlEvent]: ...
```

Define `ManagedRunRequest` once in `base.py` with repository/worktree identity, prompt, model,
effort, sandbox, permission policy, proof-contract ID, and token/cost ceilings. Every method must
return a typed capability or lifecycle error for unsupported, unknown-session, stale-state,
process-exited, timeout, and protocol-version cases. Add a contract test suite that runs against
both managed adapters and a deliberately observation-only adapter; no adapter may silently no-op.

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
             "tool_input": {"command": "pytest"}},
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

`HookClient.send()` must use the foundation's versioned bounded frame protocol, read the normalized
policy decision from the acknowledgement, and time out after 100 ms by default. It resolves the
socket/pipe from trusted local configuration, never hook input. `process_hook()` returns exit 0 on
observation failure and a typed blocking response only when a configured fail-closed policy
explicitly requires it. Map `allow`, `warn`, `pause`, `interrupt`, `inject`, and
`request_approval` only to actions supported by the current vendor hook lifecycle; unsupported
decisions become visible capability warnings, not invented controls.

Derive `host_id`, canonical repository identity, and any session binding locally. Treat hook
payload fields as untrusted observations. Redact before transport, cap input/output size, and test
malformed UTF-8/JSON, symlinked working directories, repository deletion, timeout, daemon restart,
unknown decision action, and a real repeated-tool event reaching the daemon's existing detector.
Codex `PreToolUse` currently covers Bash, `apply_patch`, and MCP calls but is not a complete
enforcement boundary; the capability report and protected-session UI must disclose that exact
coverage rather than imply every possible tool path is intercepted.

Normalize `PermissionRequest` as a typed `ActionRequest`, not as a generic tool call. A renderer
may allow or deny only when the vendor event explicitly supports that decision. Codex
`PreToolUse` does not currently support an `ask` response, so `request_approval` must never be
rendered there; Codex approval policy belongs to `PermissionRequest`, while managed adapters use
their native approval callback/server-request surface. Tests cover stale, duplicate, and
daemon-timeout approval requests without turning a missing decision into implicit approval.

- [ ] **Step 4: Run hook tests and a latency test**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_hook_entry.py`
Expected: PASS, including a test asserting p95 fake-client processing below 20 ms.

- [ ] **Step 5: Commit the hook transport**

```bash
git add loopguard/src/loopguard/adapters loopguard/tests/adapters
git commit -m "feat: normalize native agent hooks"
```

### Task 3: Package Codex plugin hooks with a safe compatibility fallback

**Files:**
- Create: `loopguard/src/loopguard/adapters/codex_hooks.py`
- Create: `loopguard/integrations/codex-plugin/.codex-plugin/plugin.json`
- Create: `loopguard/integrations/codex-plugin/hooks/hooks.json`
- Create: `loopguard/integrations/codex-plugin/bin/loopguard-hook`
- Create: `loopguard/src/loopguard/adapters/templates/codex-hooks-fallback.json`
- Create: `loopguard/src/loopguard/adapters/templates/codex-hook-fallback`
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
    assert commands.count("hook-entry codex") == 7
    assert first.changed is True
    assert second.changed is False


def test_codex_project_install_uses_committed_git_root_wrapper(tmp_path):
    repo = make_git_repo(tmp_path)
    result = install_codex_hooks(
        repo / ".codex" / "hooks.json", scope="project", plugin_available=False
    )
    assert result.changed is True
    assert (repo / ".loopguard" / "hooks" / "codex-hook").exists()
    assert "git rev-parse --show-toplevel" in (repo / ".codex" / "hooks.json").read_text()
```

- [ ] **Step 2: Run the test and verify failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_codex_install.py`
Expected: FAIL because the installer does not exist.

- [ ] **Step 3: Implement LoopGuard entries with stable command signatures**

Package the primary integration as a versioned Codex plugin with `.codex-plugin/plugin.json`,
`hooks/hooks.json`, and a plugin-relative executable. Select the supported V1 subset
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`,
and `Stop`; do not invent `FileChanged` or `PostToolUseFailure` events for Codex. Plugin install is
explicit, previewed, version/checksum pinned, and remains subject to Codex's hook trust review.

Use direct user scope (`~/.codex/hooks.json`) or project scope (`.codex/hooks.json`) only as a
version-gated fallback when the installed Codex version cannot activate the plugin. Plugin and
fallback modes are mutually exclusive: re-read effective hook sources after activation and never
install both. Identify fallback handlers by an exact versioned command prefix and wrapper path;
do not add private fields to Codex hook objects. Acquire a file lock, parse and validate before
merging, preserve unrelated/unknown fields and handler order, write through a temporary sibling
plus `os.replace()`, re-read/validate, and retain a timestamped backup if validation fails.

Fallback project scope installs a committed `.loopguard/hooks/codex-hook` wrapper and resolves it from the
canonical Git root, matching Codex's documented project-hook trust behavior. The wrapper first
tries the local socket. If the socket is absent, it may send a signed HTTPS event only when an
explicit LoopGuard ingest URL and repository-scoped hook credential are present in the
environment; otherwise it exits 0. Sign the canonical method/path/timestamp/nonce/body hash,
include credential key ID, enforce a small request/response/time budget, and never follow
cross-origin redirects. The cloud plan owns credential issue/rotation/revocation, repository
binding, replay protection, and `/v1/hook-events` verification. Treat Codex cloud execution of
repo-local hooks as conditional until a compatibility smoke test against the installed/current
Codex surface proves a signed event arrived under the correct tenant/repository. Never advertise
live cloud observation merely because the files were committed.

Codex requires users to review and trust non-managed hook definitions, including plugin hooks.
Preserve that boundary:
installation reports `trust_required` and tells the user to review the definition in `/hooks`;
verification remains non-healthy until Codex reports the exact hook hash trusted. Never invoke
`--dangerously-bypass-hook-trust` or edit Codex trust state behind the user's back.

Expose:

```bash
loopguard integrations install codex
loopguard integrations verify codex --json
loopguard integrations uninstall codex
```

Uninstall removes only the exact LoopGuard plugin version or fallback handlers. It leaves
unrelated plugins/settings and any matcher group containing unrelated handlers intact. Upgrade
migrates fallback-to-plugin only after the effective plugin hook hash is visible and never runs
both sources during the transition.

- [ ] **Step 4: Run installer and CLI tests**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_codex_install.py tests/test_cli_smoke.py`
Expected: PASS.

- [ ] **Step 5: Commit Codex attached mode**

```bash
git add loopguard/src/loopguard loopguard/tests/adapters loopguard/integrations/codex-plugin
git commit -m "feat: install codex lifecycle hooks"
```

### Task 4: Package Claude plugin hooks for local and cloud sessions

**Files:**
- Create: `loopguard/src/loopguard/adapters/claude_hooks.py`
- Create: `loopguard/integrations/claude-plugin/.claude-plugin/plugin.json`
- Create: `loopguard/integrations/claude-plugin/hooks/hooks.json`
- Create: `loopguard/integrations/claude-plugin/bin/loopguard-hook`
- Create: `loopguard/src/loopguard/adapters/templates/claude-settings-fallback.json`
- Test: `loopguard/tests/adapters/test_claude_install.py`
- Modify: `loopguard/README.md`

- [ ] **Step 1: Write failing user/project scope tests**

```python
from loopguard.adapters.claude_hooks import install_claude_hooks


def test_claude_project_install_uses_project_relative_entry(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    result = install_claude_hooks(settings, scope="project", plugin_available=False)
    text = settings.read_text()
    assert result.changed is True
    assert "${CLAUDE_PROJECT_DIR}" in text
    assert "FileChanged" in text
    assert "PostToolUseFailure" in text
    assert "PermissionRequest" in text
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_claude_install.py`
Expected: FAIL because `install_claude_hooks` is missing.

- [ ] **Step 3: Implement user and project installers**

Package a versioned Claude plugin using `.claude-plugin/plugin.json`, `hooks/hooks.json`, and
`${CLAUDE_PLUGIN_ROOT}`/exec-form paths. This is the primary reusable local integration. For
repository-scoped cloud execution, or when the installed Claude version cannot activate the
plugin, use a version-gated `.claude/settings.json` fallback. Never activate plugin and fallback
handlers together. Include `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
`PostToolUse`, `PostToolUseFailure`, `FileChanged`, `PreCompact`, and `Stop`. Preserve unrelated
settings and hook groups with the same lock/atomic-write/re-read/backup contract as Codex.

Fallback project hooks call a committed thin script:

```bash
"${CLAUDE_PROJECT_DIR}/.loopguard/hooks/claude-hook"
```

The script first tries the local socket. In cloud mode it sends a signed HTTPS event only when
`LOOPGUARD_CLOUD_INGEST_URL`, credential key ID, and repository-scoped hook secret are configured;
otherwise it exits 0. Use the same canonical request signature, timestamp/nonce, no-redirect,
timeout, rotation, revocation, repository-binding, and replay contract as Codex hooks. Never place
credentials in committed settings or wrapper files.
Use Claude's documented `CLAUDE_CODE_REMOTE=true` signal only to label cloud execution, not as an
authentication mechanism. `FileChanged` covers explicitly watched literal files only; the
Change Journal filesystem watcher remains the source of truth for arbitrary source-file changes.
Managed policy that disallows user/project/plugin hooks reports `unavailable` with the policy
source; setup never bypasses the administrator boundary.

- [ ] **Step 4: Run local/cloud fixture tests**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_claude_install.py tests/adapters/test_hook_entry.py`
Expected: PASS.

- [ ] **Step 5: Commit Claude attached mode**

```bash
git add loopguard/src/loopguard loopguard/tests/adapters loopguard/README.md \
  loopguard/integrations/claude-plugin
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
    run(adapter.steer_active_turn(session, "Run impacted tests first."))
    run(adapter.interrupt(session))
    assert adapter.capabilities.supports(Capability.SELECT_MODEL)
    assert fake_codex_process.methods == [
        "initialize", "initialized", "model/list", "thread/start", "turn/start",
        "turn/steer", "turn/interrupt",
    ]


def test_managed_codex_injects_between_turns(fake_codex_process):
    adapter = CodexManagedAdapter(process=fake_codex_process)
    session = run(adapter.start(model="gpt-test", effort="medium", cwd="/repo"))
    fake_codex_process.complete_active_turn()
    run(adapter.inject_between_turns(session, "Previously verified context."))
    assert fake_codex_process.methods[-1] == "thread/inject_items"
```

- [ ] **Step 2: Verify the missing adapter**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_codex_managed.py`
Expected: FAIL because `CodexManagedAdapter` does not exist.

- [ ] **Step 3: Implement JSON-RPC lifecycle**

Start `codex app-server --listen stdio://`, perform `initialize` with stable LoopGuard
`clientInfo` followed by `initialized`, then map:

- `thread/start` to session creation.
- `model/list` and `modelProvider/capabilities/read` to validated model/effort availability.
- `turn/start` to model, effort, `cwd`, approval policy, sandbox policy, and initial input.
- `turn/steer` only to the authoritative active turn, with its exact `expectedTurnId`.
- `thread/inject_items` only to bounded, validated model-visible context between turns.
- `turn/interrupt` to interruption.
- `item/*` and `turn/*` notifications to `ControlEvent`.
- approval server requests to typed `ActionRequest` records.

Generate and pin protocol fixtures from the installed Codex CLI in a compatibility test; do not
hand-maintain assumptions that generated schemas can verify.

Maintain a per-thread locked lifecycle from server notifications. Never fall back from a failed
`turn/steer` to `thread/inject_items`, or vice versa: they have different ordering semantics.
Validate thread ownership, item role/schema/size, correlation IDs, and terminal state before every
control. Stable API fields are the production baseline; experimental methods require an explicit
capability flag, isolated tests, and an `experimental` capability label.

The initialization `clientInfo.name`, title, and version are versioned product constants and are
included in compatibility evidence. Public enterprise support is an external launch gate: contact
OpenAI to register LoopGuard as a known client before claiming enterprise Compliance Logs support.
Development and local tests may proceed without that registration but must not advertise it.

Start only inside a worktree lease created by the context plan and only after the verification
plan records the proof contract plus pre-mutation baseline. Persist vendor thread/turn IDs and
state transitions so daemon restart reports `recovering`, `reattached`, or `orphaned` instead of
creating a duplicate turn. Bound stdout/stderr/event queues, reject responses with unknown request
IDs, time out initialization/turn/control operations independently, terminate the child process
tree on cancellation, and redact process diagnostics before persistence.

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
- Create: `loopguard/integrations/claude-bridge/package-lock.json`
- Create: `loopguard/integrations/claude-bridge/tsconfig.json`
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
adapter owns process lifecycle, per-method timeouts, bounded queues, restart/recovery state,
process-tree cleanup, and `ControlEvent` validation. Like Codex, a managed Claude run requires a
leased worktree and recorded pre-mutation proof baseline. The bridge binds every response/event to
the started session and rejects unknown IDs, unsupported protocol versions, oversized frames, and
events after terminal state.

Use streaming-input mode for multi-turn sessions. Bind `effort` to the SDK's supported named
levels, query and record supported models during initialization, use `Query.interrupt()` for
cancellation, and keep between-turn input distinct from an in-flight permission decision.
`canUseTool` and SDK permission modes are the managed approval surface; do not simulate a remote
approval by mutating an already-issued tool request. Record SDK-reported usage and label
client-computed cost as an estimate rather than authoritative billing.

Authenticate managed Claude only with user-supplied API credentials or a documented supported
provider such as Bedrock, Vertex AI, or Azure AI Foundry. Unless Anthropic grants explicit written
approval, LoopGuard must not offer or proxy `claude.ai` login, subscription rate limits, or session
credentials as product authentication. Attached Claude Code hooks remain available independently
of managed-mode API credentials, and the capability matrix must make that distinction explicit.

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
- Create: `loopguard/src/loopguard/adapters/setup.py`
- Create: `loopguard/docs/integration-compatibility.md`
- Create: `loopguard/docs/getting-started-protected-session.md`
- Test: `loopguard/tests/adapters/test_doctor.py`
- Test: `loopguard/tests/adapters/test_setup.py`
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

Setup tests must prove dry-run has no writes, existing config is preserved, repeated setup is a
no-op, trust remains human-owned, partial failure produces an exact resume command, non-interactive
mode is deterministic, safe doctor fixes stay inside LoopGuard-owned state, uninstall preserves
unrelated hooks/data, and purge requires explicit confirmation.

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/adapters/test_doctor.py tests/adapters/test_setup.py`
Expected: FAIL because diagnostics and guided setup are missing.

- [ ] **Step 3: Implement executable/version/config checks**

Report installed versions, active plugin/fallback source, plugin version/checksum, hook locations,
trust status where observable, daemon reachability,
bridge health, and every capability as `supported`, `conditional`, `experimental`, or
`unavailable`. A configured repo-local hook is not sufficient evidence that a hosted surface
executes it: retain `codex-cloud.observe=conditional` until the compatibility smoke test records a
real event. Render the same data in CLI JSON and `integration-compatibility.md`.

Expose one guided golden path:

```bash
loopguard setup --agent auto --scope user
loopguard setup --agent auto --scope project --dry-run
loopguard doctor --fix-safe --json
loopguard uninstall --dry-run
```

`setup` detects installed Codex/Claude versions, validates the daemon extra, previews every file,
service, plugin, and fallback-hook change, installs the user service, activates a compatible
plugin when possible (or exactly one fallback), starts the daemon, and runs a synthetic end-to-end
event. It never approves vendor hook trust; it
prints the exact trust-review step and remains `attention_required` until verified. Interactive
mode defaults to detected local agents and local-only operation; non-interactive mode requires
explicit scope/agents and returns stable exit/error codes.

`doctor --fix-safe` may restart LoopGuard's own service, repair owner-only permissions, regenerate
LoopGuard-owned wrappers, or refresh compatibility metadata. It may not change vendor trust,
delete data, overwrite unrelated configuration, install cloud credentials, or bypass policy.
`uninstall` previews and removes only LoopGuard-owned hooks/services; local data is retained by
default and a separate explicit `loopguard data purge` command requires confirmation.

The getting-started guide targets a protected existing session in under five minutes: run setup,
complete explicit trust, start normal `codex`/`claude`, then see `loopguard sessions` report
`protected_attached` plus the exact event/tool coverage for that vendor/version. Never collapse
partial hook coverage into a generic “fully protected” claim. Include expected output and
troubleshooting for every attention state.

Record local, privacy-safe setup step timings and outcome codes (`install_detected`, service,
hook merge, trust pending, synthetic event, protected session) without paths, repository names,
prompts, or identifiers. `loopguard dx report --local` shows median/last time-to-quickstart and
time-to-protected-session. Upload is off by default and requires explicit telemetry opt-in.
`loopguard doctor --bundle <path>` creates a redaction-previewed diagnostic archive only after
confirmation. `loopguard feedback` prints the version-prefilled issue/support route.

- [ ] **Step 4: Run all adapter tests**

Run: `cd loopguard && python -m pytest -q tests/adapters`
Expected: PASS without provider credentials or network access.

- [ ] **Step 5: Commit diagnostics and compatibility documentation**

```bash
git add loopguard/src/loopguard loopguard/tests/adapters \
  loopguard/docs/integration-compatibility.md \
  loopguard/docs/getting-started-protected-session.md
git commit -m "feat: report agent integration capabilities"
```

### Task 8: Install the daemon as a user service

**Files:**
- Create: `loopguard/src/loopguard/adapters/service_install.py`
- Create: `loopguard/src/loopguard/adapters/templates/com.loopguard.daemon.plist`
- Create: `loopguard/src/loopguard/adapters/templates/loopguardd.service`
- Create: `loopguard/src/loopguard/adapters/templates/loopguardd-windows.xml`
- Test: `loopguard/tests/adapters/test_service_install.py`
- Test: `loopguard/tests/adapters/test_service_install_windows.py`
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
shell `PATH`. On Windows, install a current-user scheduled task (or user-scoped service where
policy permits) that runs the daemon foreground entrypoint with the named-pipe transport and
current-user ACL. Verify command quoting, SID binding, restart policy, status, upgrade, and
uninstall on a real Windows CI runner.

Expose:

```bash
loopguard daemon install
loopguard daemon status --json
loopguard daemon uninstall
```

Uninstall stops and removes only the LoopGuard-owned service definition. Capability reporting
marks Windows automatic startup supported only after the named-pipe and service integration matrix
passes on Windows; before that it reports `experimental`, never false success.

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
cd integrations/claude-bridge
npm ci
npm test
npx tsc --noEmit
cd ../..
loopguard integrations verify codex --json
loopguard integrations verify claude --json
```

Expected: unit tests pass, installers are idempotent, attached hooks remain fail-open when the
daemon is unavailable, signed cloud hook fixtures bind to the correct repository, managed adapters
report precise capabilities, and the daemon startup/transport suite passes on macOS, Linux, and
Windows CI.
