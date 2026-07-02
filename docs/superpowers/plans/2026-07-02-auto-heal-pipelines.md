# Auto-Healing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce supported staging/CI data-pipeline failures, evaluate isolated candidate fixes, and publish the smallest verified repair as an evidence-rich draft GitHub pull request.

**Architecture:** Failure adapters normalize Airflow, OpenLineage, GitHub Actions, and signed webhook inputs into `FailureEvent`. A durable repair workflow gates every later step on reproduction, generates candidates in separate worktrees/containers, evaluates them with deterministic contracts, and permits only draft-PR publication.

**Tech Stack:** Python 3.11+, Pydantic 2, Docker, Git worktrees, Temporal, Airflow callbacks, OpenLineage, GitHub App API, pytest

---

### Task 1: Define repair state and evidence contracts

**Files:**
- Create: `loopguard/src/loopguard/heal/__init__.py`
- Create: `loopguard/src/loopguard/heal/models.py`
- Test: `loopguard/tests/heal/test_models.py`

- [ ] **Step 1: Write failing state and publication tests**

```python
import pytest

from loopguard.heal.models import RepairRun, RepairState


def test_repair_cannot_evaluate_before_reproduction():
    run = RepairRun.fixture(state=RepairState.INTAKE)
    with pytest.raises(ValueError, match="reproduction required"):
        run.transition(RepairState.EVALUATING)


def test_publication_requires_verified_candidate():
    run = RepairRun.fixture(state=RepairState.RANKED, candidates=[])
    with pytest.raises(ValueError, match="verified candidate required"):
        run.transition(RepairState.PUBLISHING)
```

- [ ] **Step 2: Verify missing package**

Run: `cd loopguard && python -m pytest -q tests/heal/test_models.py`
Expected: FAIL because `loopguard.heal` does not exist.

- [ ] **Step 3: Implement repair contracts**

```python
class RepairState(StrEnum):
    INTAKE = "intake"
    REPRODUCING = "reproducing"
    NOT_REPRODUCIBLE = "not_reproducible"
    PLANNING = "planning"
    GENERATING = "generating"
    EVALUATING = "evaluating"
    RANKED = "ranked"
    AWAITING_PUBLICATION = "awaiting_publication"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureEvent(BaseModel):
    failure_id: str
    source: Literal["airflow", "openlineage", "github_actions", "webhook"]
    repo_id: str
    revision: str
    pipeline: str
    step: str
    error_type: str
    message: str
    stack_trace_artifact_id: str | None
    schema_artifact_ids: list[str]
    fixture_artifact_id: str | None
```

Add `RepairRun`, `CandidatePatch`, `CandidateEvaluation`, `DataContractDelta`, and
`PublicationRecord`. State transitions are methods, not arbitrary field assignment.

- [ ] **Step 4: Run repair model tests**

Run: `cd loopguard && python -m pytest -q tests/heal/test_models.py`
Expected: PASS.

- [ ] **Step 5: Commit repair state contracts**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal
git commit -m "feat: define gated pipeline repair workflow"
```

### Task 2: Normalize and fingerprint failure intake

**Files:**
- Create: `loopguard/src/loopguard/heal/intake.py`
- Create: `loopguard/src/loopguard/heal/fingerprint.py`
- Create: `loopguard/src/loopguard/heal/integrations/airflow.py`
- Create: `loopguard/src/loopguard/heal/integrations/openlineage.py`
- Create: `loopguard/src/loopguard/heal/integrations/github_actions.py`
- Test: `loopguard/tests/heal/test_intake.py`
- Test fixtures: `loopguard/tests/fixtures/heal/events/`

- [ ] **Step 1: Write failing cross-source fingerprint tests**

```python
from loopguard.heal.intake import normalize_failure


def test_airflow_and_openlineage_same_failure_share_fingerprint(fixtures):
    airflow = normalize_failure("airflow", fixtures.json("airflow_coordinate_failure.json"))
    lineage = normalize_failure("openlineage", fixtures.json("openlineage_coordinate_failure.json"))
    assert airflow.fingerprint == lineage.fingerprint


def test_volatile_ids_do_not_change_fingerprint(fixtures):
    first = normalize_failure("github_actions", fixtures.json("gha_failure_1.json"))
    second = normalize_failure("github_actions", fixtures.json("gha_failure_2.json"))
    assert first.fingerprint == second.fingerprint
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/heal/test_intake.py`
Expected: FAIL because failure intake is absent.

- [ ] **Step 3: Implement source adapters and normalization**

Normalize stack frames to repository-relative paths, remove timestamps/run IDs/memory addresses,
classify exception type, and hash:

```text
repo_id + revision_family + pipeline + step + exception_type + normalized_top_frames + schema_hash
```

Airflow callback payloads include DAG/task/run metadata and log artifact references. OpenLineage
accepts terminal `FAIL` run events. GitHub Actions intake uses workflow/job/check metadata.
Deduplicate active repairs by tenant, repo, and fingerprint.

- [ ] **Step 4: Run intake tests**

Run: `cd loopguard && python -m pytest -q tests/heal/test_intake.py`
Expected: PASS.

- [ ] **Step 5: Commit failure intake adapters**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal \
  loopguard/tests/fixtures/heal/events
git commit -m "feat: normalize pipeline failure intake"
```

### Task 3: Redact and validate replay fixtures

**Files:**
- Create: `loopguard/src/loopguard/heal/fixtures.py`
- Create: `loopguard/src/loopguard/heal/schema.py`
- Test: `loopguard/tests/heal/test_fixtures.py`

- [ ] **Step 1: Write failing PII and coordinate-schema tests**

```python
from loopguard.heal.fixtures import FixtureBuilder


def test_fixture_redacts_identity_but_preserves_types():
    fixture = FixtureBuilder().build([
        {"email": "person@example.com", "lat": 43.65, "lon": -79.38}
    ])
    assert fixture.rows[0]["email"] != "person@example.com"
    assert isinstance(fixture.rows[0]["email"], str)
    assert fixture.rows[0]["lat"] == 43.65


def test_schema_delta_detects_string_to_float():
    before = schema_of([{"lat": "43.65"}])
    after = schema_of([{"lat": 43.65}])
    assert diff_schema(before, after).changes == [{"path": "lat", "from": "string", "to": "float"}]
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/heal/test_fixtures.py`
Expected: FAIL because fixture services are absent.

- [ ] **Step 3: Implement allowlisted structural preservation**

Infer JSON/CSV primitive, nullability, range, enum, and nested shape. Redact configured PII fields
with deterministic type-preserving replacements. Limit row count and bytes. Record redaction
manifest and reject fixtures containing unredacted known secrets. If no safe fixture exists,
generate a synthetic fixture from schema and mark its provenance.

- [ ] **Step 4: Run fixture tests**

Run: `cd loopguard && python -m pytest -q tests/heal/test_fixtures.py`
Expected: PASS.

- [ ] **Step 5: Commit safe replay fixtures**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal
git commit -m "feat: build redacted pipeline replay fixtures"
```

### Task 4: Reproduce failures in an isolated sandbox

**Files:**
- Create: `loopguard/src/loopguard/heal/sandbox.py`
- Create: `loopguard/src/loopguard/heal/reproduce.py`
- Create: `loopguard/src/loopguard/heal/docker.py`
- Test: `loopguard/tests/heal/test_reproduce.py`
- Test fixture: `loopguard/tests/fixtures/heal/coordinate_pipeline/`

- [ ] **Step 1: Write failing reproduction-gate tests**

```python
def test_reproduction_must_match_failure_fingerprint(coordinate_fixture, sandbox):
    result = reproduce(coordinate_fixture.failure, sandbox)
    assert result.reproduced is True
    assert result.observed_fingerprint == coordinate_fixture.failure.fingerprint


def test_unrelated_failure_is_not_reproduction(other_failure, sandbox):
    result = reproduce(other_failure, sandbox)
    assert result.reproduced is False
    assert result.reason == "fingerprint_mismatch"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/heal/test_reproduce.py`
Expected: FAIL because reproduction is missing.

- [ ] **Step 3: Implement immutable, resource-bounded sandboxes**

Checkout the exact revision in a dedicated worktree. Build from an allowlisted image. Mount source
read-only for reproduction, fixture read-only, and a separate artifact directory writable. Drop
capabilities, run as non-root, set CPU/memory/PID/time limits, disable network by default, and pass
no production credentials. Capture command, image digest, repository SHA, exit status, output
artifact, and observed fingerprint.

- [ ] **Step 4: Run sandbox fixture tests**

Run: `cd loopguard && python -m pytest -q tests/heal/test_reproduce.py`
Expected: PASS. Mark Docker-dependent tests and run them in CI with an available daemon.

- [ ] **Step 5: Commit the reproduction gate**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal \
  loopguard/tests/fixtures/heal/coordinate_pipeline
git commit -m "feat: reproduce pipeline failures in isolation"
```

### Task 5: Generate independent bounded candidates

**Files:**
- Create: `loopguard/src/loopguard/heal/planner.py`
- Create: `loopguard/src/loopguard/heal/candidates.py`
- Test: `loopguard/tests/heal/test_candidates.py`

- [ ] **Step 1: Write failing independence and scope tests**

```python
def test_candidates_receive_same_baseline_not_each_others_diff(candidate_service):
    result = candidate_service.generate(failure_fixture(), count=3)
    assert len(result) == 3
    assert {candidate.base_sha for candidate in result} == {"base123"}
    assert all("candidate/" not in candidate.prompt_context for candidate in result)


def test_candidate_outside_allowed_scope_is_rejected(candidate_service):
    result = candidate_service.accept(
        patch_that_changes(["src/ingest.py", ".github/workflows/release.yml"]),
        allowed_paths=["src/**", "tests/**"],
    )
    assert result.code == "scope_violation"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/heal/test_candidates.py`
Expected: FAIL because candidate generation is absent.

- [ ] **Step 3: Implement repair strategies and isolation**

The planner emits strategy briefs, not patches:

- Normalize at ingestion boundary.
- Strengthen schema validation/coercion.
- Add backward-compatible adapter for upstream version drift.

Each candidate receives the same failure, fixture, contract, repository revision, and allowed
paths in a distinct worktree. Enforce maximum files, changed lines, tool calls, wall time, and cost.
Reject binary, dependency-lock, CI, secret, infrastructure, or migration changes unless the repair
policy explicitly permits them.

- [ ] **Step 4: Run candidate tests**

Run: `cd loopguard && python -m pytest -q tests/heal/test_candidates.py`
Expected: PASS.

- [ ] **Step 5: Commit bounded candidate generation**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal
git commit -m "feat: generate isolated repair candidates"
```

### Task 6: Evaluate and rank candidates by evidence

**Files:**
- Create: `loopguard/src/loopguard/heal/evaluate.py`
- Create: `loopguard/src/loopguard/heal/rank.py`
- Test: `loopguard/tests/heal/test_evaluate.py`

- [ ] **Step 1: Write the ranking matrix**

```python
def test_passing_minimal_boundary_fix_wins():
    candidates = [
        evaluation("broad", replay=True, regression=True, changed_lines=80, contract_break=True),
        evaluation("boundary", replay=True, regression=True, changed_lines=12, contract_break=False),
        evaluation("small-fail", replay=False, regression=True, changed_lines=4, contract_break=False),
    ]
    result = rank_candidates(candidates)
    assert result.winner_id == "boundary"
    assert result.rejected["small-fail"] == "replay_failed"


def test_no_candidate_wins_if_required_check_is_inconclusive():
    assert rank_candidates([evaluation("x", required_inconclusive=True)]).winner_id is None
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/heal/test_evaluate.py`
Expected: FAIL because evaluation/ranking is missing.

- [ ] **Step 3: Implement required evaluation order**

For each candidate:

1. Apply patch cleanly.
2. Confirm original reproduction command now passes.
3. Run fixture/schema/data-quality checks.
4. Run impacted unit/integration tests.
5. Run policy-required security/static checks.
6. Compute data-contract delta and diff metrics.

Fail closed on required timeout/inconclusive. Rank only passing candidates by contract
compatibility, risk penalties, changed files, changed lines, and observed verification duration.
No model chooses the winner.

- [ ] **Step 4: Run evaluation tests**

Run: `cd loopguard && python -m pytest -q tests/heal/test_evaluate.py`
Expected: PASS.

- [ ] **Step 5: Commit evidence-based ranking**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal
git commit -m "feat: rank repairs by deterministic evidence"
```

### Task 7: Publish an evidence-rich draft GitHub PR

**Files:**
- Create: `loopguard/src/loopguard/heal/github.py`
- Create: `loopguard/src/loopguard/heal/report.py`
- Test: `loopguard/tests/heal/test_github.py`
- Test fixture: `loopguard/tests/fixtures/heal/expected_pr.md`

- [ ] **Step 1: Write failing draft-only and report tests**

```python
def test_publisher_always_creates_draft(fake_github, verified_repair):
    result = GitHubPublisher(fake_github).publish(verified_repair)
    assert fake_github.pull_requests[0]["draft"] is True
    assert fake_github.merges == []


def test_report_contains_reproduction_candidates_and_rollback(verified_repair):
    text = render_report(verified_repair)
    assert "Original reproduction" in text
    assert "Candidate matrix" in text
    assert "Data contract delta" in text
    assert "Rollback" in text
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/heal/test_github.py`
Expected: FAIL because publication is missing.

- [ ] **Step 3: Implement GitHub App publication**

Use installation tokens scoped to contents and pull requests. Create branch
`loopguard/repair/<short-repair-id>`, commit only the winning patch and tests, push with expected
base SHA protection, and open a draft PR. Report root cause, sanitized failure fingerprint,
reproduction command/result, every candidate outcome, winning diff rationale, exact verification,
contract delta, risk, artifacts, and rollback. Never call merge or deployment APIs.

- [ ] **Step 4: Run publisher tests**

Run: `cd loopguard && python -m pytest -q tests/heal/test_github.py`
Expected: PASS.

- [ ] **Step 5: Commit draft PR publication**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal \
  loopguard/tests/fixtures/heal/expected_pr.md
git commit -m "feat: publish verified repairs as draft pull requests"
```

### Task 8: Orchestrate repair as a durable workflow

**Files:**
- Create: `services/control-api/src/loopguard_api/repair_workflow.py`
- Create: `services/control-api/src/loopguard_api/routes/repairs.py`
- Test: `services/control-api/tests/test_repair_workflow.py`
- Test: `loopguard/tests/integration/test_airflow_repair.py`

- [ ] **Step 1: Write failing resume and approval tests**

```python
def test_workflow_resumes_after_worker_restart(temporal_env, coordinate_failure):
    handle = temporal_env.start_workflow("repair", coordinate_failure)
    temporal_env.restart_worker_after("reproduction")
    result = handle.result()
    assert result.state == "awaiting_publication"
    assert result.reproduction_attempts == 1


def test_publication_waits_for_authorized_action(temporal_env, verified_repair):
    handle = temporal_env.start_workflow("repair", verified_repair)
    assert handle.query("state") == "awaiting_publication"
    handle.signal("publish", actor=viewer())
    assert handle.query("state") == "awaiting_publication"
    handle.signal("publish", actor=publisher())
    assert handle.result().state == "completed"
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd services/control-api && python -m pytest -q tests/test_repair_workflow.py
cd ../../loopguard && python -m pytest -q tests/integration/test_airflow_repair.py
```

Expected: FAIL because orchestration is absent.

- [ ] **Step 3: Implement durable activities and signals**

Activities are intake, fixture, reproduce, plan, generate candidate, evaluate candidate, rank,
render report, and publish. Each activity is idempotent by repair/candidate ID and persists an
artifact before returning. Workflow enforces time, attempt, candidate, and cost budgets. Publication
waits indefinitely for an authorized, unexpired action or configured cancellation timeout.

- [ ] **Step 4: Run workflow and end-to-end fixture tests**

Run:

```bash
cd services/control-api && python -m pytest -q tests/test_repair_workflow.py
cd ../../loopguard && python -m pytest -q tests/heal tests/integration/test_airflow_repair.py
```

Expected: PASS with fake providers, fake GitHub, and local Docker fixture.

- [ ] **Step 5: Commit the repair workflow**

```bash
git add services/control-api loopguard/src/loopguard/heal \
  loopguard/tests/heal loopguard/tests/integration
git commit -m "feat: orchestrate durable pipeline repairs"
```

### Task 9: Add observe-only rollout and safety metrics

**Files:**
- Create: `loopguard/src/loopguard/heal/metrics.py`
- Create: `loopguard/docs/auto-heal-runbook.md`
- Test: `loopguard/tests/heal/test_metrics.py`

- [ ] **Step 1: Write failing rollout-gate tests**

```python
def test_generation_disabled_until_reproduction_threshold(policy, metrics):
    metrics.record_reproduction(successes=17, attempts=20)
    assert policy.can_generate(metrics).allowed is False
    metrics.record_reproduction(successes=3, attempts=3)
    assert policy.can_generate(metrics).allowed is True
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/heal/test_metrics.py`
Expected: FAIL because rollout metrics are absent.

- [ ] **Step 3: Implement staged gates and runbook**

Modes: `observe`, `reproduce`, `generate`, `publish-draft`. Track intake deduplication,
reproduction rate, safe-fixture rate, candidate pass rate, no-winner rate, cost, time, publication
rate, PR acceptance, PR reversion, and incidents. Require explicit organization policy promotion
between modes. Document cancellation, credential revocation, artifact deletion, bad-patch response,
and GitHub App disable procedures.

- [ ] **Step 4: Run heal tests**

Run: `cd loopguard && python -m pytest -q tests/heal`
Expected: PASS.

- [ ] **Step 5: Commit repair rollout controls**

```bash
git add loopguard/src/loopguard/heal loopguard/tests/heal \
  loopguard/docs/auto-heal-runbook.md
git commit -m "feat: gate auto-heal rollout with safety metrics"
```

## Completion gate

Run:

```bash
cd loopguard
python -m pytest -q tests/heal tests/integration/test_airflow_repair.py
ruff check src/loopguard/heal tests/heal
```

Expected: all commands exit 0; a non-reproduced failure cannot generate candidates; no
inconclusive candidate can win; and publication creates only a draft PR after explicit authority.
