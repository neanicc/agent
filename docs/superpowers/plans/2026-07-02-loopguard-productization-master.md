<!-- /autoplan restore point: /private/tmp/loopguard-autoplan-restore-20260714-092612 -->

# LoopGuard Productization Master Implementation Plan

> **For implementation agents:** Execute this plan task-by-task and track every checkbox. In Codex, use the native plan, debugging, review, and verification tools available in the host. In Claude Code, use `superpowers:subagent-driven-development` or `superpowers:executing-plans` when installed. A missing named workflow is never a blocker; perform the equivalent TDD and verification steps directly.

> **Command convention:** Resolve one absolute, supported virtualenv interpreter as `$PY`. In every
> shell snippet, read bare `python` as `$PY`, `python -m pip` as `$PY -m pip`, and `ruff` as
> `$PY -m ruff`; never assume those executables are on `PATH`.

**Goal:** Deliver LoopGuard as a production local-first reliability control plane for Codex and Claude with verified completion, coordinated context, cost-aware managed execution, remote approvals, accelerated browser testing, and safe pipeline repair.

**Architecture:** Preserve the current loop detector as an inner library and build independently deployable control-plane subsystems around a versioned event contract. Ship local value first, then managed execution, hosted control surfaces, repair workflows, and enterprise hardening.

**Tech Stack:** Python 3.11+, Pydantic 2, asyncio, SQLite, FastAPI, PostgreSQL, Temporal, Docker, OpenTelemetry, SwiftUI/iOS 26, Next.js/TypeScript, Playwright

---

## Current repository snapshot (verified 2026-07-14)

- `main` and `origin/main` both pointed to `e898e987c63a1262052b920c2d3f1ec848237659`
  during this review. The implementation prompt requires a fresh remote/branch check before work.
- `loopguard/` is still the hackathon-era Python product: the reusable deterministic
  `LoopEvent -> LoopGuard -> LoopDecision` circuit breaker, a small FastAPI/demo surface, fake/live
  provider examples, and 71 passing tests. The current run also reports a third-party
  Starlette/`httpx` deprecation and a LoopGuard-owned unawaited `_broadcast` coroutine warning;
  Foundation Task 6 closes the owned warning. None of the July 2 control-plane subsystems is
  implemented yet.
- `cloud-app/` is still the Expo prototype: 6 tests pass and TypeScript checks cleanly. It is not
  the planned Next.js web console or native SwiftUI client.
- The current Python baseline must use `loopguard/.venv/bin/python`; bare `python` and `ruff` are
  not installed on `PATH` in this checkout. Tests pass, while Ruff has two pre-existing findings:
  unused local `result` in `tests/test_agent.py:37` and unused import `JudgeVerdict` in
  `tests/test_judge.py:1`. Record these before implementation and do not attribute them to a task.
- Planned `apps/web`, `apps/ios`, `services/control-api`, browser broker, managed adapters,
  verification/context/routing/preference/repair packages, and production infrastructure do not
  exist. This roadmap is an implementation program, not a description of already shipped code.
- `.DS_Store` is repository-ignored. Preserve existing user metadata and never let it stop or
  contaminate implementation commits.

## Relationship to earlier plans

This program supersedes the product-direction and production-architecture portions of:

- `2026-06-27-loopguard-v2-real-product.md`
- `2026-06-27-loopguard-cloud-app.md`
- `2026-06-30-loopguard-phone-ui-redesign.md`

Those documents remain historical evidence for the current demo and prototype UI. Do not execute
them in parallel with this program where file paths or architecture differ.

## Plan index

There are 12 plan documents in this program: this master coordinator plus the 11 executable
subsystem plans below. The master is not a twelfth implementation lane.

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
| Phone control | Cloud control plane, control surfaces | Outbound-only relay; device-signed and cloud-countersigned expiring actions execute at most once |
| Best model and thinking level | Model routing and cost control | Deterministic phase routing; unsupported cloud controls are never advertised |
| Lower total token/cost use | Foundation, routing, verification | Zero-token monitoring, hard overhead budgets, observed-cost holdout report |
| Auto-healing pipeline | Auto-healing pipelines | Reproduction and deterministic evaluation precede draft-PR publication |
| iOS 26 and web quality | Control surfaces, preferences | Native SwiftUI plus web console pass visual and accessibility gates |
| Prevent fix/reintroduced bugs | Regression verification | Baseline separates existing failures from newly introduced failures |
| Switch between agents | Context coordination | Structured handoff transfers decisions, changes, tests, risks, and cursor |
| Human-like design preferences | Preference engine | Editable layered rules; learned preferences stay soft until promoted |
| Immediate awareness of other agents | Context coordination | Hook plus filesystem reconciliation updates the Change Journal and collision leases |
| Faster Playwright/browser testing | Browser acceleration | Real Playwright fixture, warm process, isolated contexts, native-baseline benchmark, full PR gate |
| Full production readiness | Production hardening | Isolation, recovery, SLO, retention, release, and incident gates pass |

## Critical path and parallel lanes

```text
Foundation
   ├── Attached integrations ── Verification Proof Core ── Context
   │                                      └─────────────── Managed adapters/routing
   └── Cloud control plane ── Core control surfaces (Tasks 1-11)
                                      └── Auto-heal ── Repair surfaces (Task 12)

Verification ── Browser acceleration
Verification ── Preference engine

All lanes ── Production hardening
```

After Foundation, two lanes can work without sharing implementation files, subject to the explicit
milestone dependencies above:

- Runtime lane: attached integrations, proof, context, managed adapters, routing, browser, preferences.
- Hosted product lane: cloud API, core iOS/web, repair workflow, repair client activation.

Auto-heal starts only after verification contracts and hosted durable workflows are stable.
The cloud plan publishes a disabled repair schema and effective capability; core clients package
without a dead repair route. Auto-Heal activates the workflow-backed capability, then Surface Task
12 adds the repair destinations and reruns final parity gates.
Security controls required by Foundation are implemented in their owning early tasks; the
production-hardening plan remains the final audit and release gate.

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

- [ ] Complete all six control-plane foundation tasks, including Task 6 baseline warning/lint debt,
  daemon health, and zero-key quickstart.
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

## Phase 1: Local attach and Verification Proof Core

**Duration:** 4–6 weeks
**Dependencies:** Phase 0
**Exit criterion:** A developer installs LoopGuard once, starts Codex or Claude normally, and sees
local sessions and loop decisions, then receives an honest baseline-aware verified/inconclusive
result without cloud service or the full context index.

- [ ] Complete Agent Integration Tasks 1–4 (attached plugin/fallback tranche).
- [ ] Complete all seven Regression Verification tasks, including the conservative no-index fallback.
- [ ] Prove plugin and fallback modes are mutually exclusive and vendor trust remains human-owned.
- [ ] Run prompt -> pre-mutation baseline -> change -> deterministic verdict -> signed evidence E2E.
- [ ] Run a five-repository dogfood cohort with local-only mode enabled.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/control tests/adapters/test_hook_entry.py tests/verify \
  tests/integration/test_verified_completion.py
```

Expected: tests pass; missing/stale impact indexes fall back safely; a late attach is inconclusive;
and a trusted baseline plus unchanged required checks produces immutable proof.

## Phase 2: Context coordination

**Duration:** 4–6 weeks
**Dependencies:** Phase 1
**Exit criterion:** Changes from agents, editors, scripts, and Git are journaled; concurrent work is
isolated/coordinated; and context-aware impact improves iteration without changing Proof Core truth.

- [ ] Complete all seven Context Coordination tasks.
- [ ] Exercise concurrent Codex and Claude sessions in separate Git worktrees.
- [ ] Prove journal-backed impact narrows iteration checks but final gates remain conservative.
- [ ] Collect collision, false-positive, and missed-regression cases from dogfood repositories.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/context tests/verify tests/control tests/adapters/test_hook_entry.py
```

Expected: tests pass, concurrent managed leases never share a writable worktree, and context E2E
does not weaken proof verdicts.

## Phase 3: Managed execution and cost routing

**Duration:** 6 weeks
**Dependencies:** Phases 1 and 2
**Exit criterion:** LoopGuard can start, steer, interrupt, and verify managed Codex and Claude runs,
while selecting model and effort at phase boundaries within a configured budget.

- [ ] Complete Agent Integration Tasks 5–8 for managed adapters, compatibility/setup/doctor, and
  user-service installation.
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
  the same checks on production core web/iOS surfaces in Phase 6 and repairs in Phase 7.
- [ ] Measure warm-start, impacted-suite, and full-suite latency separately.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/preferences tests/browser
cd integrations/browser-broker
npm ci
npm test
npx tsc --noEmit
cd ../playwright-loopguard
npm ci
npm test
npx tsc --noEmit
```

Expected: all tests pass, the broker isolation test proves storage does not cross sessions, and a
real Playwright project demonstrates any warm-invocation benefit against native worker reuse.

## Phase 5: Hosted control plane and remote approvals

**Duration:** 8 weeks
**Dependencies:** Phases 1–3
**Exit criterion:** Authenticated users can pair a daemon, receive replayable session updates, and
submit expiring approval actions through an API test client without exposing the daemon. Native
and web core product clients follow in Phase 6.

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

## Phase 6: Native iOS and web core control surfaces

**Duration:** 6–8 weeks
**Dependencies:** Phases 4 and 5
**Exit criterion:** Native iOS and web clients package independently, replay sessions without gaps,
show host/integration coverage, and submit safe loop/verification actions. Repair navigation is
absent while its effective capability is disabled.

- [ ] Complete Control Surface Tasks 1–11 only.
- [ ] Complete APNs and web notification delivery.
- [ ] Verify host/integration, preference, cost, device, and audit screens against service fixtures.
- [ ] Add capability-hidden navigation, offline, duplicate-action, stale-action, and reconciliation tests.
- [ ] Run visual, accessibility, and reproducible core-package gates before private beta dogfood.

**Release gate:**

```bash
cd apps/web
npm test
npx tsc --noEmit
npx playwright test
npm run build
```

Run iOS unit/UI tests through XcodeBuildMCP when available, otherwise equivalent
`xcodebuild`/`simctl` commands. Expected: core clients pass with no dead repair destination; API
acceptance is not displayed as execution success; and unsigned/package verification succeeds.

## Phase 7: Auto-healing beta and repair-surface activation

**Duration:** 8 weeks
**Dependencies:** Phases 2, 3, 5, and 6
**Exit criterion:** A supported Airflow/OpenLineage failure is reproduced, bounded candidates are
evaluated in isolation, a verified winner can be published as a draft PR, and the workflow-backed
repair capability activates complete web/iOS destinations.

- [ ] Complete all nine Auto-Heal tasks.
- [ ] Run in observe-only mode on authorized staging incidents before enabling repair generation.
- [ ] Complete Control Surface Task 12 and rerun final client parity/visual/accessibility gates.
- [ ] Prove capability revocation hides/locks repair actions without losing audit/evidence.

**Release gate:**

```bash
cd loopguard
python -m pytest -q tests/heal tests/integration/test_airflow_repair.py
cd ../apps/web
npm test
npx tsc --noEmit
npx playwright test
```

Expected: fake providers/GitHub are used by default; no repair can merge/deploy; and every visible
repair route is backed by a fresh ready capability and durable workflow API.

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

1. Maintainers only: attached plugins/fallbacks plus Proof Core, no cloud relay.
2. Design partners: context-aware proof and managed runs.
3. Private beta: hosted approvals and independently packaged core clients.
4. Repair beta: selected Airflow/dbt repositories, draft PR only, then repair client activation.
5. Public beta: individual accounts and small teams after product go/no-go thresholds pass.
6. General availability: tenant governance, SSO, billing, retention, and operational SLOs.

Every phase uses capability flags. A customer sees only capabilities that have completed their
release gate; the UI must not display disabled actions as if they are available.

### Product go/no-go evidence

Engineering completion does not authorize broader rollout. Before each beta expansion, record the
measurement query/script, environment, cohort, sample size, numerator/denominator, threshold, and
owner decision in the milestone evidence:

| Signal | Private-beta threshold | Failure action |
|---|---:|---|
| Fresh install -> protected session | median <=5 min, p90 <=10 min, >=20 clean installs, >=80% complete without maintainer intervention | Fix onboarding; do not add new platform scope |
| Weekly protected-session retention | >=40% of activated design partners in each of four weeks, cohort >=20 | Interview churned users and pivot workflow before public beta |
| Eligible tasks ending with accepted proof | >=70% over >=100 tasks | Diagnose trust, command, and inconclusive reasons; block expansion |
| Inconclusive proof rate | <=15% of eligible tasks; every reason classified | Fix baseline/integration coverage before claiming verified completion |
| False pause/block rate | <=2% of protected tasks and zero confirmed destructive false blocks | Return enforcement to observe-only and correct policy |
| Confirmed prevented waste | >=5 confirmed loops/regressions/collisions per 100 eligible tasks, with user acknowledgement | Revisit wedge if benefit is not observed |
| Proof engagement | >=50% of active users open/share a proof or act from its receipt weekly | Simplify proof presentation; do not assume evidence is valued |
| LoopGuard overhead | p95 hook path <=100 ms and observed model/token overhead below observed prevented paid work in the holdout window | Disable costly optional features or keep them in shadow |

Continue to public beta only when activation, retention, proof acceptance, false-enforcement, and
overhead gates all pass. If activation passes but retention/proof engagement misses, pivot the
workflow and rerun one cohort. If two consecutive cohorts remain below 20% retention or 20% proof
engagement, confirmed prevented waste stays below two per 100 tasks, or false enforcement exceeds
5%, stop platform expansion and run a founder/product decision gate. Estimated savings never
satisfy an observed-value threshold.

Before locking public registry names, domains, Apple bundle/team identifiers, marketplace entries,
or App Store metadata, the product owner must confirm whether LoopGuard is the final name, approve
one sentence of positioning centered on baseline-aware verified completion, and supply legal and
account ownership. Implementation may use clearly provisional local identifiers; it may not invent
or publish final identity on the owner's behalf.

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
- Every browser action carries registered WebAuthn proof; every iOS action carries registered
  device-key proof; the cloud countersigns the exact canonical request.
- “Verified” requires task intake and baseline before the first mutation; late-attached passing
  checks remain inconclusive.
- Local, repository, session, cloud-ingest, and client-stream positions use distinct cursor names.

## Final verification

Run all repository and service suites, then produce a signed release candidate:

```bash
cd loopguard
python -m pip install -e ".[all-dev]"
python -m pytest -q
ruff check src tests
python -m build
cd integrations/claude-bridge
npm ci
npm test
npx tsc --noEmit
cd ../browser-broker
npm ci
npm test
npx tsc --noEmit
cd ../playwright-loopguard
npm ci
npm test
npx tsc --noEmit
cd ../../../apps/web
npm ci
npm test
npx tsc --noEmit
npx playwright test
cd ../../services/control-api
python -m pytest -q
```

Expected: all commands exit 0. iOS unit/UI tests, container scanning, migration rehearsal,
backup/restore, and the production readiness checklist must also pass before release promotion.

---

## GSTACK AUTOPLAN REVIEW — PHASE 1: CEO, SCOPE, AND STRATEGY

**Review date:** 2026-07-02
**Review mode:** SELECTIVE EXPANSION
**Review corpus:** This master roadmap, all eleven executable subsystem plans, the approved
reliability-control-plane design, the Codex / Claude Code implementation prompt, current repository
instructions, and the existing LoopGuard engine/server/prototype code.
**UI scope:** Yes.
**Developer-facing scope:** Yes.
**Independent voices:** Codex completed a read-only review. The Claude Code independent review is
unavailable because the local CLI has no current authentication, so no cross-model consensus is
claimed in this phase.

### 0A. Premise challenge

| Premise | Assessment | Consequence for the plan |
|---|---|---|
| Developers need another phone remote | Weak as a primary wedge; native Codex/Claude and multiple third parties already provide remote control | Phone/web remain supporting approval surfaces, not the product thesis |
| Deterministic loop detection alone is the product | Insufficient; loop detection and kill switches already exist elsewhere | Existing detection becomes one signal inside a broader proof system |
| Cross-agent verified completion is valuable | Strongest premise | Lead with proof that an agent satisfied the task without introducing regressions |
| Model routing guarantees savings | Unproven | Treat cost reduction as a measured secondary benefit, never a launch promise |
| Auto-Heal belongs inside the core loop detector | False | Keep repair as a separately gated subsystem that consumes proof/evidence contracts |
| Every attached session can be fully controlled | False | Capability metadata must distinguish observation, hook-time intervention, and managed control |
| One year-long branch and one draft PR are safe delivery mechanics | False | Preserve the full roadmap but deliver it through reviewable vertical milestones |

The actual user outcome is not “more agent infrastructure.” It is: **an agent change ends with
trustworthy evidence of what changed, whether the requested behavior works, and whether existing
behavior regressed.** Loop prevention, shared context, model routing, browser acceleration, remote
approval, and repair all support that result.

If nothing is built, developers can continue using vendor-native agents and phone remotes, but
they still lack a provider-neutral proof record, baseline-versus-regression attribution, and
coordinated evidence across agents. That is the pain this roadmap must validate first.

### 0B. What already exists

| Sub-problem | Existing repository primitive | Reuse decision |
|---|---|---|
| Deterministic loop detection | `LoopEvent`, `LoopGuard`, exact/semantic/ping-pong/budget detectors | Preserve as the inner circuit breaker |
| Optional loop judgment | `LLMJudge` and provider abstraction | Reuse after incident-keyed caching and budget reservation are fixed |
| Cost accounting | Provider `LLMResult`, `LoopEvent.cost_usd`, `LoopGuard.summary()` | Migrate unknown price from zero to unknown; keep provider-reported and estimated cost separate |
| Agent tool loop | `run_agent()` and `run_multi_agent()` | Keep for demos and fixtures, not as the production managed-runtime abstraction |
| Intervention semantics | `LoopDecision` plus pause/auto/flag handling | Project into versioned `PolicyDecision` and capability-scoped actions |
| Local persistence helpers | JSONL export/read helpers | Retain for import/export; do not use as the production event store |
| Demo server | FastAPI run registry and WebSocket streaming | Freeze as `demo-v1`; do not evolve into the hosted service |
| Prototype mobile UI | Expo mission-control application | Use for product learning only; deprecate after native/web parity |

The current core is small and useful, but three behaviors require explicit migration instead of
blind preservation:

1. Embedded demo agents execute a tool before the guard observes it.
2. Judge caching is keyed by `(run_id, detector)`, not an incident fingerprint.
3. Unknown model pricing is reported as `$0.00`, which is not honest production accounting.

### 0C. Dream state

```text
CURRENT
Demo-owned agents + deterministic loop guard + optional judge
        |
        v
FIRST VALUABLE PRODUCT
Install once -> attach -> capture task baseline -> journal changes
-> run trusted checks -> issue signed proof or explicit inconclusive result
        |
        v
12-MONTH PLATFORM
Provider-neutral proof and coordination layer
-> managed model/effort routing
-> safe remote approvals
-> evidence-backed repair workflows
-> enterprise policy, audit, retention, and recovery
```

The roadmap points toward the dream state, but the existing execution prompt turns the program
into a big-bang integration branch. That delays proof of demand and puts security after the data
model it is supposed to constrain.

### 0C-bis. Implementation alternatives

#### Approach A: One integration branch, all plans sequentially

- **Effort:** XL
- **Risk:** High
- **Completeness:** 8/10
- **Pros:** Preserves the current prompt; one place tracks the entire program.
- **Cons:** Long-lived branch, unreviewable PR, delayed user feedback, late security and migration
  discovery, and high recovery cost after context loss.
- **Reuses:** All current plans unchanged.

#### Approach B: Proof-first vertical milestones, full roadmap retained

- **Effort:** XL total, delivered as S/M milestones
- **Risk:** Medium
- **Completeness:** 10/10
- **Pros:** Validates the wedge early; security and contracts land before dependent data; every
  milestone has a shippable exit gate; all eleven plans remain in the program.
- **Cons:** Requires changing the implementation prompt, task counts, and branch/PR strategy.
- **Reuses:** Existing plans, reorganized into dependency-safe vertical slices.

#### Approach C: Auto-Heal-first enterprise product

- **Effort:** L for a narrow Airflow/dbt beta, XL for the full platform
- **Risk:** High
- **Completeness:** 6/10 for the original cross-agent product
- **Pros:** Concrete enterprise pain and a visible draft-PR outcome.
- **Cons:** Different buyer, higher data/privacy risk, and it does not validate the core
  cross-agent proof product.
- **Reuses:** Verification, sandbox, artifact, and action contracts.

**Recommendation:** Approach B. It keeps every requested subsystem while making the first release
useful and reviewable.

**Premise-gate result (2026-07-02):** The user chose the delivery mechanics in Approach A: retain
the complete roadmap, implement it on `feat/loopguard-production`, and keep one draft PR open for
the program. This overrides the reviewer's branch/PR recommendation without weakening the
proof-first sequencing. The branch must use milestone commits, task-level commits, cumulative plan
gates, and explicit review checkpoints so that the single PR stays recoverable and reviewable.

### 0D. Selective expansion and scope decision

No new top-level product is added. The roadmap already contains more surface area than one launch
can validate. The useful expansion is structural: make proof/evidence contracts the common product
spine and require each later subsystem to consume them.

The following are completeness fixes inside the existing blast radius, not new product scope:

- Add a daemon event dispatcher so persisted events actually reach loop detection, context,
  verification, relay, and registered subsystem handlers.
- Define separate local-log, repository, session, cloud-ingest, and client-stream cursors.
- Make remote actions target typed resources (`session`, `repair`, `host`, `verification`, or
  `repository`) and require capability checks.
- Move local-state permissions, socket identity, frame limits, encryption, and repository trust
  ahead of persistence and command execution.
- Add the client-required query APIs, repair persistence, web BFF/stream-ticket flow, and
  device-signed action proof.
- Add signed webhook intake and hostile-input isolation for Auto-Heal.
- Add the missing Playwright runner fixture/adapter before claiming broker speedups.

### 0E. Temporal interrogation

| Implementation point | Decision that must be fixed in the plans first |
|---|---|
| Foundation | Canonical contract, cursor domains, action target, socket transport, peer identity, encrypted storage |
| Local attach | Exact hook schemas, trust state, fail-open/closed policy, daemon evaluation response |
| Context | Observation identity versus content fingerprint, cursor-bearing stored record, attached-worktree warning |
| Verification | Who creates the proof contract, when baseline freezes, repository command trust/sandbox |
| Managed mode | Provider capability truth, phase ownership, restart/resume semantics |
| Cloud | Repository/host binding, BFF authentication, stream ticket, signing-key rotation, endpoint ownership |
| Auto-Heal | Hostile input boundary, safe geolocation/PII fixtures, isolated candidate runtime, repair persistence |
| Clients | Complete APIs, offline/replay states, device request signature, APNs/ActivityKit targets |
| Release | Milestone commits/checkpoints on the single integration branch, migration rehearsal, human authority gates, rollback and support ownership |

### Review sections 1–11

#### 1. Architecture

The subsystem boundaries are directionally sound, but there is no complete composition root.
Foundation persists events but its daemon task does not project them into `LoopGuard` or dispatch
them to later services. Context creates a journal, watcher, leases, digest, and MCP server but does
not register them with the daemon. Add an explicit `DaemonServices`/event-dispatch contract and an
end-to-end hook-to-proof integration test.

The canonical action contract is session-only while repair publication also uses `/v1/actions`.
Replace `session_id` as the target with a typed action target and optional session binding.

#### 2. Error and rescue map

| Codepath | Named failure | Required handling | User-visible result |
|---|---|---|---|
| Hook -> daemon | `DaemonUnavailable`, `HookDeadlineExceeded` | Fail open for observation; fail closed only by explicit policy | “LoopGuard unavailable; session continues” or policy block reason |
| Daemon frame ingest | `FrameTooLarge`, `UnsupportedSchema`, `InvalidEvent` | Reject before allocation/persistence; structured local audit | Integration unhealthy with exact corrective action |
| SQLite append | `StoreBusy`, `StoreCorrupt`, `DiskFull` | Bounded retry for busy; stop acknowledgement on durability failure | Local guard degraded; no false persisted acknowledgement |
| Event dispatch | `HandlerUnavailable`, `HandlerTimeout` | Persist handler status; isolate optional handlers; never hide required failure | Capability-specific degraded state |
| Baseline capture | `UntrustedCommand`, `BaselineMissing`, `CommandTimeout` | Require trust/sandbox; never classify missing baseline as pre-existing | “Tests ran, verification inconclusive” |
| Cloud relay | `HostAuthFailed`, `CursorConflict`, `Backpressure` | Rotate/re-pair or replay with jitter from last durable cursor | Local mode continues; cloud status degraded |
| Remote action | `InvalidDeviceProof`, `ExpiredAction`, `StaleState`, `UnsupportedCapability` | Fail closed, audit, never retry mutation blindly | Exact rejection reason and refreshed current state |
| Artifact upload | `ChecksumMismatch`, `ArtifactTooLarge`, `RetentionExpired` | Reject completion/delete partial object | Evidence unavailable, never marked complete |
| Candidate generation | `PromptInjectionDetected`, `CandidateBudgetExceeded`, `SandboxViolation` | Isolate, terminate candidate, retain sanitized evidence | Candidate rejected; repair continues only if policy permits |
| Draft publication | `BaseAdvanced`, `BranchCollision`, `GitHubPermissionDenied` | Re-evaluate against new base or request operator action | Repair remains awaiting publication |

Catch-all exception handling may exist only at process boundaries. It must map named domain errors,
preserve correlation IDs, and never turn a failed required step into success.

#### 3. Security and threat model

Critical gaps are repository-controlled verification commands, plaintext early local persistence,
an unauthenticated local protocol, device keys that do not participate in action authorization,
browser auth that conflicts with HttpOnly tokens, and failure/webhook content entering repair
generation without a hostile-input boundary. These controls move into the owning plans rather than
waiting for the final hardening phase.

#### 4. Data flow and interaction edge cases

```text
HOOK/SDK EVENT
   -> size/schema validation
   -> redact
   -> durable local append
   -> ordered dispatch
        -> loop policy
        -> change journal
        -> verification
        -> relay outbox
   -> capability-scoped response
   -> vendor renderer

shadow paths:
missing baseline -> INCONCLUSIVE, never VERIFIED
duplicate event  -> same local sequence result, no duplicate side effect
cursor gap       -> replay from owning cursor domain
stale action     -> reject and fetch current state
optional service down -> local deterministic guard remains available
```

Attached sessions cannot be transparently moved into separate worktrees and cannot promise generic
interrupt/inject control. They must warn on shared-worktree risk and expose only supported actions.

#### 5. Code quality

The design and executable contracts have drifted: `PolicyDecision` action names differ,
`AgentAdapter` is incomplete in the executable plan, `ChangeRecord` has no cursor while digest
tests require one, `ModelSpec.source` is required but omitted by its first test, and several Node
packages use `npm ci` without declaring lockfiles. One canonical contract matrix and executable
contract fixtures must land before subsystem code.

#### 6. Test review

The existing plan covers many unit paths but is missing these release-blocking integrations:

- Hook -> daemon persistence -> loop decision -> vendor-specific response.
- Prompt intake -> pre-mutation baseline -> change -> deterministic proof.
- Hook/filesystem duplicate observation -> one change with merged provenance.
- Interleaved repositories/sessions -> independent cursor replay without false gaps.
- Browser BFF login -> one-use stream ticket -> replay -> remote action -> daemon resolution.
- Device revocation -> action proof rejection.
- Signed failure webhook -> sanitized fixture -> isolated candidates -> draft-only publication.
- Daemon/worker/database crash after persistence but before acknowledgement.
- Full milestone upgrade/rollback from the prior released schema.

#### 7. Performance

The 100 ms hook budget needs a non-blocking optional-handler design and measured p95 on real local
sockets. Synchronous SQLite work on the event loop needs serialization/backpressure. The browser
broker must first prove that a Playwright fixture can reuse its process; a standalone `page.run`
protocol does not accelerate ordinary `npx playwright test` by itself.

#### 8. Observability and debuggability

Event, repository, session, turn, action, verification, repair, workflow, and request IDs must be
propagated without prompts/source in labels. Each handler records accepted, persisted, dispatched,
completed, failed, and replayed counters. “Local works, cloud degraded” must be a first-class health
state, not a generic error.

#### 9. Deployment and rollout

Threat modeling and local security cannot remain Plan 11-only work. Production claims also exceed
the current infrastructure plan: regional failover is required but only single-region multi-AZ is
specified; billing lacks an entitlement/payment provider; GA SSO lacks organization lifecycle and
SCIM. Either add those tasks or explicitly move them outside GA.

#### 10. Long-term trajectory

The architecture can become a platform if proof/evidence remains the spine. It becomes four
unrelated products if phone control, model routing, design review, and data repair each become a
separate headline. Keep one product narrative and separately gated release tracks.

#### 11. Design and UX

The native hierarchy and selective Liquid Glass direction are specific enough to avoid generic
styling, but the client plan names Changes and Verification navigation without implementing their
pages/APIs. It also claims Live Activities without a Widget extension target. Web authentication
needs a server-side BFF because browser JavaScript cannot read HttpOnly tokens or attach bearer
headers to a native WebSocket.

### CEO dual voice consensus table

| Dimension | Claude independent voice | Codex independent voice | Consensus |
|---|---|---|---|
| Premises valid? | N/A: authentication unavailable | Proof premise strong; remote/cost premises weak | Not cross-model confirmed |
| Right problem? | N/A | Lead with verified completion | Not cross-model confirmed |
| Scope calibrated? | N/A | Too broad for one launch | Not cross-model confirmed |
| Alternatives explored? | N/A | Missing simpler hook/MCP/CI approaches | Not cross-model confirmed |
| Competitive risks covered? | N/A | Remote control and loop detection are crowded | Not cross-model confirmed |
| Six-month trajectory sound? | N/A | Big-bang sequence delays validation | Not cross-model confirmed |

Codex reported 19 objective discrepancies and 8 product-taste concerns. The primary review
independently confirmed the contract, API, daemon-composition, trust, browser, auth, action, cursor,
and delivery gaps. Product-scope reductions are not applied automatically because the user
explicitly requested the full roadmap.

### Failure modes registry

| Codepath | Failure mode | Rescued? | Test planned? | User sees? | Logged? |
|---|---|---:|---:|---:|---:|
| Daemon | Persists but never evaluates/distributes event | No | No | Silent | No |
| Verification | Baseline captured after mutation | No | No | False confidence | Partial |
| Context | Global/content cursor interpreted as session cursor | No | No | Endless resync/gap | Partial |
| Remote action | Paired device revoked but bearer still acts | No | No | Unauthorized control | Audit only after action |
| Web stream | HttpOnly token unavailable to browser WebSocket | No | No | Reconnect failure | Partial |
| Repair | No durable repair/candidate schema | No | No | Missing/vanishing repair | Partial |
| Browser | Broker unused by Playwright runner | No | No | No speed benefit | Metrics misleading |
| Hook cloud fallback | No signed HTTPS ingest lifecycle | No | No | Cloud sessions absent | Partial |
| Evidence | Hash points to mutable/deleted blob | No | No | Proof cannot be reproduced | Partial |
| Attached concurrency | Two agents share primary worktree | Warning only | Partial | Collision warning | Yes |
| Unknown model price | Reported as zero | No | Existing test expects zero | Misleading savings | Warning |

Rows 1–9 and 11 are **CRITICAL GAPS** for the claimed production behavior.

### NOT in scope

- Automatic merge or deployment of generated repairs.
- Hidden chain-of-thought capture or transfer.
- Pretending attached sessions support controls their native surface does not expose.
- A generic phone terminal mirror that duplicates vendor Remote Control products.
- Automatic production infrastructure apply, App Store submission, or real external repair PR
  without separate human authority.

### Dream-state delta

If the objective discrepancies are fixed, the roadmap ends with the intended proof-centered
platform. If the plans are executed unchanged, it ends with individually tested packages that
lack a trustworthy event composition path, complete client API, and coherent security sequence.

### Implementation tasks from Phase 1

These are review findings mapped into the 88 numbered subsystem tasks. Do not execute or commit
them as additional tasks; their checkboxes close only when their owning numbered work is verified.

- [x] **CEO-T1 (resolved 2026-07-14)** — Current `loopguard/AGENTS.md` explicitly authorizes the
  production roadmap while preserving the stable core; there is no instruction conflict.
- [ ] **CEO-T2 (P1)** — Freeze canonical event, cursor, decision, action-target, and adapter contracts.
- [ ] **CEO-T3 (P1)** — Add daemon dispatch and hook-to-loop end-to-end behavior.
- [ ] **CEO-T4 (P1)** — Move local state/socket/trust controls into foundation.
- [ ] **CEO-T5 (P1)** — Define attached-mode proof intake and pre-mutation baseline ownership.
- [ ] **CEO-T6 (P1)** — Separate observation identity, content fingerprint, and cursor domains.
- [ ] **CEO-T7 (P1)** — Add repository trust and sandbox policy for discovered commands.
- [ ] **CEO-T8 (P1)** — Complete session/action/change/verification/repair APIs and persistence.
- [ ] **CEO-T9 (P1)** — Require device-signed action proof and signing-key rotation.
- [ ] **CEO-T10 (P1)** — Define the web BFF and one-use stream-ticket flow.
- [ ] **CEO-T11 (P1)** — Connect the browser broker through a real Playwright fixture and benchmark.
- [ ] **CEO-T12 (P1)** — Add signed hostile-input repair intake and stronger sandbox isolation.
- [ ] **CEO-T13 (P1)** — Make evidence content-addressed, signed, retained, and replayable.
- [ ] **CEO-T14 (P1)** — Add missing lockfiles, dependency-install gates, and self-contained fixtures.
- [ ] **CEO-T15 (P1)** — Keep the user-selected integration branch and one draft PR, but enforce
  proof-first vertical sequencing through milestone commits, task commits, cumulative plan gates,
  and PR-body checkpoints.

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | CEO | Use SELECTIVE EXPANSION review posture | Mechanical | Completeness | Preserve all explicit requirements while fixing the whole blast radius | Silent scope reduction |
| 2 | CEO | Keep proof/evidence as the shared product spine | Taste, pending premise gate | Explicit over clever | One auditable outcome ties the subsystems together | Equal-weight product headlines |
| 3 | CEO | Keep cost reduction secondary until holdouts prove it | Mechanical | Completeness | Avoid an unverified savings claim | Estimated savings presented as fact |
| 4 | CEO | Keep Auto-Heal as a separately gated subsystem | Mechanical | DRY | It reuses proof/action/artifact contracts without entering `LoopGuard.observe()` | Repair logic inside the detector |
| 5 | CEO | Retain native iOS and Auto-Heal in the full roadmap | User direction | Bias toward action | The user explicitly requested both; no cross-model consensus exists to remove them | Silent deletion from scope |
| 6 | CEO | Keep one integration branch and one draft PR with milestone controls | User direction, premise gate resolved | Pragmatic | Preserves the requested delivery model while bounding recovery and review risk with task commits and cumulative gates | Multiple milestone branches/PRs |
| 7 | Design | Use attention, proof, then safe action as the shared screen hierarchy | Mechanical | Explicit over clever | It makes the proof-centered product legible in five seconds and keeps raw telemetry secondary | Metric-first dashboard |
| 8 | Design | Implement Changes, Verification, Repairs, and Settings as real destinations | Mechanical | Completeness | Navigation cannot promise dead or placeholder destinations | Defer missing screens to polish |
| 9 | Design | Reuse the Expo visual language but keep native iOS system typography | Mechanical | DRY | Preserves the approved product character without breaking Dynamic Type and platform metrics | Port Expo fonts unchanged to SwiftUI |
| 10 | Design | Limit Liquid Glass to native navigation and transient controls | Taste, auto-decided | Explicit over clever | Satisfies the iOS 26 direction while keeping evidence/content calm and readable | Glass content cards or no native glass |
| 11 | Design | Use a web BFF and one-use stream tickets | Mechanical | Completeness | HttpOnly auth and browser WebSocket constraints require an explicit safe flow | Bearer token in browser JavaScript or URL |
| 12 | Design | Specify every async, stale, offline, expired, and resyncing state | Mechanical | Completeness | Reliability software cannot hide uncertainty behind blank panels or spinners | Happy-path-only screens |
| 13 | Design | Use intentional layouts for wide web, tablet, mobile web, iPhone, and iPad | Mechanical | Completeness | “Stack on mobile” would hide actions and make dense evidence unusable | Desktop-only shell |
| 14 | Design | Require a WidgetKit target for Live Activities | Mechanical | Explicit over clever | ActivityKit UI cannot exist as ordinary app-only files | Implicit extension setup |
| 15 | Eng | Dispatch every persisted event through a `DaemonServices` composition root | Mechanical | Explicit over clever | The daemon previously acknowledged storage without invoking the real guard or subsystem handlers | Persistence-only daemon |
| 16 | Eng | Separate all five cursor domains by name and tests | Mechanical | Completeness | Global/repository/session/cloud/client positions cannot safely share generic `cursor` semantics | Context-dependent cursor guessing |
| 17 | Eng | Encrypt local event bodies and enforce owner/peer/protocol boundaries in Foundation | Mechanical | Completeness | Security must exist before real session data is persisted | Defer local security to final hardening |
| 18 | Eng | Require trusted task intake and baseline before first mutation | Mechanical | Completeness | Tests passing after a late attach cannot prove no regression was introduced | Green “verified” without baseline ownership |
| 19 | Eng | Treat repository verification commands as untrusted executable code | Mechanical | Completeness | Discovery from package/agent config cannot grant execution, network, or secret access | Auto-run discovered scripts |
| 20 | Eng | Bind MCP context identity with a daemon-issued local capability | Mechanical | Explicit over clever | Caller-supplied repository/session IDs are spoofable | Trust MCP arguments |
| 21 | Eng | Connect acceleration through an opt-in real Playwright Test fixture and native benchmark | Mechanical | Completeness | A standalone page RPC does not speed ordinary Playwright Test | Claim speed from warm probe only |
| 22 | Eng | Require registered device/WebAuthn proof before cloud countersigning actions | Mechanical | Completeness | Pairing has no authorization value unless the key proves each action | Bearer-only remote control |
| 23 | Eng | Bind relay/hook intake to tenant, host, repository handle, signature, and nonce | Mechanical | Completeness | Client-supplied repository identity permits cross-tenant substitution | Trust `repo_id` in event payload |
| 24 | Eng | Store signed content-addressed evidence and verify object checksums | Mechanical | Completeness | A database hash or S3 ETag does not prove immutable bytes exist | Hash-only or ETag validation |
| 25 | Eng | Isolate hostile repair evidence and candidate execution from tools, network, and credentials | Mechanical | Completeness | Pipeline logs and repositories are attacker-controlled inputs | Prompt-only “be safe” instruction |
| 26 | Eng | Keep one branch/PR but enforce task commits and cumulative milestone gates | User direction | Pragmatic | Preserves the chosen delivery model while bounding recovery and review risk | Multiple PRs or unstructured big-bang commits |
| 27 | DX | Target AI-heavy local developers first, platform evaluators second | Mechanical | Explicit over clever | The first user already runs Codex/Claude and wants protection without adopting a new chat client | Enterprise-admin-first onboarding |
| 28 | DX | Use DX EXPANSION for the new product | Mechanical | Completeness | Productization needs a complete install/debug/upgrade/community journey, not only polished existing commands | Triage-only review |
| 29 | DX | Target offline magic under 2 minutes and protected session under 5 minutes | Taste, auto-decided | Bias toward action | Separates zero-key evaluation from unavoidable vendor trust while remaining competitive | One long cloud/account setup path |
| 30 | DX | Make `loopguard quickstart` the zero-key magical moment | Mechanical | Explicit over clever | It demonstrates the real daemon/store/dispatcher/guard before asking for permissions or keys | Video-only or live-provider demo first |
| 31 | DX | Add one idempotent `loopguard setup --agent auto` golden path | Mechanical | Completeness | Detection, service install, hook merge, trust status, and synthetic verification belong in one guided flow | Multiple manual install guides |
| 32 | DX | Standardize CLI/API errors and local explanations | Mechanical | Completeness | Developers need problem, cause, fix, request ID, and docs without searching source | Raw exceptions or prose-only errors |
| 33 | DX | Ship docs, changelog, migrations, dry-run/JSON, safe uninstall, and local DX timing with features | Mechanical | Completeness | The full journey includes debug and upgrade, not only first install | Documentation after implementation |
| 34 | DX | Block public package/source distribution until owner supplies legal terms | External authority | Pragmatic | An implementation agent cannot invent a license or publish under ambiguous rights | Silently choose a license |
| 35 | Reconcile | Keep attached Claude hooks separate from managed Claude authentication | Mechanical | Capability truth | Anthropic documents API/provider credentials for third-party Agent SDK products and requires separate approval before offering `claude.ai` login or rate limits | Proxy consumer subscription authentication |

### Phase 1 completion summary

```text
Mode: SELECTIVE EXPANSION
System audit: current demo core reusable; repository instructions explicitly authorize the roadmap
Objective discrepancies: 19 from Codex, with additional daemon-composition and dependency gaps confirmed
Critical failure-mode gaps: 10
Claude independent voice: unavailable due missing current authentication
Codex independent voice: completed
Cross-model consensus: not claimed
Premise gate: passed; retain all features and use one integration branch plus one draft PR
Unresolved decisions: 0
```

## GSTACK AUTOPLAN REVIEW — PHASE 2: PRODUCT DESIGN

### Design scope and initial rating

The UI scope is production-sized: a responsive web operations console, a native iOS safety inbox,
remote approvals, changes and verification evidence, repairs, policy/cost/device/audit
administration, notifications, and Live Activities.

**Initial design completeness: 5/10.** The plans named the correct screens, the approved workbench
style, and important action-safety details. They did not define a complete hierarchy, screen-state
matrix, production design-system boundary, responsive behavior, or several destinations promised
by navigation. A 10/10 plan makes those choices explicit enough that two implementers build the
same product without inventing UX during coding.

Visual mockups were not generated: the gstack designer binary is installed, but no gstack/OpenAI
design credential is configured. The review therefore uses the approved Expo redesign, its locked
design system, and current components as the visual reference.

### What already exists

- `cloud-app/design.md` defines the approved austere operational-workbench language, token names,
  Space Grotesk/IBM Plex typography, status grammar, spacing, motion, CTA voice, and anti-slop
  constraints.
- `cloud-app/src/theme.ts` is the current opaque-sRGB native token export and centralizes status
  labels/colours, hit targets, control height, radii, spacing, and type roles.
- `cloud-app/src/components/ui.tsx` already demonstrates reusable interaction patterns: compact
  page headers, separated sections, labelled inputs, explicit focus/pressed/loading/disabled
  states, status badges, and useful empty states.
- `cloud-app/src/screens/MissionControlScreen.tsx` provides the current compact operational
  hierarchy: run state, metrics, bounded event timeline, intervention surface, and scoped error
  feedback.
- The approved June 30 phone redesign explicitly rejects ambient decoration, fake chrome,
  card-in-card layouts, emoji controls, side stripes, and ornamental glass.

Production clients reuse those principles, not the Expo component code. Native SwiftUI uses system
typography and iOS metrics; the web uses the approved custom type families and semantic tokens.

### Pass 1 — Information architecture: 5/10 → 10/10

The original web navigation listed Changes and Verification but created no corresponding pages.
The iOS navigation listed Changes, Repairs, and Settings while its tasks built only Inbox and Runs.
That leaves first-run orientation and core proof flows to the implementer.

The control-surface plan now defines separate web and iOS trees and the first three scan targets on
every operational screen: what needs attention, what proof explains it, and what safe action is
available. It adds real list/detail routes for Changes, Verification, and Repairs plus native
Changes, Repairs, and Settings destinations. Raw telemetry stays behind disclosure.

### Pass 2 — Interaction-state coverage: 3/10 → 10/10

Approval expiry and timeline replay had useful tests, but most surfaces were happy-path
descriptions. A reliability product that shows a blank proof panel, stale action, or endless
spinner actively damages trust.

The plan now specifies loading, empty, error, success, partial, offline/stale, cursor-resync,
expired, revoked, and per-candidate failure behavior for Inbox, Runs, Changes, Verification,
Approvals, Repairs, and admin screens. Every async control has idle, focus/pressed, loading,
disabled, error, and confirmed states. Uncertainty is labelled; destructive controls fail closed.

### Pass 3 — User journey and emotional arc: 4/10 → 10/10

The original tasks described screens independently but not the moment from push notification to
audited outcome. The plan now storyboards the operator journey:

```text
notification
  -> exact attention item
  -> current state + proof + provenance
  -> explicit effect/risk/expiry review
  -> device-signed, server-confirmed action
  -> immutable receipt and later audit
```

The five-second layer answers “what needs me?”, the five-minute layer supports verification and
action without a terminal, and the long-term layer builds confidence through stable proof and
complete provenance.

### Pass 4 — AI-slop resistance: 7/10 → 10/10

The existing Expo direction already rejects the main slop patterns. The remaining risk was
describing the web as a generic dashboard and applying Liquid Glass without a material boundary.

Classifier: **APP UI**. The locked contract now rejects dashboard-card mosaics, decorative
gradients/blobs, coloured icon circles, fake browser chrome, indiscriminate glass, all-centred
hierarchy, decorative copy, and status conveyed only by colour. Cards are reserved for bounded
interactive objects. Liquid Glass is limited to native navigation and transient controls; content
and evidence remain on standard surfaces.

### Pass 5 — Design-system alignment: 5/10 → 10/10

The approved Expo system said no glass and used custom fonts, while the production spec asked for
system typography and iOS 26 Liquid Glass. The plan did not say which source won.

The production rule is now explicit: reuse the Expo colour/status/density/content language; use
Space Grotesk, IBM Plex Sans, and IBM Plex Mono on web; use system type and SF Symbols on native
iOS; permit Liquid Glass only in native navigation/controls. `docs/product/design-system.md`
becomes the production source for semantic light/dark tokens and platform divergence.

### Pass 6 — Responsive design and accessibility: 6/10 → 10/10

The original plan mentioned Dynamic Type, VoiceOver, and iPad keyboard navigation but did not
specify responsive transformations or full accessibility behavior.

The executable plan now covers labelled sidebar, compact rail, navigation sheet, responsive
tables, sticky safe-area actions, iPhone navigation stacks, iPad split view, coarse/fine pointer
targets, focus order, WCAG 2.2 AA, landmarks, headings, live-region restraint, Voice Control,
Differentiate Without Color, Increase Contrast, Reduce Motion, largest Dynamic Type, and
keyboard-only flows. Visual gates cover 320/768/1280/1536 web widths, iPhone, and iPad.

### Pass 7 — Unresolved design decisions: 4/10 → 10/10

| Former ambiguity | Resolution |
|---|---|
| Which surface is the product home? | Inbox is the attention queue; Runs is history/current execution, not a competing dashboard |
| Does web receive bearer tokens? | No; same-origin BFF owns tokens and emits one-use, 30-second stream tickets |
| Does native iOS inherit Expo fonts? | No; system typography preserves Dynamic Type and platform behavior |
| Where is glass allowed? | Native navigation/transient controls only |
| What happens on tablet/mobile web? | Compact rail or navigation sheet plus single-pane/inspector behavior |
| Are Changes/Verification/Repairs/Settings placeholders? | No; each is a tested real destination |
| How is Live Activity shipped? | Separate WidgetKit extension with non-sensitive shared attributes |
| What is shown when proof is missing? | An explicit inconclusive/missing-baseline state, never an empty success surface |

No design decision remains unresolved. The one aesthetic choice, restrained navigation-only
Liquid Glass, was auto-decided under the user's explicit iOS 26 and non-slop direction and remains
visible in the audit trail.

### Design dual voices

```text
CLAUDE SUBAGENT (design): unavailable — Claude CLI has no current authentication
CODEX SAYS (design): unavailable — managed policy rejected exporting private plan contents
Fallback: full repository-grounded primary review
Cross-model consensus: not claimed
```

| Design review dimension | Codex voice | Claude voice | Consensus |
|---|---|---|---|
| Information architecture | unavailable | unavailable | not established |
| Loading/empty/error/stale/offline states | unavailable | unavailable | not established |
| User journeys and action safety | unavailable | unavailable | not established |
| Visual hierarchy and anti-slop | unavailable | unavailable | not established |
| Design-system ownership | unavailable | unavailable | not established |
| Responsive behavior and accessibility | unavailable | unavailable | not established |
| Remaining design decisions | unavailable | unavailable | not established |

### Design litmus scorecard

| Litmus check | Before | After plan fixes | Evidence |
|---|---:|---:|---|
| Product unmistakable in first screen? | No | Yes | Inbox is a LoopGuard-specific queue of loops, regressions, stale context, and approvals |
| One strong visual anchor? | Partial | Yes | Live state/proof/action is the anchor; no decorative hero or metric mosaic |
| Understandable by scanning headings? | Partial | Yes | Attention → proof → safe action hierarchy and direct utility headings |
| Each section has one job? | Partial | Yes | Explicit list/detail and progressive-disclosure roles |
| Cards actually necessary? | Partial | Yes | Cards limited to bounded approvals/candidates; rows and panes handle ordinary content |
| Motion improves hierarchy/feedback? | Yes | Yes | Direct manipulation and state transitions only, with reduced-motion fallback |
| Premium without decorative shadows? | Yes | Yes | Tokenized type, spacing, contrast, density, and native materials carry the design |

### Design failure modes

| Failure mode | Planned prevention |
|---|---|
| Dead navigation destination | Route/screen inventory and navigation UI tests |
| Stale data presented as current | Visible freshness/resync state; risky actions disabled |
| Generic dashboard/card grid | Locked design contract plus deterministic source/design checks |
| Glass reduces evidence readability | Material wrapper restricted to native chrome |
| Long identifiers destroy layout | Truncation plus accessible full value and copy action |
| Dynamic Type clips confirmation | Largest-size UI fixtures and hittability assertions |
| Screen reader flooded by events/countdown | Thresholded announcements; timeline is not a continuous live region |
| Browser auth leaks into JavaScript/URL | BFF and one-use stream-ticket tests |
| Live Activity leaks repository/source data | Allowlisted non-sensitive attributes and preview tests |

### Design NOT in scope

- Marketing/landing pages, pricing presentation, or brand campaign work.
- Reimplementing the Expo prototype as the production native client.
- Decorative animation, ambient effects, or a custom navigation metaphor.
- A phone terminal/transcript mirror; the phone exposes status, evidence, and explicit actions.
- Automatic action confirmation or destructive swipe shortcuts.

### Design implementation tasks

These are review findings mapped into the 88 numbered subsystem tasks. Do not execute or commit
them as additional tasks; their checkboxes close only when their owning numbered work is verified.

- [ ] **DESIGN-T1 (P1, human: ~3h / CC: ~25min)** — Design system — Write the
  production cross-platform design contract with semantic light/dark tokens and explicit web/iOS
  divergence.
  - Surfaced by: Pass 5 — conflicting prototype and native rules.
  - Files: `docs/product/design-system.md`, `apps/web/src/styles/tokens.css`,
    `apps/ios/LoopGuard/DesignSystem/`
  - Verify: token/source sweep plus light/dark component fixtures.
- [ ] **DESIGN-T2 (P1, human: ~1d / CC: ~1h)** — Navigation — Implement every promised web and
  iOS destination with attention/proof/action hierarchy.
  - Surfaced by: Pass 1 — dead and missing destinations.
  - Files: `apps/web/src/app/(console)/`, `apps/ios/LoopGuard/Features/`
  - Verify: responsive-navigation and native navigation UI tests.
- [ ] **DESIGN-T3 (P1, human: ~6h / CC: ~45min)** — State system — Implement the complete
  asynchronous, offline, stale, resyncing, expired, and revoked state contract.
  - Surfaced by: Pass 2 — happy-path-only surface descriptions.
  - Files: `apps/web/src/components/async-state.tsx`,
    `apps/ios/LoopGuard/DesignSystem/AsyncStateView.swift`
  - Verify: component fixtures, reducer tests, and accessibility announcements.
- [ ] **DESIGN-T4 (P1, human: ~4h / CC: ~30min)** — Responsive/a11y — Add intentional viewport,
  input-method, assistive-technology, and largest-text gates.
  - Surfaced by: Pass 6 — incomplete responsive and accessibility contracts.
  - Files: `apps/web/e2e/`, `apps/ios/LoopGuardUITests/`
  - Verify: Playwright viewports/axe and Xcode UI tests/screenshots.
- [ ] **DESIGN-T5 (P1, human: ~4h / CC: ~30min)** — iOS system surfaces — Add the real WidgetKit
  extension and constrained Liquid Glass wrappers.
  - Surfaced by: Passes 4 and 7 — implicit extension/material ownership.
  - Files: `apps/ios/project.yml`, `apps/ios/LoopGuardWidgets/`,
    `apps/ios/LoopGuardShared/`
  - Verify: extension build, previews, entitlement inspection, and sensitive-content fixture tests.
- [ ] **DESIGN-T6 (P2, human: ~3h / CC: ~20min)** — Anti-slop gate — Reject token bypass,
  decorative patterns, content glass, and colour-only state.
  - Surfaced by: Pass 4 — generic dashboard regression risk.
  - Files: `apps/web/e2e/design-system.spec.ts`, `apps/ios/LoopGuardUITests/VisualStateUITests.swift`
  - Verify: deliberate violating fixtures fail before removal.

No design debt is deferred to `TODOS.md`; all findings are inside the requested production
surface and have been added to the executable plan.

### Phase 2 completion summary

```text
System audit: approved Expo workbench system exists; production cross-platform source was missing
Initial design score: 5/10
Final design score: 10/10 at plan level
Passes: IA 5→10, states 3→10, journey 4→10, slop 7→10, system 5→10, responsive/a11y 6→10
Decisions resolved: 8
Decisions deferred: 0
Mockups: 0; designer credential unavailable
Dual voices: both unavailable; Claude lacks authentication and external Codex export is policy-blocked
Cross-model consensus: not claimed
Unresolved design decisions: 0
```

## GSTACK AUTOPLAN REVIEW — PHASE 3: ENGINEERING

### Step 0 — Scope challenge

This program is intentionally much larger than the eight-file/two-service complexity smell
threshold. The minimum defensible product wedge is the local proof path, but the user explicitly
confirmed that the full roadmap remains in this execution program and selected one integration
branch plus one draft PR. Scope was therefore **accepted as-is**. The review reduced accidental
complexity and closed dependency gaps; it did not delete requested subsystems.

Existing primitives reused instead of rebuilt:

- `LoopEvent -> LoopGuard -> LoopDecision` remains the inner circuit breaker.
- Exact, semantic, ping-pong, and budget detectors remain deterministic.
- The existing judge/provider interfaces remain optional and lazy.
- The current CLI, demo projects, FastAPI demo server, and Expo prototype remain compatibility
  fixtures rather than production control-plane foundations.
- Existing Expo tokens/components establish the product's operational design language.
- Git, pytest/JUnit, Jest JSON, Playwright, OIDC, PostgreSQL, Temporal, platform credential stores,
  and GitHub App APIs are used through their standard contracts rather than custom replacements.

### Architecture review — 12 issues found and folded into executable plans

```text
Codex / Claude / filesystem / CI / pipeline / operator
                         │
            vendor adapter + capability truth
                         │
       bounded authenticated ControlEvent protocol
                         │
                  loopguardd
                         │
      validate -> redact -> encrypted durable append
                         │
                DaemonServices.dispatch
              ┌──────────┼───────────┬─────────────┐
              │          │           │             │
       per-session    Change      Proof intake   Router/
       LoopGuard      Journal     + verifier     Preferences
              │          │           │             │
              └──────────┴─────┬─────┴─────────────┘
                               │
                    normalized PolicyDecision
                               │
                attached renderer / managed adapter
                               │
              outbound signed relay (local still works)
                               │
            FastAPI + PostgreSQL RLS + outbox + Temporal
          ┌───────────────┬───────────────┬───────────────┐
          │               │               │               │
     replay stream    signed actions   proof/artifacts  repair workflow
          │               │               │               │
      Web BFF      device/WebAuthn +      signed       restricted worker
      + iOS          cloud signature     manifests      -> draft PR only
```

Load-bearing corrections:

1. **[P0] (confidence: 10/10)** Foundation Task 5 originally ended at
   `self.store.append(...)` plus an acknowledgement. It now has a composition root, per-session
   guard registry, durable handler queue, crash replay, and a hook-to-real-guard integration test.
2. **[P0] (confidence: 10/10)** `PolicyDecision` originally used
   `Literal["allow", "warn", "block", "request_approval", "inject"]`, which contradicted the
   approved pause/interrupt contract. Canonical actions and typed targets/state hashes are frozen.
3. **[P0] (confidence: 10/10)** A generic `cursor` represented host, repository, session, cloud,
   and client positions. All five domains now have distinct names, allocation, replay, and
   interleaving tests.
4. **[P0] (confidence: 10/10)** Foundation storage originally wrote plaintext `body TEXT` and the
   daemon unlinked a socket without owner/type checks. Encryption, platform-protected keys,
   permissions, peer identity, frame bounds, migrations, and recovery now land before real data.
5. **[P0] (confidence: 10/10)** `AgentAdapter` originally declared only `events()`. It now matches
   the spec's attach/start/inject/interrupt/events contract and adds typed action resolution.
6. **[P0] (confidence: 10/10)** MCP tools accepted caller-supplied `repo_id`/`session_id`. A
   daemon-issued short-lived local capability now binds identity and allowed tools.
7. **[P0] (confidence: 10/10)** Verification discovered repository commands but had no trust
   boundary. Command hash approval, preview, containment, secret/network denial, isolation level,
   and unsandboxed fallback rules are now explicit.
8. **[P0] (confidence: 10/10)** A passing attached session could be labelled verified without proof
   that baseline preceded mutation. Intake ordering is now part of the proof state machine.
9. **[P0] (confidence: 10/10)** Pairing a phone/device did not prove authorization for each action.
   Clients now sign the canonical current-state action; the cloud verifies and countersigns it with
   rotating identified keys.
10. **[P0] (confidence: 10/10)** Relay and cloud-hook intake trusted repository identity too far.
    Opaque server-bound repository handles, scoped credentials, canonical signatures, nonce replay
    protection, and key lifecycle close that boundary.
11. **[P1] (confidence: 10/10)** The browser broker's `page.run` path did not integrate ordinary
    Playwright Test. The plan now includes an opt-in fixture/config package and native baseline.
12. **[P0] (confidence: 10/10)** Auto-Heal treated failure/log content as ordinary prompt context
    and hosted Docker isolation was underspecified. Hostile-data envelopes, managed generator
    protocol, no-credential/network tool policy, gVisor/microVM isolation, and idempotent GitHub
    publication are now required.

### Code-quality review — 8 issues found and folded

- **Contract drift:** One canonical action, target, cursor, and adapter vocabulary now spans spec,
  foundation, cloud, clients, and tests.
- **Identity drift:** `observation_id`, `content_fingerprint`, and stored `record_id` are separate;
  repeated transitions no longer collapse history.
- **Composition drift:** Context, proof, routing, preferences, browser, and relay services must
  register with `DaemonServices`; standalone packages do not count as implemented behavior.
- **Undefined test helpers:** Every illustrative fixture/helper must be implemented in listed
  support modules; snippets cannot commit pseudocode names.
- **Dependency drift:** Every Node package has a lockfile and `npm ci`; Python product extras extend
  `all-dev` and have a metadata assertion.
- **Process recovery:** Managed adapters, verification commands, broker contexts, workflows, and
  publication have explicit recovering/orphaned/idempotent states.
- **Evidence drift:** Content-addressed encrypted objects plus signed manifests replace mutable
  paths and hash-only records.
- **Platform truth:** Windows named pipe/service and iOS WidgetKit are explicit artifacts/tests;
  capability reports cannot claim unavailable implementations.

Inline ASCII diagrams should be maintained in implementation comments for:

- `control/dispatch.py`: persist/dispatch/ack and crash boundary.
- `control/store.py`: five cursor domains and allocation ownership.
- `verify/service.py`: proof/baseline/verification recovery state machine.
- `context/worktrees.py`: allocation/quarantine/cleanup state machine.
- `control/actions.py`: device proof -> cloud signature -> local consume flow.
- `heal/repair_workflow.py`: durable activity and publication gates.
- `browser/service.py`: daemon/broker/fixture/context ownership.

### Test review — execution and user-flow coverage

```text
CODE PATHS                                             USER / OPERATOR FLOWS

[+] Hook -> daemon -> existing guard                  [+] Automatic session protection
  ├─ [★★★ PLANNED] valid event/persist/dispatch         ├─ [★★★] repeated tool loop -> warning/pause
  ├─ [★★★] duplicate + crash before ack                 ├─ [★★★] daemon down -> explicit fail-open policy
  ├─ [★★★] wrong peer/version/oversized frame           └─ [★★★] restart -> no lost/double decision
  └─ [★★★] handler lag/failure without event loss

[+] Prompt -> baseline -> mutation -> proof           [+] Fix bug without introducing another
  ├─ [★★★] managed ordering guaranteed                  ├─ [★★★] old failure stays pre-existing
  ├─ [★★★] attached late/missing baseline               ├─ [★★★] new failure becomes regression
  ├─ [★★★] untrusted command preview/revocation         └─ [★★★] missing proof never shows verified
  ├─ [★★★] timeout/output/process-tree/isolation
  └─ [★★★] signed artifact manifest/recovery

[+] Change observation -> shared context             [+] Switch agents / avoid collisions
  ├─ [★★★] hook + watcher provenance merge              ├─ [★★★] bounded delta handoff
  ├─ [★★★] repeated same content at later time          ├─ [★★★] managed worktree isolation
  ├─ [★★★] overflow/restart current-state recovery      └─ [★★★] attached shared-tree warning/block
  └─ [★★★] MCP repository/session spoof rejected

[+] Managed adapter -> router -> phase               [+] Right model/effort with bounded overhead
  ├─ [★★★] capability unavailable/process exit          ├─ [★★★] no mid-tool switch
  ├─ [★★★] catalog signature/stale/unknown price         ├─ [★★★] unknown cost stays unknown
  ├─ [★★★] concurrent budget + judge single-flight      └─ [★★★] shadow/holdout before automation
  └─ [★★★] restart/orphaned phase

[+] Playwright fixture -> broker -> context           [+] Faster browser iteration
  ├─ [★★★] real project, two CLI invocations            ├─ [★★★] impacted run then full merge gate
  ├─ [★★★] context/auth/session isolation               ├─ [★★★] broker-down native fallback
  ├─ [★★★] denied origin/DNS rebinding                  └─ [★★★] speed + escape metrics together
  └─ [★★★] crash removes decrypted auth files

[+] Relay/API/RLS/outbox/stream                       [+] Phone/web monitoring
  ├─ [★★★] host/repository substitution                 ├─ [★★★] reconnect without gaps
  ├─ [★★★] all cursor domains interleaved               ├─ [★★★] loading/stale/resync UI
  ├─ [★★★] one-use browser stream ticket                └─ [★★★] tenant-safe list/detail routes
  └─ [★★★] API + PostgreSQL RLS paths

[+] Device action -> cloud -> daemon                  [+] Remote approve/reject
  ├─ [★★★] iOS key / WebAuthn proof                     ├─ [★★★] exact target/effect/risk/expiry
  ├─ [★★★] cloud key rotation/revocation                ├─ [★★★] double tap executes once
  ├─ [★★★] stale state version/hash                     └─ [★★★] revoked/expired explains failure
  └─ [★★★] dual signature + local policy

[+] Signed failure -> sandbox -> candidates -> PR     [+] Auto-heal staging pipeline
  ├─ [★★★] forged/replayed intake                       ├─ [★★★] exact failure reproduced
  ├─ [★★★] sensitive fixture synthesis                  ├─ [★★★] candidate matrix/proof/rollback
  ├─ [★★★] hostile log prompt injection                 ├─ [★★★] publication approval expires
  ├─ [★★★] no socket/network/credential escape          └─ [★★★] draft only; never merge/deploy
  └─ [★★★] base advance/branch collision/lost response

COVERAGE TARGET: every branch above has a named unit/integration/E2E/security/chaos test.
QUALITY TARGET: ★★★ behavior + edge + failure path for every production boundary.
EVALS: judge and visual-critic structured-output quality fixtures; models never decide deterministic
      route, regression verdict, repair winner, authorization, or publication.
```

The standalone gstack QA test-plan artifact was not separately emitted under
`~/.gstack/projects/...`. The complete test map is embedded here and in each executable plan, so
this does not remove implementation or verification coverage.

### Performance review — 6 issues found and folded

1. Event dispatch, relay, WebSocket, workflow, and browser request queues are bounded and expose
   backpressure rather than accepting unbounded memory growth.
2. Event/change tables have domain-specific sequence and tenant/repository/session indexes; query
   plans and high-cardinality tests are part of cloud load gates.
3. Context reconciliation is incremental and bounded; overflow triggers one explicit current-state
   scan without pretending historical completeness.
4. Judge calls use incident-fingerprint TTL/LRU single-flight caching; unknown cost is never zero.
5. Playwright compares warm cross-invocation benefit to native worker reuse over at least thirty
   samples and ships acceleration disabled if the result is not material.
6. Artifact upload/download, verification output, screenshots/traces, repair fixtures, and list APIs
   have byte/count/page limits plus streaming or content-addressed storage.

### Failure-mode registry after plan fixes

| Boundary | Production failure | Recovery and user-visible result | Planned proof |
|---|---|---|---|
| Local append/dispatch | Crash after commit before ack | Replay dispatch marker; same positions/decision, no duplicate side effect | daemon crash integration |
| Local key/store | Wrong/missing key or corrupt WAL | Named startup failure; never create a blank replacement DB | crypto/recovery tests |
| Hook | Daemon timeout | Observed fail-open or policy fail-closed with diagnostic status | latency/fail-open tests |
| Attached proof | LoopGuard attaches after first edit | Checks may run, verdict remains explicitly unbaselined/inconclusive | intake-order E2E |
| Worktree | Partial branch/worktree creation | Recover/quarantine state; never delete dirty work | Git-backed state tests |
| Managed process | Adapter exits mid-turn | Persist recovering/orphaned; no duplicate turn | fake-process restart tests |
| Browser | Broker crashes with decrypted state | Context lost, plaintext swept, native fallback available | crash and directory scan |
| Relay | Server commits then connection drops | Replay same local positions; cloud event idempotency | reconnect/duplicate test |
| Client stream | Notification loss/slow client | Fill from PostgreSQL; close with resumable position | stream gap/backpressure test |
| Remote action | Device/cloud key revoked | Fail closed before execution; explicit reason and audit | rotation/revocation matrix |
| Artifact | Multipart ETag or missing object | Verify SHA-256/recompute; proof reports unavailable, not success | object-store fault tests |
| Repair | Hostile log requests secret/network | Data remains inert; sandbox/tool policy rejects attempt | injection/escape fixtures |
| GitHub | Push/PR succeeds but response is lost | Reconcile branch/PR by repair/patch identity | publication recovery test |
| Region | Primary unavailable | Human-authorized writer fencing and rehearsed DR promotion | staging failover exercise |

No listed boundary remains silent with neither error handling nor a planned test.

### Distribution and operations

- Python wheel/sdist, daemon user service, Node bridge/broker/fixture packages, web container,
  native iOS archive, service/worker containers, Terraform/Helm, and release manifest all have
  explicit build/verification paths.
- The final release manifest references the real package output paths and verifies checksum, SBOM,
  provenance, and signature for every artifact.
- Multi-AZ is not called regional failover; the hardening plan now includes a separate DR region
  and failover/failback rehearsal.
- Billing, enterprise federation/SCIM, consent-bound support access, and human GA sign-offs are
  explicit rather than implied by “production ready.”

### Worktree and execution strategy

The architecture supports parallel teams, but the supplied one-agent implementation prompt remains
sequential on the user-selected branch:

| Lane | Modules | Depends on |
|---|---|---|
| A — local runtime | `loopguard/control`, adapters, context, verify, router, preferences, browser | Foundation contracts |
| B — hosted core | `services/control-api`, migrations, relay, actions, artifacts | Foundation + adapter event contract |
| C — repair | `loopguard/heal`, repair workflows, GitHub publication | Verification + routing + hosted workflow/storage |
| D — clients | `apps/web`, `apps/ios`, contracts | Complete cloud/repair OpenAPI |
| E — hardening | security, infra, release, operations | Owning controls in A-D |

For the friend's top-to-bottom run: A -> B -> C -> D -> E, task commits only, cumulative gates after
each plan, and one draft PR. If a larger team later uses parallel worktrees, only A/B may overlap
after the canonical foundation; contracts/migrations are merge gates before C/D, and E audits every
lane. No two worktrees edit the same migration, OpenAPI contract, `pyproject.toml`, or progress file
concurrently.

### Engineering NOT in scope

- Automatic merge/deploy of repair PRs.
- Production infrastructure apply, regional failover, billing activation, real hook/App/GitHub
  installation, TestFlight/App Store publication, or human sign-off by an implementation agent.
- Hidden chain-of-thought capture or transcript mirroring.
- Claiming unsupported vendor controls or Windows/iOS capabilities before their platform tests pass.
- A custom model-learning router before deterministic shadow/holdout evidence.

### Engineering implementation tasks

These are review findings mapped into the 88 numbered subsystem tasks. Do not execute or commit
them as additional tasks; their checkboxes close only when their owning numbered work is verified.

- [ ] **ENG-T1 (P1, human: ~2d / CC: ~3h)** — Foundation — Freeze canonical contracts and build
  secure persist/dispatch/ack with restart replay.
  - Files: `loopguard/src/loopguard/control/`, `loopguard/tests/control/`
  - Verify: control matrix on Linux/macOS/Windows plus hook-to-guard E2E.
- [ ] **ENG-T2 (P1, human: ~2d / CC: ~3h)** — Proof — Enforce task intake, trusted commands,
  pre-mutation baseline, signed artifacts, and recovery states.
  - Files: `loopguard/src/loopguard/verify/`, `loopguard/tests/verify/`
  - Verify: baseline ordering, malicious repository, isolation, artifact, and Stop-hook E2E.
- [ ] **ENG-T3 (P1, human: ~1d / CC: ~2h)** — Context — Separate observation/content identity,
  bind MCP capabilities, and make worktree/reconciliation recovery honest.
  - Files: `loopguard/src/loopguard/context/`, `loopguard/tests/context/`
  - Verify: repeated transitions, spoofing, overflow, partial allocation, attached collision tests.
- [ ] **ENG-T4 (P1, human: ~2d / CC: ~3h)** — Cloud actions — Add RLS, repository-bound relay/hook
  ingest, device/WebAuthn proof, cloud key rotation, and complete client APIs.
  - Files: `services/control-api/`, `contracts/`
  - Verify: two-tenant direct-query matrix, action crypto matrix, OpenAPI endpoint ownership test.
- [ ] **ENG-T5 (P1, human: ~1d / CC: ~2h)** — Evidence — Use content-addressed signed artifacts,
  real object checksums, retention/tombstones, and recovery.
  - Files: `loopguard/src/loopguard/verify/`, `services/control-api/src/loopguard_api/artifacts.py`
  - Verify: tamper, partial write/upload, wrong key, retention, deletion authorization tests.
- [ ] **ENG-T6 (P1, human: ~2d / CC: ~3h)** — Auto-Heal — Authenticate intake, isolate hostile
  candidate work, synthesize sensitive fixtures, and make draft publication idempotent.
  - Files: `loopguard/src/loopguard/heal/`, `services/control-api/src/loopguard_api/repair_workflow.py`
  - Verify: injection/escape, coordinate privacy, base advance, lost response, bounded timeout E2E.
- [ ] **ENG-T7 (P1, human: ~1d / CC: ~2h)** — Playwright — Ship the real fixture, secure broker,
  crash cleanup, and native benchmark.
  - Files: `loopguard/integrations/browser-broker/`,
    `loopguard/integrations/playwright-loopguard/`, `loopguard/src/loopguard/browser/`
  - Verify: real-project isolation/fallback and 30-sample native comparison.
- [ ] **ENG-T8 (P1, human: ~2d / CC: ~3h)** — Production — Add release manifest, DR region,
  billing reconciliation, enterprise identity, support boundaries, and human authority stops.
  - Files: `infra/`, `services/control-api/`, `.github/workflows/`, `docs/operations/`
  - Verify: release, restore, failover, billing, SCIM, support, and readiness evidence gates.

No engineering debt is deferred to `TODOS.md`; all findings are in the full production program.

### Engineering dual voices

```text
CLAUDE SUBAGENT (engineering): unavailable — Claude CLI has no current authentication
CODEX SAYS (engineering): unavailable — managed policy rejected exporting private plan contents
Fallback: full repository-grounded primary review
Cross-model consensus: not claimed
```

| Engineering review dimension | Codex voice | Claude voice | Consensus |
|---|---|---|---|
| Architecture and contracts | unavailable | unavailable | not established |
| Code quality and ownership | unavailable | unavailable | not established |
| Test graph and edge cases | unavailable | unavailable | not established |
| Performance and backpressure | unavailable | unavailable | not established |
| Failure handling and recovery | unavailable | unavailable | not established |
| Distribution and operations | unavailable | unavailable | not established |

### Phase 3 completion summary

```text
Mode: FULL_REVIEW; user-confirmed full scope retained
Architecture issues: 12 found, 12 folded into owning plans
Code-quality issues: 8 found, 8 folded
Test review: end-to-end diagram produced; all identified boundary gaps now have named tests
Performance issues: 6 found, 6 folded
Critical silent failure modes remaining in plans: 0
TODOS proposed: 0; all work is in scope
Execution: one integration branch + one draft PR, with task commits/cumulative gates
Outside voices: unavailable due Claude authentication and external-export policy; cross-model consensus not claimed
External gstack test artifact: not separately emitted; full test map remains in this plan
Unresolved engineering decisions: 0
```

## GSTACK AUTOPLAN REVIEW — PHASE 3.5: DEVELOPER EXPERIENCE

### Product classification and review mode

- **Primary product type:** CLI tool and local developer platform.
- **Secondary types:** hosted API/service, Python library, generated client contract, and
  infrastructure platform.
- **Mode:** DX EXPANSION. This is a new production developer tool, so install, first value, real
  integration, debugging, upgrade, distribution, and community all belong in the plan.

### Target developer persona

```text
TARGET DEVELOPER PERSONA
========================
Who:       An AI-heavy product engineer already using Codex and/or Claude Code locally.
Context:   They have seen an agent loop, overwrite work, or claim success without enough proof.
Tolerance: Under 2 minutes to see value; under 5 minutes to protect a real session.
Expects:   One command, no model key for core value, explicit permission changes, local-first
           privacy, exact status, reversible install, and a path to team/cloud policy later.

SECONDARY PERSONA
=================
Who:       A platform/security engineer evaluating organization-wide rollout.
Expects:   Non-interactive setup, capability truth, RLS/audit/retention, SSO/SCIM, predictable
           upgrades, deployment evidence, support boundaries, and no surprise data transfer.
```

### Developer perspective before the fixes

I open the root README because that is where GitHub sends me. It calls LoopGuard a circuit breaker,
then presents an engine, a FastAPI “cloud server,” and an Expo mobile app. The first quickstart asks
me to create a virtual environment, install the server plus Cerebras, write a provider key to
`.env`, run a deliberately biased demo, then start two development servers. I can prove the
hackathon demo works, but I still do not know how LoopGuard attaches to the Codex or Claude session
I use every day.

I follow the package README and find a better offline demo, but the production journey is still
missing: there is no one-shot setup, daemon installer, hook preview, trust-status explanation,
“protected” signal, safe uninstall, migration command, or current capability table. If the daemon
is unreachable I may get a failing command or raw exception, but not a stable code telling me what
happened and the exact recovery command. By minute five I understand the idea and can run a demo;
by minute ten I still cannot get the actual product behavior promised by the roadmap. As an
engineer I now worry that setup will edit my agent configuration invisibly, and I stop before
trusting it with real repositories.

### Competitive DX benchmark

Times below are estimates from the documented golden paths, not vendor-measured activation data.

| Tool | Estimated first value | Notable DX choice | Source |
|---|---:|---|---|
| Claude Code CLI | 3–5 min | One native install command, login on first run, then launch in an existing project | [official quickstart](https://code.claude.com/docs/en/quickstart) |
| AgentOps | 3–5 min | Install, add two lines plus API key, run, then receive a direct session link | [official quickstart](https://docs.agentops.ai/v1/quickstart) |
| LangSmith with LangChain | 3–5 min | Existing agents enable tracing with two environment variables and no code change | [official observability quickstart](https://docs.langchain.com/oss/python/langchain/observability) |
| LangSmith generic app | 5–10 min | Install wrappers, configure two keys, run a complete sample, inspect a trace | [official tracing quickstart](https://docs.langchain.com/langsmith/observability-quickstart) |
| LoopGuard before DX review | >10 min / unavailable | Demo succeeds, but no documented path to a protected real Codex/Claude session | current README and plans |
| LoopGuard target | <2 min demo; <5 min protected | Zero-key real daemon/guard quickstart, then one guided attach command with explicit trust | updated plans |

### Magical moment specification

Delivery vehicle: **one copy-paste terminal command**.

```bash
uvx loopguard quickstart
```

Before registry publication, the equivalent installed-package command is `loopguard quickstart`.
It starts an isolated temporary real daemon, sends hook-shaped repeated events through the secure
protocol and encrypted store into the existing detector, prints the intervention before another
paid turn, proves no model/key was used, cleans up, and ends with one next command:

```text
Loop detected before another paid turn.
No model or API key used.
Next: loopguard setup --agent auto
```

This is not a canned output path; its tests use the same store, dispatcher, guard, decision, and
diagnostic contracts as production.

### First-time developer confusion report

```text
FIRST-TIME DEVELOPER REPORT
============================
Persona: AI-heavy product engineer
Attempting: Protect an existing Codex or Claude session

T+0:00  Opens README; sees engine/server/mobile prototype rather than one production path.
T+0:45  Reaches Quickstart; is asked for a Cerebras key before seeing local-first product value.
T+2:00  Runs demo successfully but cannot tell whether ordinary Codex/Claude is now protected.
T+4:00  Searches for daemon/hook install, trust, doctor, uninstall, and data-location commands.
T+7:00  Finds architecture plans but no copy-paste current-product onboarding or expected output.
T+10:00 Stops. The concept worked; the product integration remains uncertain.
```

Every point is addressed in the plan: production-first README, zero-key quickstart, guided setup,
explicit trust status, `sessions`/doctor confirmation, config/data location, safe uninstall/purge,
and versioned docs.

### Developer journey after plan fixes

| Stage | Developer does | Former friction | Planned status |
|---|---|---|---|
| Discover | Reads production-first README and capability table | Hackathon/prototype led the story | Fixed in Foundation Task 6 |
| Install | Uses `uvx`/`pipx` after registry verification | Editable clone install only | Fixed in release plan; blocked on owner registry/legal authority |
| Hello world | Runs `loopguard quickstart` | Provider key and multiple processes | Fixed; zero key, isolated, <2 min target |
| Real usage | Runs `loopguard setup --agent auto`, reviews trust, starts normal agent | Manual service/hook steps and no protected signal | Fixed in Integrations Task 7 |
| Debug | Runs `doctor --fix-safe`, `explain`, `config show`, or creates redacted bundle | Raw/missing errors and unclear recovery | Fixed through structured error contract |
| Upgrade | Runs signed update/migration check with preview/backup | No changelog, deprecation, or migration path | Fixed in Hardening Task 2 |
| Scale | Adds cloud pairing/policy/SSO/SCIM through capability-aware docs | Local demo to enterprise cliff | Fixed through progressive platform docs |
| Leave | Runs dry-run uninstall; data retained unless explicit purge | Ownership/removal unclear | Fixed; reversible by default |

### Pass 1 — Getting started: 2/10 → 10/10

The current root quickstart requires a provider key and demonstrates the old demo architecture.
The updated plan creates a three-step golden path:

```text
1. Install or run package          target: <=30 s after cached package metadata
2. loopguard quickstart            target: p95 <=90 s, no key/account/permanent writes
3. loopguard setup --agent auto    target: protected session <=5 min including explicit trust
```

Each step has expected output, stable machine JSON, local timing, and an exact next action.

### Pass 2 — API/CLI/SDK design: 5/10 → 9/10

The core library types are already small and explicit. The production CLI now has one noun/verb
grammar across `quickstart`, `setup`, `sessions`, `integrations`, `doctor`, `config`, `router`,
`update`, `migrate`, `uninstall`, `data`, `dx`, `feedback`, and `explain`. Human output is concise;
`--json` is stable for automation; `--dry-run` precedes mutations; IDs and cursor domains are
self-describing; capability-unavailable is a normal result.

The API endpoint/schema ownership matrix prevents clients from dropping to ad hoc HTTP. A final
point remains intentionally platform-specific: public SDKs beyond the Python library and generated
web/iOS clients are not invented before demand.

### Pass 3 — Errors and debugging: 4/10 → 10/10

Three representative paths:

| Path | Before | Planned output |
|---|---|---|
| Missing daemon | `{"daemon":"unreachable"}` or connection exception | `LGD-DAEMON-001`, cause (service/socket), exact status/start/setup commands, docs URL, safe diagnostics |
| Hook trust pending | Installed file may be mistaken for working protection | `LGD-INTEGRATION-TRUST-001`, exact file hash/scope, `/hooks` review step, status `attention_required` |
| Stale remote action | Typed code existed but contract varied by client | RFC 9457 `stale_state` with request ID, reviewed/current state hashes, retryable=false, fresh-review action |

Every error provides what happened, likely cause, safe fix, actual non-secret values, request ID
where applicable, and a local/public reference. Tracebacks require `--verbose` and are redacted.

### Pass 4 — Documentation and learning: 3/10 → 9/10

The plan now treats docs as a release artifact:

- Quickstart, Tutorials, How-to, Reference, Concepts, Security/Privacy, Operations, Migration, and
  Error Code sections.
- Generated CLI and OpenAPI reference with CI drift checks.
- Runnable code/command examples with expected output.
- Public version-labelled routes, full-text search, stable anchors, source/edit links, and direct
  links from CLI/API errors and dashboard states.
- Prototype/live-provider docs remain available but clearly secondary.

The score stops at 9 because the actual information-retrieval time must be measured after the docs
site exists.

### Pass 5 — Upgrade and migration: 2/10 → 10/10

Signed update manifests, semantic/package and explicit protocol/API/schema versions, changelog,
deprecation schedule, migration guides, read-only checks, preview, encrypted backup, resumable
apply, compatibility matrix, restore instructions, and fixture migrations are now in the
hardening plan. No agent silently updates vendor trust or reinterprets data.

### Pass 6 — Environment and tooling: 5/10 → 9/10

Plans now cover macOS/Linux/Windows, current-user service managers, named pipes/sockets, ARM/x86
container builds, deterministic Node lockfiles, Python `all-dev`, dry-run/non-interactive/JSON
paths, local fakes, generated types, test helpers, CI matrices, and no-credential offline suites.
The score remains 9 until real platform installation tests establish measured success rates.

### Pass 7 — Community and ecosystem: 1/10 → 8/10

The hardening plan adds contribution/security/conduct docs, issue forms, PR template, support
expectations, transparent pricing/limits, source/edit links, and a documented extension boundary
through adapters/plugins. Public distribution is correctly blocked until the owner supplies
approved legal terms and reserves registry names. An implementation agent cannot choose either.

### Pass 8 — Measurement and feedback: 0/10 → 9/10

Quickstart/setup record privacy-safe local step timing and outcome codes. `loopguard dx report
--local` exposes time-to-magic and time-to-protected-session; upload is opt-in. Issue/support flows
can include a consented, redaction-previewed doctor bundle. Product metrics track install/setup
completion, trust-pending drop-off, integration health, verification value, and cost claims without
repository/prompts/path telemetry. A post-implementation `/devex-review` must measure reality.

### DX scorecard

```text
+====================================================================+
|              DX PLAN REVIEW — SCORECARD                            |
+====================================================================+
| Dimension            | Before | After                              |
|----------------------|--------|------------------------------------|
| Getting Started      | 2/10   | 10/10                              |
| API/CLI/SDK          | 5/10   | 9/10                               |
| Error Messages       | 4/10   | 10/10                              |
| Documentation        | 3/10   | 9/10                               |
| Upgrade Path         | 2/10   | 10/10                              |
| Dev Environment      | 5/10   | 9/10                               |
| Community            | 1/10   | 8/10                               |
| DX Measurement       | 0/10   | 9/10                               |
+--------------------------------------------------------------------+
| TTHW                 | >10 min / product path absent -> <2 min demo |
| Protected session    | absent -> <5 min target                      |
| Competitive Rank     | Red Flag -> Champion demo / Competitive real|
| Magical Moment       | designed via copy-paste real offline command|
| Product Type         | CLI + local platform + hosted API           |
| Mode                 | DX EXPANSION                                |
| Overall DX           | 3/10 -> 9/10 at plan level                 |
+====================================================================+
```

### DX implementation checklist

- [ ] Time to offline magical moment p95 <2 minutes.
- [ ] Time to protected existing session p95 <5 minutes.
- [ ] Install/run is one verified registry command after owner approval.
- [ ] First run uses the real daemon/store/dispatcher/guard and no API key.
- [ ] Every error has problem + cause + fix + code/docs link.
- [ ] CLI/API naming, dry-run, JSON, and idempotency are consistent.
- [ ] Docs examples execute and generated reference cannot drift.
- [ ] Real local, managed, cloud, browser, and repair tutorials exist.
- [ ] Update/migration/deprecation/changelog policy is tested.
- [ ] Cross-platform install/doctor/uninstall tests run in CI.
- [ ] Hosted pricing/limits and data behavior are transparent.
- [ ] Public distribution waits for legal/registry owner approval.
- [ ] Setup friction is measured locally and telemetry remains opt-in.

### DX NOT in scope

- Requiring a hosted playground before local-first value; the real offline quickstart is the
  lower-friction, privacy-consistent vehicle.
- Public SDKs for every language before demand; Python plus generated web/iOS clients cover the
  approved first product.
- A generic chat/terminal replacement or hidden automatic vendor-trust approval.
- Publishing packages, choosing legal terms, creating public support communities, or promising
  response SLAs without owner/human authority.

### What already exists for DX

- A genuinely offline demo and clear small Python `LoopGuard.observe()` example.
- Rich terminal output, Typer CLI, demo scenarios, provider-specific missing-dependency guidance,
  and current architecture/demo docs.
- Current tests and fakes that can be extended into a zero-key product quickstart.
- Official vendor capability/trust surfaces and a current prototype users can inspect.

### DX implementation tasks

These are review findings mapped into the 88 numbered subsystem tasks. Do not execute or commit
them as additional tasks; their checkboxes close only when their owning numbered work is verified.

- [ ] **DX-T1 (P1, human: ~1d / CC: ~2h)** — Quickstart — Build the real zero-key temporary
  daemon/guard walkthrough and production-first README.
  - Files: `loopguard/src/loopguard/quickstart.py`, `README.md`, `loopguard/README.md`,
    `docs/getting-started/quickstart.md`
  - Verify: clean environment, no keys/network/permanent writes, p95 timing.
- [ ] **DX-T2 (P1, human: ~1d / CC: ~2h)** — Setup — Build idempotent detected-agent setup,
  explicit trust, safe doctor fixes, uninstall preview, and protected-session confirmation.
  - Files: `loopguard/src/loopguard/adapters/setup.py`, CLI, integration docs/tests.
  - Verify: install/repeat/partial failure/resume/uninstall on macOS/Linux/Windows.
- [ ] **DX-T3 (P1, human: ~6h / CC: ~45min)** — Errors — Standardize CLI/daemon/API problems and
  local explanations.
  - Files: `control/errors.py`, `loopguard_api/errors.py`, `docs/reference/errors.md`
  - Verify: golden snapshots for six local and all public API error codes.
- [ ] **DX-T4 (P1, human: ~1d / CC: ~2h)** — Docs — Ship versioned searchable docs, generated
  CLI/OpenAPI reference, and executable examples.
  - Files: `docs/`, `apps/web/src/app/(docs)/`, docs E2E/CI.
  - Verify: link/snippet/reference/search tests.
- [ ] **DX-T5 (P1, human: ~1d / CC: ~1h)** — Upgrade — Add signed update checks, migration
  preview/backup/apply/restore, changelog, versioning, and deprecation.
  - Files: `loopguard/src/loopguard/security/`, `CHANGELOG.md`, `docs/migrations/`
  - Verify: every supported schema fixture upgrades and failure restores safely.
- [ ] **DX-T6 (P2, human: ~6h / CC: ~45min)** — Community/legal — Add contribution, security,
  issue, support, pricing, and release-ownership gates.
  - Files: root community files, issue templates, `docs/product/pricing-and-limits.md`
  - Verify: template/docs lint; package publishing blocked without owner legal/registry metadata.
- [ ] **DX-T7 (P2, human: ~4h / CC: ~30min)** — Measurement — Record local opt-in-safe onboarding
  timings, report friction, and support redacted diagnostic feedback.
  - Files: setup/quickstart telemetry, `loopguard dx`, doctor bundle, privacy docs.
  - Verify: no paths/repos/prompts/identifiers and no upload before opt-in.

No DX debt is silently deferred to `TODOS.md`. Legal terms, registry ownership, production support
SLAs, and final community channels are named external owner gates, not decisions an implementation
agent may make.

### DX dual voices

```text
CLAUDE SUBAGENT (DX): unavailable — Claude CLI has no current authentication
CODEX SAYS (DX): unavailable — managed policy rejected exporting private plan contents
Fallback: full repository-grounded primary review plus current official competitor quickstarts
Cross-model consensus: not claimed
```

| DX review dimension | Codex voice | Claude voice | Consensus |
|---|---|---|---|
| Getting started and time to value | unavailable | unavailable | not established |
| API/CLI/SDK consistency | unavailable | unavailable | not established |
| Errors and documentation | unavailable | unavailable | not established |
| Upgrade and environment | unavailable | unavailable | not established |
| Community and distribution | unavailable | unavailable | not established |
| Measurement and feedback | unavailable | unavailable | not established |

### Phase 3.5 completion summary

```text
Mode: DX EXPANSION
Persona: AI-heavy local Codex/Claude developer; platform evaluator secondary
Product type: CLI/local platform + hosted API
Initial score: 3/10
Plan-level score after fixes: 9/10
TTHW: current production path absent/>10 min; target <2 min offline, <5 min protected session
Magical moment: real zero-key `loopguard quickstart`
All 8 passes completed
TODOs deferred: 0
External owner gates: legal terms, registry ownership, support SLA/community, real publication
Outside voices: unavailable due Claude authentication and external-export policy; cross-model consensus not claimed
Unresolved DX implementation decisions: 0
```

## Cross-phase reconciliation

Five themes now bind every plan:

1. **Proof is the product spine.** Loop detection, context, routing, preferences, browser feedback,
   remote actions, and repair all feed a baseline-aware evidence record. No subsystem invents its
   own meaning of “verified.”
2. **Capability truth beats feature theatre.** Attached, managed, local, and cloud surfaces report
   exactly what the current vendor API permits. Partial hook coverage is visible; unsupported
   model, effort, interrupt, inject, or cloud behavior is never simulated.
3. **Security precedes data and action.** Peer identity, frame limits, encryption, repository
   trust, RLS, request proof, hostile-input isolation, and sandbox boundaries live in the first
   task that needs them. The last plan audits these controls instead of introducing them late.
4. **Identity and ordering are explicit.** Observation IDs, content fingerprints, state
   version/hash, typed action targets, and all five cursor domains remain distinct across storage,
   APIs, clients, replay, and recovery.
5. **Low-friction onboarding cannot weaken trust.** Quickstart is zero-key and temporary; real
   setup previews mutations, preserves vendor trust prompts, exposes partial capability, supports
   dry-run/uninstall, and measures time-to-value without uploading repository data.

Current vendor constraints checked during reconciliation:

- Codex hooks are a documented integration surface, but current `PreToolUse` interception is not a
  complete enforcement boundary. App Server remains the deep managed-control surface.
  See [Codex hooks](https://learn.chatgpt.com/docs/hooks) and
  [App Server](https://learn.chatgpt.com/docs/app-server). Plugin hooks are primary; direct hook
  file merge is a mutually exclusive compatibility fallback.
- Codex App Server requires stable `clientInfo`; `turn/steer` requires the active
  `expectedTurnId`, while `thread/inject_items` is the between-turn history surface. Enterprise
  Compliance Logs support also requires LoopGuard to become a known client before launch claims.
- Codex already offers native phone remote control, so LoopGuard's phone/web value is
  provider-neutral proof, policy, and safe actions rather than another generic terminal remote.
  See [Codex remote connections](https://learn.chatgpt.com/docs/remote-connections).
- Claude project hooks run in local and cloud sessions, while managed Agent SDK products use
  documented API/provider credentials. LoopGuard must not proxy `claude.ai` login or rate limits
  without Anthropic approval. See [Claude web sessions](https://code.claude.com/docs/en/claude-code-on-the-web)
  and [Agent SDK quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart).

### Aggregated implementation index

This index deduplicates the review work; it does not add to or replace the 88 numbered tasks in the
eleven executable plans.

| Order | Aggregate outcome | Owning executable work | Gate |
|---:|---|---|---|
| 1 | Freeze canonical event, decision, action, state, cursor, error, and capability contracts | Foundation Tasks 1–2 | Cross-plan contract fixtures pass |
| 2 | Build encrypted durable local composition path and secure daemon protocol | Foundation Tasks 3–6 | Persist -> dispatch -> guard -> ack survives restart |
| 3 | Deliver zero-key quickstart, lifecycle CLI, diagnostics, and structured local errors | Foundation Task 6 | p95 quickstart <2 min, no key/network/permanent state |
| 4 | Attach Codex/Claude without overwriting config or bypassing trust | Integrations Tasks 1–4 | Idempotent install/verify/uninstall; exact coverage shown |
| 5 | Require proof intake and clean pre-mutation baseline | Verification Tasks 1–3 | Late attach cannot produce `verified` |
| 6 | Run trusted checks safely and persist signed immutable evidence | Verification Tasks 4–7 | No-index fallback and prompt-to-proof E2E pass |
| 7 | Journal every repository actor and coordinate isolated concurrent work | Context Tasks 1–7 | Cursor/provenance/collision/worktree recovery tests pass |
| 8 | Add managed Codex/Claude lifecycle with honest credentials and recovery | Integrations Tasks 5–8 | Start/stream/inject/interrupt/recover contract suite passes |
| 9 | Route model/effort deterministically within observed budgets | Routing Tasks 1–7 | Shadow/holdout report separates observed and estimated cost |
| 10 | Compile editable preference layers and isolate optional visual criticism | Preference Tasks 1–6 | Hard/soft scope and hostile-input tests pass |
| 11 | Integrate real Playwright fixtures, isolation, and native-baseline benchmarks | Browser Tasks 1–7 | No cross-session auth leak; complete PR gate remains |
| 12 | Build tenant-safe hosted ingest, relay, replay, action, artifact, and audit APIs | Cloud Tasks 1–9 | RLS, signature, replay, reconnect, and retention tests pass |
| 13 | Ship independently packageable core web/iOS clients with capability-aware navigation | Surfaces Tasks 1–11 | No dead repair UI; core API, replay, action, visual, a11y, package gates pass |
| 14 | Reproduce hostile pipeline failures and rank bounded candidates | Auto-Heal Tasks 1–6 | No candidate proceeds without reproduction/evidence |
| 15 | Publish only authorized, verified draft repair PRs with durable recovery | Auto-Heal Tasks 7–9 | No merge/deploy; lost-response/base-advance cases reconcile |
| 16 | Activate complete repair destinations only after workflow capability is ready | Surfaces Task 12 | Web/iOS repair parity and final visual/a11y gates pass |
| 17 | Prove production security, recovery, observability, DR, billing, and provenance | Hardening Tasks 1–10 | Human production-readiness sign-offs receive complete evidence |
| 18 | Ship production-first docs, migration/changelog, support, and legal gates | Foundation/Integrations/Hardening DX additions | Examples execute; public release remains owner-authorized |
| 19 | Maintain one branch and one draft PR without losing reviewability | Implementation prompt progress/task/milestone rules | 88 task commits, cumulative gates, clean milestone checkpoints |

## GSTACK REVIEW REPORT

| Review | Runs | Status | Findings and resolution |
|---|---:|---|---|
| CEO / scope | 2 | CLEAR | 2026-07-14 refresh moved proof before context, core clients before Auto-Heal, added go/no-go thresholds, and preserved full scope |
| Independent voices | 5 completed, external cross-model unavailable | DEGRADED | Same-host CEO/design/engineering/DX voices completed; external Claude authentication remains unavailable, so cross-model consensus is not claimed |
| Product design | 2 | CLEAR | Capability-aware IA, host/integration first run, multi-hop action states, core packaging, and repair activation were folded into the surface/cloud plans |
| Engineering | 2 | CLEAR | Plugin-first adapters, current app-server/Agent SDK semantics, proof fallback, capability trust, ownership, and bootstrap boundaries have named tests |
| Developer experience | 2 | CLEAR | Host-neutral workflows, absolute virtualenv commands, resumability, harmless metadata handling, Xcode fallback, and product evidence gates are specified |
| Final reconciliation | 2 | CLEAR | 11 executable plans, 88 sequential tasks, links, code fences, cursor domains, branch/PR policy, vendor constraints, and implementation prompt were reconciled |

External evidence limitations:

- The local gstack review dashboard now contains CEO, design, engineering, DX, and voice-status
  entries. A separate external QA artifact was not emitted; its full test map remains embedded in
  this master and the executable plans.
- Design mockups were not generated because the configured designer credential was unavailable.
  The implementation plan includes an executable design contract and post-build visual gates.
- Legal terms, registry ownership, real package/app publication, production credentials,
  infrastructure apply, failover, billing activation, and final organizational sign-offs remain
  explicit human authority gates. They are not unresolved implementation choices.

**VERDICT:** CEO, DESIGN, ENGINEERING, AND DX ARE CLEARED AT PLAN LEVEL. The complete roadmap is
ready for top-to-bottom implementation on `feat/loopguard-production` using one draft PR, task
commits, and cumulative milestone gates. Cross-model confidence is degraded, not silently
represented as consensus.

NO UNRESOLVED DECISIONS
