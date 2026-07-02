# LoopGuard Productization Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver LoopGuard as a production local-first reliability control plane for Codex and Claude with verified completion, coordinated context, cost-aware managed execution, remote approvals, accelerated browser testing, and safe pipeline repair.

**Architecture:** Preserve the current loop detector as an inner library and build independently deployable control-plane subsystems around a versioned event contract. Ship local value first, then managed execution, hosted control surfaces, repair workflows, and enterprise hardening.

**Tech Stack:** Python 3.11+, Pydantic 2, asyncio, SQLite, FastAPI, PostgreSQL, Temporal, Docker, OpenTelemetry, SwiftUI/iOS 26, Next.js/TypeScript, Playwright

---

## Relationship to earlier plans

This program supersedes the product-direction and production-architecture portions of:

- `2026-06-27-loopguard-v2-real-product.md`
- `2026-06-27-loopguard-cloud-app.md`
- `2026-06-30-loopguard-phone-ui-redesign.md`

Those documents remain historical evidence for the current demo and prototype UI. Do not execute
them in parallel with this program where file paths or architecture differ.

## Plan index

1. [Control-plane foundation](2026-07-02-control-plane-foundation.md)
2. [Codex and Claude integrations](2026-07-02-agent-integrations.md)
3. [Shared context and multi-agent coordination](2026-07-02-context-coordination.md)
4. [Regression verification](2026-07-02-regression-verification.md)
5. [Model routing and cost control](2026-07-02-model-routing-cost-control.md)
6. [Preference engine](2026-07-02-preference-engine.md)
7. [Browser and Playwright acceleration](2026-07-02-browser-acceleration.md)
8. [Hosted cloud control plane](2026-07-02-cloud-control-plane.md)
9. [Auto-healing pipelines](2026-07-02-auto-heal-pipelines.md)
10. [Native iOS and web control surfaces](2026-07-02-control-surfaces.md)
11. [Production security and operations](2026-07-02-production-hardening.md)

## Requirement coverage

| Requested capability | Owning plan | Production acceptance |
|---|---|---|
| Automatically runs in Codex and Claude | Agent integrations | Idempotent global/project hook install; daemon-down observation remains fail-open |
| Local and cloud support | Agent integrations, cloud control plane | Capability matrix distinguishes attached, managed, and cloud-attached behavior |
| Phone control | Cloud control plane, control surfaces | Outbound-only relay; signed expiring actions execute at most once |
| Best model and thinking level | Model routing and cost control | Deterministic phase routing; unsupported cloud controls are never advertised |
| Lower total token/cost use | Foundation, routing, verification | Zero-token monitoring, hard overhead budgets, observed-cost holdout report |
| Auto-healing pipeline | Auto-healing pipelines | Reproduction and deterministic evaluation precede draft-PR publication |
| iOS 26 and web quality | Control surfaces, preferences | Native SwiftUI plus web console pass visual and accessibility gates |
| Prevent fix/reintroduced bugs | Regression verification | Baseline separates existing failures from newly introduced failures |
| Switch between agents | Context coordination | Structured handoff transfers decisions, changes, tests, risks, and cursor |
| Human-like design preferences | Preference engine | Editable layered rules; learned preferences stay soft until promoted |
| Immediate awareness of other agents | Context coordination | Hook plus filesystem reconciliation updates the Change Journal and collision leases |
| Faster Playwright/browser testing | Browser acceleration | Warm process, isolated contexts, impacted iteration, full PR gate |
| Full production readiness | Production hardening | Isolation, recovery, SLO, retention, release, and incident gates pass |

## Critical path and parallel lanes

```text
Foundation
   ├── Agent integrations ── Context ── Verification ── Managed routing
   │                                             └────── Auto-heal
   └── Cloud control plane ── Auto-heal ── Control surfaces

Verification ── Browser acceleration
Verification ── Preference engine

All lanes ── Production hardening
```

After Foundation, two teams can work without sharing implementation files:

- Runtime lane: integrations, context, verification, routing, browser, preferences.
- Hosted product lane: cloud API, iOS, and web.

Auto-heal starts only after verification contracts and hosted durable workflows are stable.
Control-surface contract generation starts after Auto-heal registers repair APIs, so its OpenAPI
checks never depend on endpoints from a later plan.
Production hardening begins during Foundation but remains the final release gate.

## Target repository shape

```text
apps/
  ios/                         Native SwiftUI application
  web/                         Next.js operations console
cloud-app/                     Existing Expo prototype; frozen after parity work starts
docs/superpowers/
  specs/                       Approved product and subsystem designs
  plans/                       Executable implementation plans
loopguard/
  src/loopguard/
    adapters/                  Codex, Claude, filesystem, CI, pipeline adapters
    browser/                   Browser broker and impacted-test selection
    context/                   Change journal, leases, digests, handoffs, MCP
    control/                   Canonical events, daemon, local store, action protocol
    detectors/                 Existing deterministic loop detectors
    heal/                      Failure intake, sandboxing, candidates, evaluation, PR
    preferences/               Policy layers and visual evaluation
    router/                    Model catalog, features, deterministic routing, outcomes
    verify/                    Proof contracts, baselines, impact, runners, evidence
services/
  control-api/                 Multi-tenant hosted API and worker package
    alembic/                    PostgreSQL/Alembic migrations
```

## Phase 0: Contract and migration guardrails

**Duration:** 2 weeks
**Exit criterion:** Existing demos and tests pass while the new versioned event contract is usable
from a local process.

- [ ] Run the control-plane foundation plan through its event, store, and daemon health tasks.
- [ ] Add compatibility projection from `ControlEvent` to the existing `LoopEvent`.
- [ ] Freeze the current FastAPI/Expo protocol as `demo-v1`; do not extend it with production data.
- [ ] Record baseline test, typecheck, and package-build commands in the repository root.
- [ ] Tag a migration checkpoint before native adapters land.

**Release gate:**

```bash
cd loopguard
python -m pytest -q
ruff check src tests
python -m build
```

Expected: all tests pass, Ruff exits 0, and both wheel and source distribution are created.

## Phase 1: Local attach and context

**Duration:** 6 weeks
**Dependencies:** Phase 0
**Exit criterion:** A developer installs LoopGuard once, starts Codex or Claude normally, and sees
local sessions, changes, loop decisions, handoffs, and collision warnings without cloud service.

- [ ] Complete attached-mode tasks in the agent integrations plan.
- [ ] Complete the Change Journal, digest, handoff, and advisory lease tasks.
- [ ] Add the local MCP server and hook installer.
- [ ] Exercise concurrent Codex and Claude sessions in separate Git worktrees.
- [ ] Run a five-repository dogfood cohort with local-only mode enabled.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/control tests/adapters tests/context
loopguard doctor --json
loopguard integrations verify --agent codex
loopguard integrations verify --agent claude
```

Expected: tests pass, doctor reports a healthy daemon and writable store, and both integrations
report installed hooks with no unsupported mandatory capabilities.

## Phase 2: Proof of Change

**Duration:** 6 weeks
**Dependencies:** Phase 1
**Exit criterion:** LoopGuard distinguishes pre-existing failures from new regressions and refuses
to mark a managed task verified without required evidence.

- [ ] Complete proof-contract, baseline, impact, command-runner, and verdict tasks.
- [ ] Add language adapters for Python and TypeScript test discovery.
- [ ] Add route/schema/UI impact plugins behind capability flags.
- [ ] Integrate verifier results with Stop hooks and managed turn completion.
- [ ] Collect false-positive and missed-regression cases from dogfood repositories.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/verify tests/integration/test_verified_completion.py
```

Expected: tests pass, including fixtures for pre-existing failures, introduced failures, skipped
checks, timeouts, and successful verification.

## Phase 3: Managed execution and cost routing

**Duration:** 6 weeks
**Dependencies:** Phases 1 and 2
**Exit criterion:** LoopGuard can start, steer, interrupt, and verify managed Codex and Claude runs,
while selecting model and effort at phase boundaries within a configured budget.

- [ ] Complete Codex app-server and Claude SDK managed adapters.
- [ ] Complete deterministic feature extraction, catalog, policy, and outcome recording.
- [ ] Add plan/implement/verify/repair phase transitions.
- [ ] Add explicit capability errors for unsupported native cloud model controls.
- [ ] Run shadow routing before enabling automatic model selection.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/adapters/test_codex_managed.py \
  tests/adapters/test_claude_managed.py tests/router
```

Expected: tests pass with fake protocol servers and no live provider credentials.

## Phase 4: Browser and preference intelligence

**Duration:** 5 weeks
**Dependencies:** Phase 2
**Exit criterion:** UI work receives bounded preference evaluation and impacted Playwright feedback
without sharing browser state between agents.

- [ ] Complete deterministic preference layers and decision recording.
- [ ] Complete browser lifecycle, isolated contexts, impacted selection, and trace policies.
- [ ] Add UI proof evidence to verification records.
- [ ] Dogfood on the existing `cloud-app` prototype and representative fixture applications; repeat
  the same checks on production web/iOS surfaces in Phase 7.
- [ ] Measure warm-start, impacted-suite, and full-suite latency separately.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/preferences tests/browser
cd integrations/browser-broker
npm test
npx tsc --noEmit
```

Expected: all tests pass and the broker isolation test proves storage does not cross sessions.

## Phase 5: Hosted control plane and remote approvals

**Duration:** 8 weeks
**Dependencies:** Phases 1–3
**Exit criterion:** Authenticated users can pair a daemon, receive replayable session updates, and
submit expiring approval actions through an API test client without exposing the daemon. Native
and web product clients follow in Phase 7.

- [ ] Complete hosted tenancy, ingest, relay, action, artifact, notification, audit, and control-query tasks.
- [ ] Add offline, duplicate-action, stale-action, and reconnect testing.
- [ ] Run an external security review before public beta.

**Release gate:**

```bash
cd services/control-api
python -m pytest -q
```

Expected: backend tests pass; preference, cost, device, and audit APIs are registered; and a
duplicate or expired approval never executes twice.

## Phase 6: Auto-healing pipeline beta

**Duration:** 8 weeks
**Dependencies:** Phases 2, 3, and 5
**Exit criterion:** A supported Airflow/OpenLineage failure is reproduced, two or three candidates
are evaluated in isolation, and the winning verified patch is published as a draft GitHub PR.

- [ ] Complete failure intake and fingerprinting.
- [ ] Complete redacted fixture capture and reproducibility gate.
- [ ] Complete sandbox, candidate, evaluator, and deterministic ranking tasks.
- [ ] Complete GitHub App publication and PR evidence rendering.
- [ ] Run in observe-only mode on real staging incidents before enabling repair generation.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/heal tests/integration/test_airflow_repair.py
```

Expected: tests pass and the end-to-end fixture opens no real PR unless the fake GitHub publisher is
explicitly replaced in a controlled staging environment.

## Phase 7: Native iOS and web control surfaces

**Duration:** 8 weeks
**Dependencies:** Phases 4–6
**Exit criterion:** Native iOS and web clients consume the complete OpenAPI contract, replay live
sessions without gaps, and submit safe actions for loops, verification, and repairs.

- [ ] Complete the native iOS and web control-surface plan.
- [ ] Complete APNs and web notification delivery.
- [ ] Verify repair, preference, cost, device, and audit screens against real service fixtures.
- [ ] Add offline, duplicate-action, stale-action, and reconnect testing.
- [ ] Run visual and accessibility gates before private beta.

**Release gate:**

```bash
cd apps/web
npm test
npx tsc --noEmit
npx playwright test
```

Run the iOS unit and UI test schemes through XcodeBuildMCP. Expected: web and iOS checks pass; a
duplicate or expired approval never executes twice; and repair UI is backed by implemented APIs.

## Phase 8: Production hardening

**Duration:** 8 weeks, then ongoing
**Dependencies:** All earlier phases
**Exit criterion:** Published SLOs, tenant isolation, retention/deletion, billing metering, release
provenance, disaster recovery, and incident response have passed production readiness review.

- [ ] Complete the production hardening plan.
- [ ] Run load, chaos, backup/restore, and regional failover exercises.
- [ ] Complete privacy, threat-model, dependency, and container reviews.
- [ ] Establish support escalation and incident ownership.
- [ ] Enable paid plans only after usage metering reconciliation passes.

## Rollout strategy

1. Maintainers only: local attach, no cloud relay.
2. Design partners: local attach plus verification.
3. Private beta: managed runs and hosted approvals.
4. Repair beta: selected Airflow/dbt repositories, draft PR only.
5. Public beta: individual accounts and small teams.
6. General availability: tenant governance, SSO, billing, retention, and operational SLOs.

Every phase uses capability flags. A customer sees only capabilities that have completed their
release gate; the UI must not display disabled actions as if they are available.

## Program-level acceptance criteria

- Monitoring alone invokes no model.
- A daemon restart loses no acknowledged local event.
- The cloud never needs an inbound connection to a developer machine.
- Context injection is cursor-based and bounded.
- Concurrent agents receive collision warnings before overlapping work is merged.
- Verification identifies whether a failure is old or newly introduced.
- Model routing records a deterministic explanation and respects surface capability.
- Browser sessions cannot read another session's storage.
- Subjective preferences do not block unless explicitly configured as hard policy.
- Repair cannot publish without reproduction and required evidence.
- No first-release repair can merge or deploy automatically.
- Every phone action is authenticated, authorized, expiring, replay-safe, and audited.

## Final verification

Run all repository and service suites, then produce a signed release candidate:

```bash
cd loopguard
python -m pytest -q
ruff check src tests
python -m build
cd ../apps/web
npm ci
npm test
npx tsc --noEmit
npx playwright test
cd ../../services/control-api
python -m pytest -q
```

Expected: all commands exit 0. iOS unit/UI tests, container scanning, migration rehearsal,
backup/restore, and the production readiness checklist must also pass before release promotion.
