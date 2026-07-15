# LoopGuard

LoopGuard is a local circuit breaker for AI coding agents. It detects repeated tool loops before another model turn, persists a redacted encrypted audit trail, and returns a structured policy decision that integrations can enforce.

The fastest proof uses no model, API key, cloud account, Docker, or permanent hooks:

```bash
cd loopguard
python -m venv .venv
source .venv/bin/activate
pip install -e ".[control]"
loopguard quickstart
```

Expected result:

```text
Loop detected before another paid turn.
Protected path: 3 encrypted events → real LoopGuard.
No model or API key used. No telemetry sent. Temporary state removed.
Next: loopguard setup --agent auto
```

## What runs today

| Surface | Purpose | Status |
|---|---|---|
| Python engine | Exact, semantic, ping-pong, and budget loop detection | Working and tested |
| Local control plane | Framed owner-authenticated daemon, encrypted SQLite log, crash replay, durable handlers | Working on POSIX; Windows named pipe remains capability-gated |
| CLI | Offline quickstart, foreground daemon, doctor, error explanations, layered configuration | Working |
| FastAPI + Expo demo | Streams real demo-agent runs to web/mobile controls | Prototype; open CORS, no production auth, in-memory runs |
| Managed Codex/Claude hooks | Idempotent setup, trust, uninstall, compatibility doctor | Planned in the integration milestone |

LoopGuard does not advertise a capability until its platform integration test passes. Background daemon startup therefore refuses unmanaged forking; use `loopguard daemon start --foreground` until managed service installation ships.

## How the protected path works

```text
agent hook
  → versioned bounded local frame
  → schema validation and secret redaction
  → AES-256-GCM encrypted SQLite commit
  → per-session deterministic LoopGuard detector
  → allow or request_approval policy decision
  → durable idempotent secondary handlers
```

The core decision is synchronous and durable before acknowledgement. A crash after persistence is replayed without double-applying detector state or completed handler work.

## Useful commands

```bash
loopguard quickstart              # isolated offline proof
loopguard quickstart --json       # stable automation output
loopguard daemon start --foreground
loopguard doctor --json
loopguard explain LGD-DAEMON-001
loopguard config show --json
```

See [offline quickstart](docs/getting-started/quickstart.md), [CLI reference](docs/reference/cli.md), [configuration](docs/reference/configuration.md), and [errors](docs/reference/errors.md).

## Repository map

- [`loopguard/`](loopguard/) — Python engine, daemon, CLI, server prototype, and tests.
- [`cloud-app/`](cloud-app/) — Expo monitoring/intervention prototype.
- [`docs/superpowers/`](docs/superpowers/) — reviewed production roadmap, specifications, and implementation progress.

## Development checks

```bash
cd loopguard
pip install -e ".[all-dev]"
python -m ruff check src tests
python -m pytest -q -W error::RuntimeWarning
python -m loopguard.cli_docs --check
python -m build
```

CI runs the foundation suite on Ubuntu, macOS, and Windows. Windows control transport stays disabled until a real current-user named-pipe backend passes native tests.

## Production honesty

The repository is being productized milestone by milestone. The local event contract, encrypted store, daemon protocol, offline quickstart, diagnostics, and their safety tests are implemented. Managed agent adapters, authenticated cloud relay, production web/iOS control surfaces, pipeline healing, and final operations hardening remain on the tracked roadmap. The FastAPI/Expo demo must not be exposed publicly in its current form.
