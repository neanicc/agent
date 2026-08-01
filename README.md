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

## What is implemented

| Surface | Purpose | Status |
|---|---|---|
| Python engine | Exact, semantic, ping-pong, and budget loop detection | Working and tested |
| Local control plane | Framed owner-authenticated daemon, encrypted SQLite log, crash replay, durable handlers | Working on POSIX; Windows named pipe remains capability-gated |
| CLI and managed hooks | Quickstart, daemon, doctor, setup/trust/uninstall, updates and migration checks | Implemented with platform capability gates |
| Hosted control API | Tenant-scoped ingest, replay, signed actions, audit, retention, metering, billing, SCIM and support policy | Implemented; hosted startup requires durable adapters |
| Web console and public docs | Authenticated monitoring/control plus searchable public documentation | Implemented and browser-tested locally |
| Native iOS client | Inbox, runs, actions, policies, costs, devices, audit and reconnect behavior | Implemented for the iOS 26 simulator |
| Auto-Heal | Isolated reproduction, bounded candidate, verification and draft repair PR | Implemented for supported eligible pipelines; cannot merge/deploy |
| AWS/Kubernetes operations | Terraform, Helm, signed deploy, backup/restore, DR and incident runbooks | Prepared; live validation and human approval remain |

LoopGuard does not advertise a capability until its platform integration test passes. Unsupported
agent/platform combinations fail closed, and the local circuit breaker remains useful when hosted
services are unavailable.

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

See the [plain-English product guide](docs/getting-started/product-guide.md),
[offline quickstart](docs/getting-started/quickstart.md),
[first-session tutorial](docs/tutorials/protect-first-session.md),
[CLI reference](docs/reference/cli.md), [configuration](docs/reference/configuration.md), and
[errors](docs/reference/errors.md).

## Repository map

- [`loopguard/`](loopguard/) — Python engine, local daemon, CLI, integrations, repair engine, and tests.
- [`services/control-api/`](services/control-api/) — multi-tenant hosted API and worker.
- [`apps/web/`](apps/web/) — authenticated web console and public docs.
- [`apps/ios/`](apps/ios/) — native SwiftUI control client.
- [`infra/`](infra/) — Terraform, Helm, and rendered-manifest policy tests.
- [`cloud-app/`](cloud-app/) — legacy Expo prototype retained for migration comparison.
- [`docs/superpowers/`](docs/superpowers/) — reviewed specifications, implementation plans, and progress.

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

Public beta and GA are currently **NO-GO**. The implementation includes the production safety and
operations architecture, but live staging load, encrypted restore, migration, regional
failover/failback, signed-release, real identity/payment-provider, and infrastructure validation
still require approved environments. Durable hosted adapters, legal terms, and engineering,
security, operations, privacy, product, and support sign-offs are also outstanding. The repository
has no license; distribution remains `awaiting_owner_legal_choice`.

The legacy Expo demo must not be exposed publicly. See the
[production readiness ledger](docs/operations/production-readiness.md) for exact evidence and
remaining authority.
