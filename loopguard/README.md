# LoopGuard Python package

LoopGuard detects runaway AI-agent behavior locally and returns an enforceable decision before another expensive turn.

## Install and prove it offline

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[control]"
loopguard quickstart
```

`quickstart` exercises the real framed daemon, recursive redaction, encrypted event store, event projection, existing detector, durable acknowledgement, and cleanup. It deliberately uses zero provider keys and sends zero telemetry.

## Run the local daemon

```bash
loopguard daemon start --foreground
```

State defaults to `~/.loopguard` or `$LOOPGUARD_HOME`:

- `events.db` — WAL SQLite with encrypted event bodies and authenticated cursor metadata;
- `loopguard.sock` — POSIX owner-only socket with peer-UID verification;
- `loopguard.pid` — owner-only foreground process marker;
- `config.json` — optional strict layered configuration.

Background start intentionally returns `LGD-CAP-005` until managed service installers are implemented. Windows named-pipe support is similarly unadvertised until native tests pass.

## Diagnose and configure

```bash
loopguard doctor
loopguard doctor --json --verbose
loopguard explain LGD-DAEMON-001
loopguard config path
loopguard config show --json
loopguard config validate
```

Doctor checks protocol/schema versions, daemon reachability, peer identity capability, owner-only permissions, SQLite integrity, encryption-key availability, dispatch lag/failures, cursor positions, and integration trust without printing keys or raw tracebacks.

Configuration precedence is defaults → file → environment → CLI. Telemetry defaults off. See [configuration](../docs/reference/configuration.md) and [error reference](../docs/reference/errors.md).

## Protect Codex and Claude Code sessions

Start the local daemon, then install the native plugin for the agent you use:

```bash
loopguard integrations install codex
loopguard integrations verify codex
loopguard integrations install claude
loopguard integrations verify claude
```

The installers stage versioned, checksum-pinned plugin files and preserve unrelated agent settings. Codex keeps hook trust under the user's control. Claude Code respects administrator-managed hook policy; LoopGuard reports the policy file and stays unavailable instead of bypassing it. Remove only LoopGuard-owned entries with `loopguard integrations uninstall codex` or `loopguard integrations uninstall claude`.

Older agent releases can use an explicit `--fallback` after review. Plugin and fallback hooks are mutually exclusive, so the same event is never sent twice. Claude's `FileChanged` hook watches only `.env`, `.envrc`, and `CLAUDE.md`; it is not a general filesystem monitor.

Repository hooks for Claude Code remote sessions are opt-in and fail open when neither transport is reachable. Configure all three runtime secrets, keep them out of Git, uninstall the local Claude plugin, and then prepare the reviewed project hook:

```bash
export LOOPGUARD_CLOUD_INGEST_URL=https://loopguard.example/v1/hook-events
export LOOPGUARD_HOOK_KEY_ID=repository-key-id
export LOOPGUARD_HOOK_SECRET=URL_SAFE_BASE64_256_BIT_SECRET
loopguard integrations install claude --cloud --scope project
```

Cloud events use a short-timeout signed HTTPS request with timestamp, nonce, and body hash; redirects are refused. `CLAUDE_CODE_REMOTE=true` is treated only as an environment label, never as authentication. Setup reports cloud delivery as `conditional` until the deployment endpoint verifies a real signed smoke event for the intended tenant and repository.

## Embed the engine directly

```python
from loopguard import LoopEvent, LoopGuard, LoopGuardConfig

guard = LoopGuard(
    LoopGuardConfig(action="raise", enable_judge=False)
)

decision = guard.observe(
    LoopEvent(
        run_id="agent-session",
        agent="repo-agent",
        kind="tool_call",
        tool_name="read_file",
        tool_args={"path": "package.json"},
    )
)
```

Layer 1 is deterministic, local, and free: exact repetition, local hashing-based semantic similarity, A-B-A-B ping-pong, and token/cost/tool-call budgets. Layer 2 is an optional provider-backed judge and is disabled in the control daemon unless explicitly configured by a later policy milestone.

## Optional extras

```bash
pip install "loopguard[control]"   # encryption, platform key store, platform adapter deps
pip install "loopguard[server]"    # FastAPI/uvicorn demo server
pip install "loopguard[litellm]"   # LiteLLM provider routing
pip install "loopguard[cerebras]"  # direct Cerebras SDK
pip install "loopguard[all-dev]"   # every current product extra + test/build tools
```

## Existing demonstrations

The legacy demo commands remain available but are secondary to the zero-key product quickstart:

```bash
loopguard demo
loopguard projects
loopguard run npm-manifest --mode auto
loopguard serve --port 8000
```

Live agent demos require a provider key and may spend tokens. The FastAPI server and Expo app are local/LAN prototypes with open CORS, no authentication, and in-memory run state; do not expose them publicly.

## Verify a checkout

```bash
pip install -e ".[all-dev]"
python -m ruff check src tests
python -m pytest -q -W error::RuntimeWarning
python -m loopguard.cli_docs --check
python -m build
```

The local daemon protocol is bounded and versioned; event payloads are redacted before AES-256-GCM persistence; cursor domains are independent; core replay is deterministic; and handler delivery IDs are stable across retries and restarts.
