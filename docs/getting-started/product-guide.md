# LoopGuard in plain English

LoopGuard is a circuit breaker and control room for AI coding agents. It notices when an agent is
repeating work, stores a privacy-safe record, asks for approval before risky actions, and can prove
a proposed pipeline repair before opening a draft pull request.

In one sentence for a friend: **it is a seat belt and flight recorder for coding agents—it stops
obvious loops, records what happened safely, and requires proof before risky changes move forward.**

## What changed

The repository now has four connected product layers:

1. A local Python guard receives bounded agent events, removes secrets, encrypts them, and makes a
   deterministic allow-or-stop decision before another model turn.
2. Managed Codex and Claude adapters install idempotent hooks, verify repository trust, and keep
   local guarding useful even if the hosted service is unavailable.
3. A multi-tenant control API streams sessions to authenticated web and iOS clients. Signed,
   expiring, idempotent actions can interrupt, redirect, request evidence, or approve a bounded
   operation.
4. Auto-Heal reproduces supported pipeline failures in an isolated worker, validates a candidate
   against required tests, and may publish a draft repair PR. It cannot merge or deploy.

Hosted operations add tenant isolation, observability, retention/export/deletion, metering,
billing reconciliation, enterprise provisioning, consent-bound support access, signed releases,
backup/restore tooling, and deployable AWS/Kubernetes definitions.

## Try the safety loop

The quickest proof is local and disposable:

```bash
cd loopguard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[control]"
loopguard quickstart
```

The quickstart passes three synthetic events through the real local protocol and detector. It uses
no model, API key, cloud account, permanent hook, or telemetry.

## Protect a supported agent

After installing all local integration dependencies:

```bash
python -m pip install -e ".[all-dev]"
loopguard setup --agent auto
loopguard doctor
loopguard daemon start --foreground
```

Review the setup preview and repository trust request before accepting them. Installation is
idempotent: rerunning it updates the managed block without duplicating hooks. Use
`loopguard integrations uninstall codex` or `loopguard integrations uninstall claude` to remove
only LoopGuard-managed configuration for that agent.

If the doctor says an adapter is unsupported, local guard protection remains available through the
documented foreground path. LoopGuard does not silently claim an unverified integration.

## Use the control surfaces

The web console and iOS app show the same server-authorized model:

- Inbox prioritizes sessions needing a decision.
- Runs shows the ordered event timeline and replay state.
- Changes previews proposed signed actions before confirmation.
- Verification explains what was tested and what remains inconclusive.
- Hosts, policies, costs, devices, and audit expose operational state without revealing raw secrets.

Every remote action has a target, expected state version, expiration, signature, idempotency key,
and audit record. A stale or ambiguous action is reconciled by action ID; the client does not
blindly retry.

## Understand Auto-Heal

Auto-Heal starts only for an eligible, trusted repository and supported failure. A separate repair
worker:

1. fetches an allowlisted commit;
2. builds an isolated sandbox with no host or container socket;
3. reproduces the failure;
4. creates a bounded candidate;
5. runs the declared verification suite;
6. computes policy evidence;
7. waits for approval when required; and
8. opens only a draft PR through the approved GitHub installation.

If evidence is incomplete, the result stays inconclusive. If a budget, quota, policy, sandbox, or
verification gate fails, no PR is published.

## Production status

The code and local validation are production-oriented, but public GA is currently **NO-GO**. Live
staging load, restore, regional failover/failback, signed release, real identity/payment-provider
integration, production infrastructure validation, legal terms, and named human sign-offs remain
external gates. In-memory enterprise identity and support stores are test adapters; hosted startup
requires durable injected adapters.

The old `cloud-app` Expo client is a one-release local/demo fallback and is not a production
artifact. Use the authenticated web console or native iOS client for the implemented product.

See [production readiness](../operations/production-readiness.md) for the evidence ledger and
[pricing and limits](../product/pricing-and-limits.md) for what is and is not activated.
