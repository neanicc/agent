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
