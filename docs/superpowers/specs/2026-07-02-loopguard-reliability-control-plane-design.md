# LoopGuard Reliability Control Plane Design

**Date:** 2026-07-02
**Status:** Approved direction; production-readiness refresh 2026-07-14
**Audience:** Product, engineering, security, and design

## Product definition

LoopGuard becomes a local-first reliability control plane for coding agents. It observes native
Codex and Claude sessions, detects waste and conflicting work, shares compact verified context,
proves that code changes did not introduce regressions, selects appropriate compute when it owns
the run, repairs selected pipeline failures in isolated sandboxes, and exposes safe approvals
through native iOS and web clients.

The product is not a replacement chat client and not a generic observability dashboard. Its
primary value is preventing wasted work and unverified changes across coding agents.

## Requirements

1. Attach automatically to supported local Codex and Claude sessions.
2. Observe supported Codex and Claude cloud sessions without requiring a public listener.
3. Permit full steering, model selection, and effort selection in LoopGuard-managed sessions.
4. Make active runs and explicit approval actions available from iOS and web.
5. Detect exact, semantic, ping-pong, and budget loops without model tokens.
6. Keep optional judgment and context injection within explicit token and cost budgets.
7. Detect changes from agents, editors, scripts, and Git operations.
8. Give agents relevant current context without copying full transcripts or hidden reasoning.
9. Establish a pre-change baseline and detect newly introduced regressions.
10. Encode user and repository design preferences as an editable policy, not an opaque persona.
11. Reduce browser-test latency without weakening final verification.
12. Reproduce selected data-pipeline failures, evaluate isolated repairs, and open evidence-rich
    draft pull requests.
13. Operate safely in single-user local mode and multi-tenant hosted mode.
14. Provide auditable decisions, exact action provenance, retention controls, and deletion.
15. Compute effective capabilities from policy, entitlement, role, host/adapter version, trust, and
    fresh health so clients never expose unsupported navigation or actions.

## Non-goals

- Reading or transferring hidden chain-of-thought.
- Guaranteeing that every short session is cheaper.
- Sharing one writable worktree between concurrent agents.
- Automatically deploying an AI-generated pipeline repair in the first production release.
- Replacing Codex Remote Connections or Claude Remote Control as a generic terminal mirror.
- Using an LLM for routing every prompt or for monitoring ordinary file changes.

## Architecture

```text
Codex hooks/app-server     Claude hooks/Agent SDK
Cloud repo hooks           Git, filesystem, CI, Airflow, OpenLineage
              │
      Vendor-specific adapters
              │
     Versioned ControlEvent stream
              │
          loopguardd
 ┌────────────┼──────────────┬───────────────┐
 Guard     Context       Verification      Router
              │               │               │
         Preferences      Proof record    Managed run
              └───────────────┼───────────────┘
                           Heal
                             │
                Local encrypted event store
                             │
                 Outbound authenticated relay
                             │
      Hosted API, Postgres, workflow workers, object storage
                             │
                    Native iOS and web
```

## Stable core and new boundary

The existing `LoopEvent -> LoopGuard -> LoopDecision` flow remains the inner circuit breaker.
It continues to own deterministic loop detection, optional judge consultation, cost ceilings, and
the immediate loop action.

The product layer must not turn `LoopEvent` into a universal event object. It introduces:

- `ControlEvent`: versioned envelope for session, tool, file, test, pipeline, and operator events.
- `PolicyDecision`: allow, warn, pause, interrupt, inject, or request approval against a typed
  `ActionTarget`.
- `ContextCheckpoint`: explicit repository and session sequence positions used for incremental
  context.
- `VerificationRun`: baseline, impact set, commands, artifacts, and verdict.
- `RepairRun`: failure evidence, candidate patches, evaluations, and publication status.
- `ActionRequest`: base expiring local/attached request with target, expected state version/hash,
  and nonce. Remote actions use a proof-bearing subtype that requires device and cloud signing
  proof; remote intake never accepts the unsigned base request.

Cursor domains are not interchangeable: `local_log_seq` is host-wide durable order, `repo_seq` is
repository order, `session_seq` is session order, `cloud_ingest_seq` is server ingest order, and
`client_stream_seq` is subscription delivery order. Every field/API uses its full domain name.

Managed Claude authentication is bring-your-own API/provider credential through Anthropic's
documented SDK paths. Attached Claude Code hooks remain a separate capability. LoopGuard does not
proxy `claude.ai` login, subscription rate limits, or session credentials without explicit
Anthropic approval.

Adapters project relevant `ControlEvent` payloads into existing `LoopEvent` instances. This keeps
the loop detector small and permits other subsystems to evolve independently.

## Operating modes

### Attached mode

Versioned vendor plugins are the primary reusable hook distribution; direct user/project settings
merges are a mutually exclusive, version-gated compatibility fallback. Hooks and the local daemon
observe sessions launched normally by the developer. Monitoring and change journaling are
deterministic. The integration only blocks or injects context where the native lifecycle API
supports it. If the daemon is unavailable, observation is fail-open; explicit organization
policies may separately be configured fail-closed.

### Managed mode

LoopGuard launches Codex through app-server or SDK surfaces and launches Claude through its Agent
SDK or managed-agent surface. LoopGuard owns the turn boundary, model and effort, sandbox,
permission flow, context injection, interruption, and event stream.

For Codex App Server, active-turn steering uses `turn/steer` with `expectedTurnId`; between-turn
model-visible context uses `thread/inject_items`. Model/effort availability comes from the server
catalog/capabilities rather than hard-coded names. For Claude Agent SDK, streaming input,
`canUseTool`, supported models/effort, and `Query.interrupt()` provide the corresponding managed
boundaries. Neither adapter silently substitutes one lifecycle operation for another.

### Cloud-attached mode

Repository-scoped hooks report supported cloud events to an authenticated ingest endpoint.
Platform limitations remain visible in capability metadata. LoopGuard never promises model
switching on a surface that does not expose it.

## Local daemon

`loopguardd` is the trusted local coordinator. It:

- Listens on a Unix domain socket or Windows named pipe.
- Stores events in SQLite WAL mode.
- Redacts secrets before persistence or relay.
- Runs deterministic detectors, routing features, context indexing, and verification scheduling.
- Owns local worktree leases and the browser broker.
- Maintains an outbound-only authenticated connection to hosted services.
- Continues useful local operation when the cloud is unavailable.

It uses owner-only state and socket/pipe permissions, peer user/SID verification, bounded
versioned frames, encrypted event bodies with platform-protected keys, explicit migrations, and
crash replay. Ingestion is not complete at persistence: a composition root dispatches eligible
events into a per-session existing `LoopGuard`, then bounded context/verification/relay handlers,
and returns the normalized policy decision.

The existing FastAPI demo server remains available during migration but is not extended into the
production control plane.

## Agent integrations

Codex and Claude each receive an adapter with a shared contract:

```python
class AgentAdapter(Protocol):
    capabilities: AdapterCapabilities

    async def attach(self, session: SessionRef) -> None: ...
    async def start(self, request: ManagedRunRequest) -> SessionRef: ...
    async def interrupt(self, session: SessionRef) -> None: ...
    async def inject(self, session: SessionRef, context: str) -> None: ...
    async def events(self, session: SessionRef) -> AsyncIterator[ControlEvent]: ...
```

Capabilities are negotiated per surface. Unsupported operations return a typed
`CapabilityUnavailable` result rather than silently doing nothing.

## Shared context and coordination

The Change Journal records file content hashes, patches, symbols, worktree, actor, session,
verification evidence, and monotonic repository cursor. Native hooks record known agent actions;
filesystem events catch out-of-band changes.

Every concurrent agent uses a separate Git worktree. File and symbol leases are advisory by
default and can become enforced policy for protected paths. Handoffs contain goal, decisions,
changed files, verification, unresolved work, risks, and a journal cursor. Full transcripts and
hidden reasoning are not transferred.

The context service exposes exact state through MCP and injects a bounded delta digest only when a
session is stale. Digests cite file paths, commits, and verification records so an agent can fetch
details on demand.

## Regression verification

Each implementation task receives a proof contract:

- Acceptance criteria derived from the user request.
- Existing behavior and invariants that must remain.
- Explicit non-goals.
- Impacted files, symbols, routes, schemas, and tests.
- Required completion gates.

The verifier records a baseline before changes. During implementation it runs fast impacted checks.
At completion it runs the broader affected suite. Pull requests run the repository's full required
gate. Verdicts distinguish pre-existing failures, new failures, fixed failures, skipped checks, and
inconclusive checks.

Model-based verification is optional and only evaluates evidence after deterministic commands,
screenshots, or artifacts exist.

Proof Core ships before the full Change Journal/context index. Its trusted command discovery,
pre-mutation baseline, conservative full-suite fallback, deterministic verdict, and immutable
evidence are independently useful. Context later narrows iteration checks with explicit impact
edges; a missing or stale index can never weaken the final gate or prevent an honest proof result.

## Model and effort routing

The initial router is deterministic. It evaluates task type, risk, code impact, repository
languages, required tools, context size, prior failures, latency policy, price policy, and model
availability. A versioned policy selects a model and effort at phase boundaries: plan, implement,
verify, or repair.

The router never selects a model unavailable on the current surface. It records the considered
candidates and reasons. Historical outcomes later calibrate policy thresholds; an online learning
system is not part of the first release.

## Token and cost controls

- Monitoring, file watching, exact detection, routing features, and test selection use no model.
- Context digests have hard per-turn and per-session token limits.
- Judge responses are cached by run and incident fingerprint.
- A judge or visual critic cannot exceed its assigned cost budget.
- Verification output is parsed and summarized before model-visible injection.
- Managed runs escalate models only after an observable trigger.
- Shadow and holdout runs measure net savings without fabricating counterfactual costs.

## Preference engine

Preferences are layered:

1. Non-negotiable safety and accessibility rules.
2. Repository conventions and design tokens.
3. Explicit user preferences.
4. Learned soft preferences from recorded approvals and rejections.

Deterministic rules run first. Visual evaluation only runs for UI work with a captured screenshot,
an explicit reference or policy, and remaining critic budget. A subjective violation is a warning
unless the user promotes the rule to blocking.

## Browser broker

A local broker owns warm Playwright browser processes. An explicit Playwright Test fixture
connects projects to the broker; a standalone page RPC is not counted as test-runner acceleration.
Each run gets a new isolated `BrowserContext`; pages, storage, and credentials are never shared
between sessions. The broker supports stored authentication fixtures, route-aware impacted test
selection, first-retry tracing, and CI sharding. Iteration uses impacted smoke tests; merge gates
remain comprehensive. Speed claims compare against native Playwright worker reuse.

## Auto-healing pipelines

The first target is Python Airflow/dbt-style ingestion running in staging or CI. Failure intake
supports Airflow callbacks, OpenLineage `FAIL` events, GitHub Actions, and signed webhooks.

Every repair:

1. Normalizes and fingerprints the failure.
2. Acquires a redacted or synthetic replay fixture.
3. Reproduces the failure in an isolated sandbox at an immutable repository revision.
4. Generates two or three independent candidate patches.
5. Replays the failing slice and runs schema, data-quality, regression, and security checks.
6. Selects the smallest passing candidate using deterministic ranking.
7. Opens a draft PR containing complete evidence and rollback guidance.

Workers receive no production write credentials. Network access is denied unless a signed repair
policy permits specific hosts. The first release never deploys or merges a repair automatically.

## Hosted control plane

The hosted service contains:

- Authenticated event ingest and device relay.
- PostgreSQL tenant, repository, session, event, action, and audit records.
- Object storage for encrypted logs, traces, screenshots, and repair artifacts.
- Durable workflows for repair and long-running verification.
- APNs and web notification delivery.
- Organization policy, retention, billing, and audit APIs.
- Effective capability and host/integration-health APIs with source, reason, observation time,
  TTL, and fail-closed unknown/degraded states.

The phone does not connect directly to the daemon. It signs an expiring canonical
`ActionRequest` with its registered device key and submits it to the hosted API. Web uses a
registered WebAuthn assertion. The hosted API verifies user/device/current state and countersigns
the exact request with a rotating identified cloud key. The daemon validates both signatures,
tenant/host/target, nonce, expiry, capability, and current state version/hash before acting.

Action acceptance is multi-hop: reviewed -> signed -> queued -> delivered -> host executing ->
executed/rejected, with expired/revoked and unknown outcome -> reconciling branches. API `202`
acceptance is never displayed as execution success; only daemon resolution creates a receipt.

## Control surfaces

### iOS

The production app is native SwiftUI. Its final primary navigation is Inbox, Runs, Changes,
Repairs, and Settings. Before Auto-Heal is ready, effective capabilities omit Repairs entirely;
Hosts & integrations lives under Settings and explains pairing, daemon, repository, plugin/hook
trust, coverage, and adapter health. It uses system typography, SF Symbols, Dynamic Type,
VoiceOver, reduced-motion behavior, and Liquid Glass only for the navigation/control layer.
Approval screens always show target, scope, effect, risk, expected resulting state, and expiry.
All five tabs are implemented destinations: Changes contains provenance, diff, and linked
verification; Repairs contains
reproduction evidence, candidates, checks, rollback, and draft-PR state; Settings contains
account, devices, notifications, privacy/local-only controls, and app version. Live Activities
are supplied by a separate WidgetKit extension and expose only non-sensitive phase/progress.

### Web

The web application is a dense operations surface for run timelines, diffs, verification,
policies, repairs, cost analysis, device management, and audit. It shares API schemas with iOS but
not presentation code. Desktop uses a labelled sidebar and evidence-adjacent list/detail views;
tablet uses a compact rail and inspector; mobile uses a navigation sheet and single-column detail.
Browser code uses a same-origin BFF with HttpOnly sessions and one-use, short-lived stream tickets;
access tokens never enter browser JavaScript or WebSocket URLs.

Both clients prioritize attention, proof, then safe action. They explicitly cover loading, empty,
error, success, offline/stale, cursor-resync, expired, and revoked states. The web carries forward
the Expo prototype's Space Grotesk/IBM Plex/mono and semantic token language. Native iOS keeps
system typography and platform metrics while reusing the colour/status/content principles. This
platform-specific split replaces the ambiguous instruction to apply the prototype's font and
no-glass rules unchanged to native iOS.

Navigation is generated from effective capabilities. Core clients package and dogfood before
Auto-Heal; repair routes and the final iOS Repairs tab activate only after workflow-backed repair
capability is fresh and ready. Unavailable, not entitled, insufficient role, host offline, adapter
partial, and feature-disabled states never look actionable.

The Expo application remains a disposable prototype until native iOS and web reach feature parity.

## Security and privacy

- Local-first processing and redaction before relay.
- Per-device keys stored in Keychain or platform equivalent.
- Short-lived access tokens and capability-bound action tokens.
- Tenant isolation in application code and database policy.
- Encryption in transit and at rest.
- Secret, credential, and likely-PII detection before persistence.
- Configurable local-only mode and artifact retention.
- Signed webhook verification and replay protection.
- Immutable audit records for every external action.
- No production credentials in repair sandboxes.
- Dependency, container, and provenance scanning in release pipelines.

## Reliability

- Event ingestion is idempotent by event ID.
- Every stream has cursor-based replay.
- Local writes complete before relay acknowledgement.
- Cloud loss never disables local guarding or journaling.
- Duplicate phone actions are harmless.
- Stale action requests fail closed with a human-readable reason.
- Background workflows are resumable and bounded by time, cost, and attempts.

## Success measures

- Newly introduced regression escape rate.
- Percentage of sessions with verified completion.
- Loops detected before another paid turn.
- Median and tail total-cost change versus holdout.
- Context digest tokens versus full-context equivalent.
- Agent collision warnings that prevent overlapping edits.
- Impacted-test latency and full-gate latency.
- Repair reproduction rate, candidate pass rate, and PR acceptance rate.
- Approval response latency and invalid-action rejection rate.

## Delivery decomposition

The implementation is split into independent plans:

1. Control-plane foundation.
2. Codex and Claude integrations.
3. Shared context and multi-agent coordination.
4. Regression verification.
5. Model routing and cost control.
6. Preference engine.
7. Browser and Playwright acceleration.
8. Hosted cloud control plane.
9. Native iOS and web core control surfaces.
10. Auto-healing pipelines and the repair-surface activation tranche.
11. Production security, operations, and release hardening.

Each subsystem must ship behind capability flags and produce independently testable value.

The dependency-safe execution order intentionally splits two plans into tranches: Foundation;
Integrations 1–4; Verification Proof Core; Context; Integrations 5–8; Routing; Preferences;
Browser; Cloud; Surfaces 1–11; Auto-Heal; Surface 12; Hardening. This ships trustworthy proof
before optional context narrowing and packageable core clients before repair UI activation.
