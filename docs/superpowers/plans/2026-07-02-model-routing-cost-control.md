# Model Routing and Cost Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select an allowed model and effort at managed-run phase boundaries using deterministic evidence, then measure whether routing reduces total cost without reducing verified success.

**Architecture:** Adapters report surface capabilities and available models. A zero-token feature extractor builds a `TaskProfile`; a versioned rule policy ranks candidates under quality, latency, and cost constraints. Shadow routing records decisions before automatic routing is enabled.

**Tech Stack:** Python 3.11+, Pydantic 2, TOML, SQLite, pytest

All illustrative helpers (`repo_snapshot`, `catalog`, `profile`, `result`, managed-run fixtures)
must be implemented in `loopguard/tests/router/conftest.py` or an explicitly listed integration
support module.

---

### Task 1: Define provider-neutral model catalog types

**Files:**
- Create: `loopguard/src/loopguard/router/__init__.py`
- Create: `loopguard/src/loopguard/router/catalog.py`
- Create: `loopguard/tests/router/conftest.py`
- Test: `loopguard/tests/router/test_catalog.py`

- [ ] **Step 1: Write failing availability tests**

```python
from decimal import Decimal

from loopguard.router.catalog import ModelCatalog, ModelSpec


def test_catalog_filters_by_surface_capability_and_budget():
    catalog = ModelCatalog([
        ModelSpec(id="fast", provider="p", surfaces={"managed"}, efforts={"low", "medium"},
                  input_cost_per_million=Decimal("1"),
                  output_cost_per_million=Decimal("2"), source="signed-test-catalog"),
        ModelSpec(id="deep", provider="p", surfaces={"managed"}, efforts={"high"},
                  input_cost_per_million=Decimal("10"),
                  output_cost_per_million=Decimal("20"), source="signed-test-catalog"),
    ])
    result = catalog.eligible(surface="managed", effort="low", max_output_cost_per_million=5)
    assert [model.id for model in result] == ["fast"]
```

- [ ] **Step 2: Verify missing package**

Run: `cd loopguard && python -m pytest -q tests/router/test_catalog.py`
Expected: FAIL because `loopguard.router` does not exist.

- [ ] **Step 3: Implement immutable catalog types**

```python
class ModelSpec(BaseModel, frozen=True):
    id: str
    provider: str
    surfaces: set[str]
    efforts: set[str]
    input_cost_per_million: Decimal | None = None
    output_cost_per_million: Decimal | None = None
    max_context_tokens: int | None = None
    capabilities: set[str] = Field(default_factory=set)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str
```

Do not embed a permanent "latest" model. Load catalog entries from signed product configuration,
provider discovery, or admin policy. Record source, signature/key ID, observed/effective/expiry
times, provider model revision, and capability evidence. Reject expired/invalid signed config and
keep the last known valid catalog with a visible stale status. Unknown prices remain `None`, never
zero, and cannot be used for cost-optimized automatic selection. Use `Decimal` from parse through
accounting and serialization; do not round until display.

- [ ] **Step 4: Run catalog tests**

Run: `cd loopguard && python -m pytest -q tests/router/test_catalog.py`
Expected: PASS.

- [ ] **Step 5: Commit catalog types**

```bash
git add loopguard/src/loopguard/router loopguard/tests/router
git commit -m "feat: add provider-neutral model catalog"
```

### Task 2: Extract deterministic task and trajectory features

**Files:**
- Create: `loopguard/src/loopguard/router/features.py`
- Test: `loopguard/tests/router/test_features.py`

- [ ] **Step 1: Write failing feature tests**

```python
from loopguard.router.features import FeatureExtractor


def test_security_migration_is_high_risk_without_model_call(repo_snapshot):
    profile = FeatureExtractor().extract(
        prompt="Migrate authentication tokens and update the database schema",
        repo=repo_snapshot,
        events=[],
    )
    assert profile.task_type == "migration"
    assert profile.risk == "high"
    assert profile.requires == {"code", "tests", "database"}
    assert profile.extractor == "deterministic-v1"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/router/test_features.py`
Expected: FAIL because `FeatureExtractor` is missing.

- [ ] **Step 3: Implement bounded feature extraction**

Extract prompt length, task keywords, languages, file count, changed-symbol count, dependency depth,
test scope, tool requirements, verification failures, retry count, loop fingerprints, and
sensitivity markers. Define:

```python
class TaskProfile(BaseModel):
    task_type: Literal["search", "edit", "debug", "migration", "review", "ui", "repair"]
    risk: Literal["low", "medium", "high"]
    complexity_score: int = Field(ge=0, le=100)
    requires: set[str]
    context_tokens_estimate: int
    retry_count: int
    extractor: str = "deterministic-v1"
```

Use explicit keyword/config rules and repository metadata; do not call a model.

- [ ] **Step 4: Run feature table tests**

Run: `cd loopguard && python -m pytest -q tests/router/test_features.py`
Expected: PASS for simple search, routine edit, UI work, migration, security-sensitive work,
failed verification, and repeated-loop cases.

- [ ] **Step 5: Commit deterministic task features**

```bash
git add loopguard/src/loopguard/router/features.py loopguard/tests/router/test_features.py
git commit -m "feat: extract zero-token routing features"
```

### Task 3: Implement versioned rule policies

**Files:**
- Create: `loopguard/src/loopguard/router/policy.py`
- Create: `loopguard/src/loopguard/router/default-policy.toml`
- Test: `loopguard/tests/router/test_policy.py`

- [ ] **Step 1: Write the routing decision matrix**

```python
import pytest

from loopguard.router.policy import RouterPolicy


@pytest.mark.parametrize(
    ("task_type", "risk", "phase", "model", "effort"),
    [
        ("search", "low", "implement", "fast", "low"),
        ("edit", "medium", "implement", "standard", "medium"),
        ("migration", "high", "plan", "deep", "high"),
        ("migration", "high", "verify", "standard", "high"),
    ],
)
def test_default_policy(task_type, risk, phase, model, effort, catalog):
    decision = RouterPolicy.default(catalog).route(profile(task_type, risk), phase)
    assert (decision.model_id, decision.effort) == (model, effort)
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/router/test_policy.py`
Expected: FAIL because `RouterPolicy` is missing.

- [ ] **Step 3: Implement explainable ordered rules**

Each TOML rule contains priority, profile predicates, phase, allowed model tags, effort, maximum
unit price, and fallback rule. Return:

```python
class RoutingDecision(BaseModel):
    policy_version: str
    model_id: str
    effort: str
    phase: Literal["plan", "implement", "verify", "repair"]
    matched_rule: str
    considered_models: list[str]
    rejected: dict[str, str]
    automatic: bool
```

Tie-break by policy priority, expected unit cost, then stable model ID. If the surface cannot
select a model, return the current model with `automatic=False` and reason
`surface_capability_unavailable`.

- [ ] **Step 4: Run policy and fallback tests**

Run: `cd loopguard && python -m pytest -q tests/router/test_policy.py`
Expected: PASS.

- [ ] **Step 5: Commit routing policy**

```bash
git add loopguard/src/loopguard/router loopguard/tests/router
git commit -m "feat: route managed phases with deterministic policy"
```

### Task 4: Enforce guard and context budgets

**Files:**
- Create: `loopguard/src/loopguard/router/budgets.py`
- Create: `loopguard/src/loopguard/router/judge_cache.py`
- Modify: `loopguard/src/loopguard/guard.py`
- Modify: `loopguard/src/loopguard/judge.py`
- Modify: `loopguard/src/loopguard/config.py`
- Test: `loopguard/tests/router/test_budgets.py`
- Test: `loopguard/tests/router/test_judge_cache.py`

- [ ] **Step 1: Write failing budget-allocation tests**

```python
from decimal import Decimal

from loopguard.router.budgets import SessionBudget


def test_optional_services_cannot_exceed_guard_overhead():
    budget = SessionBudget(
        total=Decimal("2.00"),
        guard_overhead=Decimal("0.04"),
        judge=Decimal("0.02"),
        context=Decimal("0.01"),
        visual_critic=Decimal("0.01"),
    )
    assert budget.reserve("judge", Decimal("0.015")).allowed
    assert not budget.reserve("judge", Decimal("0.010")).allowed
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/router/test_budgets.py`
Expected: FAIL because `SessionBudget` is missing.

- [ ] **Step 3: Implement atomic budget reservations**

Use `Decimal`, never float, for router accounting. Keep existing hard total-spend behavior in
`LoopGuard`; add a budget callback so judge reservations happen before the call. Context digests
enforce character/token estimates independently. A failed reservation returns a normal typed
decision and never calls the optional service.

Replace run-local detector-key caching with a bounded incident cache keyed by
`(policy_version, detector_kind, normalized_incident_fingerprint, judge_model, prompt_version)`.
The fingerprint contains the normalized repeated behavior and relevant bounded context, not raw
secrets or only the run ID. Cache only validated judge outcomes, encrypt persisted values, apply
short TTL/LRU limits, and invalidate on policy/prompt/model changes. Concurrent identical incidents
use single-flight deduplication. Tests prove identical incidents across runs reuse one result,
different arguments/context do not collide, failures/timeouts are not cached, and budget is
reserved exactly once.

- [ ] **Step 4: Run router and existing budget tests**

Run: `cd loopguard && python -m pytest -q tests/router/test_budgets.py tests/router/test_judge_cache.py tests/test_budget.py tests/test_guard.py`
Expected: PASS.

- [ ] **Step 5: Commit cost ceilings**

```bash
git add loopguard/src/loopguard loopguard/tests/router
git commit -m "feat: enforce optional service cost budgets"
```

### Task 5: Record actual usage and route outcomes

**Files:**
- Create: `loopguard/src/loopguard/router/outcomes.py`
- Create: `loopguard/src/loopguard/router/store.py`
- Test: `loopguard/tests/router/test_outcomes.py`

- [ ] **Step 1: Write failing accounting tests**

```python
from decimal import Decimal

from loopguard.router.outcomes import OutcomeRecorder


def test_total_cost_includes_agent_guard_and_repair(tmp_path):
    recorder = OutcomeRecorder.for_path(tmp_path / "router.db")
    outcome = recorder.record(
        routing_id="route_1",
        verified=True,
        agent_cost=Decimal("0.20"),
        guard_cost=Decimal("0.01"),
        verification_cost=Decimal("0.00"),
        repair_cost=Decimal("0.03"),
        wall_time_ms=1200,
    )
    assert outcome.total_cost == Decimal("0.24")
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/router/test_outcomes.py`
Expected: FAIL because the outcome recorder is missing.

- [ ] **Step 3: Implement reconciled outcome records**

Store selected route, actual model, effort, token usage, provider-reported cost, estimated cost,
verification verdict, regressions, interruptions, repairs, and wall time. Keep reported and
estimated cost separate. Missing token or price data remains `None`/`unknown`, not `0`. A total is
unknown when a required component is unknown; also expose the sum of known components. Never write
an "avoided cost" as observed fact.

- [ ] **Step 4: Run accounting tests**

Run: `cd loopguard && python -m pytest -q tests/router/test_outcomes.py`
Expected: PASS.

- [ ] **Step 5: Commit outcome accounting**

```bash
git add loopguard/src/loopguard/router loopguard/tests/router
git commit -m "feat: record routing cost and quality outcomes"
```

### Task 6: Add shadow mode and holdout evaluation

**Files:**
- Create: `loopguard/src/loopguard/router/evaluation.py`
- Test: `loopguard/tests/router/test_evaluation.py`
- Modify: `loopguard/src/loopguard/control/daemon.py`
- Modify: `loopguard/src/loopguard/cli.py`

- [ ] **Step 1: Write failing stable-assignment tests**

```python
from loopguard.router.evaluation import assign_experiment


def test_holdout_assignment_is_stable_by_repository_and_task():
    first = assign_experiment("repo_1", "task_9", percentage=10)
    second = assign_experiment("repo_1", "task_9", percentage=10)
    assert first == second
    assert first in {"control", "routed"}
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/router/test_evaluation.py`
Expected: FAIL because experiment assignment is missing.

- [ ] **Step 3: Implement shadow and holdout modes**

Shadow mode records what the router would choose but does not alter the run. Holdout mode uses a
stable keyed hash after explicit user/organization opt-in. Reports compare verified success,
regression rate, total observed cost, wall time, and user interventions. Require minimum sample
size and confidence interval display before recommending automatic enablement.

Expose:

```bash
loopguard router evaluate --since 30d --json
loopguard router policy validate .loopguard/router.toml
```

- [ ] **Step 4: Run router tests**

Run: `cd loopguard && python -m pytest -q tests/router`
Expected: PASS.

- [ ] **Step 5: Commit router evaluation**

```bash
git add loopguard/src/loopguard/router loopguard/src/loopguard/control \
  loopguard/src/loopguard/cli.py loopguard/tests/router
git commit -m "feat: evaluate routing in shadow and holdout modes"
```

### Task 7: Apply routing only at managed phase boundaries

**Files:**
- Create: `loopguard/src/loopguard/router/coordinator.py`
- Modify: `loopguard/src/loopguard/adapters/codex_managed.py`
- Modify: `loopguard/src/loopguard/adapters/claude_managed.py`
- Test: `loopguard/tests/integration/test_phase_routing.py`

- [ ] **Step 1: Write failing phase-transition tests**

```python
def test_router_does_not_switch_mid_tool_call(managed_run):
    managed_run.start_phase("implement")
    managed_run.on_tool_call("Bash", {"cmd": "pytest"})
    assert managed_run.adapter.model_changes == [("standard", "medium")]
    managed_run.start_phase("verify")
    assert managed_run.adapter.model_changes == [
        ("standard", "medium"),
        ("standard", "high"),
    ]
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/integration/test_phase_routing.py`
Expected: FAIL because phase coordination is missing.

- [ ] **Step 3: Implement explicit phase state**

Allowed transitions are `plan -> implement -> verify`, with `verify -> implement` only after failed
evidence and a bounded repair count. `repair` is entered only by a repair workflow. Persist every
phase and route before invoking the adapter. Reject model switches during an active tool request.
Register the catalog, feature extractor, budget service, outcome recorder, experiment assignment,
and phase coordinator in `DaemonServices`. Recovery reloads the persisted phase/route and compares
adapter state before another turn; mismatch becomes `orphaned`/human review, not an automatic
duplicate phase.

- [ ] **Step 4: Run integration and router tests**

Run: `cd loopguard && python -m pytest -q tests/router tests/integration/test_phase_routing.py`
Expected: PASS.

- [ ] **Step 5: Commit managed phase routing**

```bash
git add loopguard/src/loopguard/router loopguard/src/loopguard/adapters \
  loopguard/tests/integration
git commit -m "feat: route models at managed phase boundaries"
```

## Completion gate

Run:

```bash
cd loopguard
python -m pytest -q tests/router tests/integration/test_phase_routing.py
ruff check src/loopguard/router tests/router
```

Expected: all commands exit 0; routing invokes no model; unsupported surfaces remain unchanged; and
reports distinguish observed cost from estimated counterfactual savings. Unknown prices/costs stay
unknown, judge calls deduplicate only by validated incident fingerprint, concurrent budget
reservations cannot overspend, and daemon restart cannot repeat a phase transition.
