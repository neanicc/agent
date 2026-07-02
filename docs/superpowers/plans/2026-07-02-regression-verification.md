# Regression Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove whether an agent change satisfies its task without introducing regressions, while separating new failures from pre-existing failures.

**Architecture:** A proof contract defines required evidence. The verifier captures a baseline, computes an impact set, runs bounded commands, stores immutable evidence, and derives a deterministic verdict that native Stop hooks and managed sessions can enforce.

**Tech Stack:** Python 3.11+, Pydantic 2, asyncio subprocesses, pytest/JUnit parsing, TypeScript/Jest JSON parsing, Git, pytest

---

### Task 1: Define proof contracts and verification records

**Files:**
- Create: `loopguard/src/loopguard/verify/__init__.py`
- Create: `loopguard/src/loopguard/verify/models.py`
- Test: `loopguard/tests/verify/test_models.py`

- [ ] **Step 1: Write failing validation tests**

```python
import pytest

from loopguard.verify.models import CheckSpec, ProofContract


def test_contract_requires_acceptance_and_completion_checks():
    with pytest.raises(ValueError):
        ProofContract(task_id="t", acceptance=[], invariants=[], checks=[])

    contract = ProofContract(
        task_id="t",
        acceptance=["Login rejects an invalid password"],
        invariants=["Valid login still succeeds"],
        checks=[CheckSpec(id="unit", command=["pytest", "tests/test_login.py"])],
    )
    assert contract.checks[0].required is True
```

- [ ] **Step 2: Verify missing module**

Run: `cd loopguard && python -m pytest -q tests/verify/test_models.py`
Expected: FAIL because `loopguard.verify` does not exist.

- [ ] **Step 3: Implement strict proof types**

```python
class CheckSpec(BaseModel):
    id: str
    command: list[str]
    cwd: str = "."
    required: bool = True
    timeout_seconds: int = 300
    phase: Literal["baseline", "impacted", "completion", "pr"] = "completion"


class ProofContract(BaseModel):
    task_id: str
    acceptance: list[str] = Field(min_length=1)
    invariants: list[str]
    non_goals: list[str] = Field(default_factory=list)
    changed_scope: list[str] = Field(default_factory=list)
    checks: list[CheckSpec] = Field(min_length=1)
```

Add `CheckResult`, `Baseline`, `VerificationRun`, `EvidenceArtifact`, and `VerificationVerdict`
types. Use explicit statuses: `passed`, `failed`, `timed_out`, `skipped`, and `inconclusive`.

- [ ] **Step 4: Run model tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_models.py`
Expected: PASS.

- [ ] **Step 5: Commit proof types**

```bash
git add loopguard/src/loopguard/verify loopguard/tests/verify
git commit -m "feat: define proof contracts and evidence"
```

### Task 2: Discover repository verification commands

**Files:**
- Create: `loopguard/src/loopguard/verify/discovery.py`
- Test: `loopguard/tests/verify/test_discovery.py`
- Test fixtures: `loopguard/tests/fixtures/verify/python_project/`
- Test fixtures: `loopguard/tests/fixtures/verify/typescript_project/`

- [ ] **Step 1: Write failing deterministic discovery tests**

```python
from loopguard.verify.discovery import discover_checks


def test_discovers_python_and_package_script_checks(fixtures):
    python_checks = discover_checks(fixtures / "python_project")
    assert [check.command for check in python_checks] == [["python", "-m", "pytest", "-q"]]

    ts_checks = discover_checks(fixtures / "typescript_project")
    assert ["npm", "test", "--", "--runInBand"] in [check.command for check in ts_checks]
    assert ["npx", "tsc", "--noEmit"] in [check.command for check in ts_checks]
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/verify/test_discovery.py`
Expected: FAIL because `discover_checks` is missing.

- [ ] **Step 3: Implement configuration-first discovery**

Read commands in this order:

1. `.loopguard/verification.toml`
2. nearest `AGENTS.md` fenced `verification` block
3. `pyproject.toml`/pytest configuration
4. `package.json` scripts

Never execute a discovered command during discovery. Return structured `CheckSpec` values and the
source path for audit. Do not infer destructive or deployment commands.

- [ ] **Step 4: Run discovery tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_discovery.py`
Expected: PASS.

- [ ] **Step 5: Commit deterministic check discovery**

```bash
git add loopguard/src/loopguard/verify loopguard/tests/verify \
  loopguard/tests/fixtures/verify
git commit -m "feat: discover repository verification commands"
```

### Task 3: Capture a baseline before edits

**Files:**
- Create: `loopguard/src/loopguard/verify/runner.py`
- Create: `loopguard/src/loopguard/verify/baseline.py`
- Test: `loopguard/tests/verify/test_baseline.py`

- [ ] **Step 1: Write failing pre-existing failure tests**

```python
from loopguard.verify.baseline import BaselineService


def test_baseline_records_existing_failure_without_marking_regression(fake_runner):
    fake_runner.add(["pytest", "-q"], exit_code=1, failures=["tests/test_old.py::test_old"])
    baseline = BaselineService(fake_runner).capture(contract_with(["pytest", "-q"]))
    assert baseline.results[0].status == "failed"
    assert baseline.results[0].failure_ids == ["tests/test_old.py::test_old"]
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/verify/test_baseline.py`
Expected: FAIL because baseline services are missing.

- [ ] **Step 3: Implement a bounded subprocess runner**

`CommandRunner.run(CheckSpec)` must:

- Use `asyncio.create_subprocess_exec`, never `shell=True`.
- Enforce `cwd`, environment allowlist, timeout, stdout/stderr byte limits, and process-group kill.
- Store raw output as an artifact and return a bounded parsed summary.
- Parse pytest node IDs, JUnit XML when configured, Jest JSON, and generic exit status.

`BaselineService.capture()` runs only checks marked `baseline` or checks needed to classify later
completion results.

- [ ] **Step 4: Run timeout, output-limit, and baseline tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_baseline.py tests/verify/test_runner.py`
Expected: PASS.

- [ ] **Step 5: Commit baseline execution**

```bash
git add loopguard/src/loopguard/verify loopguard/tests/verify
git commit -m "feat: capture pre-change verification baseline"
```

### Task 4: Compute the impacted verification set

**Files:**
- Create: `loopguard/src/loopguard/verify/impact.py`
- Create: `loopguard/src/loopguard/verify/plugins.py`
- Test: `loopguard/tests/verify/test_impact.py`

- [ ] **Step 1: Write failing impact traversal tests**

```python
from loopguard.verify.impact import ImpactAnalyzer


def test_changed_symbol_selects_direct_tests_and_callers(symbol_index):
    analyzer = ImpactAnalyzer(symbol_index, max_depth=2)
    impact = analyzer.analyze(changed_paths=["src/auth.py"], changed_symbols=["login"])
    assert "tests/test_auth.py" in impact.test_paths
    assert "src/routes/login.py" in impact.dependent_paths
    assert impact.explanation["tests/test_auth.py"] == ["covers:src/auth.py:login"]
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/verify/test_impact.py`
Expected: FAIL because `ImpactAnalyzer` is missing.

- [ ] **Step 3: Implement bounded graph traversal and plugins**

Use the context symbol/import index, test-name/path conventions, stored historical test failures,
and optional coverage edges. Stop traversal at configured depth and maximum nodes. Every selected
test must include an explanation edge.

Define plugin protocol:

```python
class ImpactPlugin(Protocol):
    name: str
    def analyze(self, repo: Path, changes: list[ChangeRecord]) -> ImpactContribution: ...
```

Initial plugins: Python imports/tests and TypeScript imports/test-name conventions. Route, schema,
and UI plugins land only after fixture coverage exists.

- [ ] **Step 4: Run impact tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_impact.py`
Expected: PASS, including cycles, maximum-depth, deleted-file, and no-index fallback cases.

- [ ] **Step 5: Commit impact analysis**

```bash
git add loopguard/src/loopguard/verify loopguard/tests/verify
git commit -m "feat: select impacted regression checks"
```

### Task 5: Derive deterministic regression verdicts

**Files:**
- Create: `loopguard/src/loopguard/verify/verdict.py`
- Test: `loopguard/tests/verify/test_verdict.py`

- [ ] **Step 1: Write the verdict matrix before implementation**

```python
import pytest

from loopguard.verify.verdict import compare_results


@pytest.mark.parametrize(
    ("baseline", "current", "expected"),
    [
        (["old"], ["old"], "verified_with_preexisting_failures"),
        (["old"], [], "verified"),
        (["old"], ["old", "new"], "regression"),
        ([], ["new"], "regression"),
        ([], [], "verified"),
    ],
)
def test_failure_set_matrix(baseline, current, expected):
    assert compare_results(result(baseline), result(current)).status == expected
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/verify/test_verdict.py`
Expected: FAIL because `compare_results` is missing.

- [ ] **Step 3: Implement set-based classification**

Verdicts must contain:

```python
class VerificationVerdict(BaseModel):
    status: Literal[
        "verified", "verified_with_preexisting_failures",
        "regression", "incomplete", "inconclusive"
    ]
    introduced_failures: list[str]
    resolved_failures: list[str]
    remaining_preexisting_failures: list[str]
    missing_required_checks: list[str]
    evidence_ids: list[str]
```

Any required timeout, skip, missing result, or parser error prevents `verified`. A passing command
without acceptance evidence can still be `incomplete`.

- [ ] **Step 4: Run verdict tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_verdict.py`
Expected: PASS.

- [ ] **Step 5: Commit deterministic verdicts**

```bash
git add loopguard/src/loopguard/verify/verdict.py loopguard/tests/verify/test_verdict.py
git commit -m "feat: distinguish regressions from existing failures"
```

### Task 6: Persist immutable evidence and proof records

**Files:**
- Create: `loopguard/src/loopguard/verify/store.py`
- Create: `loopguard/src/loopguard/verify/service.py`
- Test: `loopguard/tests/verify/test_service.py`

- [ ] **Step 1: Write failing immutability and replay tests**

```python
import pytest

from loopguard.verify.service import VerificationService


def test_completed_verification_is_immutable(tmp_path, fake_runner):
    service = VerificationService.for_path(tmp_path / "verify.db", runner=fake_runner)
    run_id = service.start(contract_fixture())
    service.complete(run_id)
    with pytest.raises(ValueError, match="completed verification is immutable"):
        service.add_result(run_id, passing_result())
    assert service.read(run_id).verdict.status == "verified"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/verify/test_service.py`
Expected: FAIL because `VerificationService` is missing.

- [ ] **Step 3: Implement state transitions**

Allowed transitions:

```text
created -> baselining -> active -> completing -> completed
                                  -> cancelled
                                  -> failed
```

Store contract hash, repository SHA, journal cursors, command specs, raw artifact hashes, summaries,
and verdict. Reject updates after terminal state. Link every result to the exact working tree hash
observed before the command started.

- [ ] **Step 4: Run service and migration tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_service.py`
Expected: PASS.

- [ ] **Step 5: Commit proof persistence**

```bash
git add loopguard/src/loopguard/verify loopguard/tests/verify
git commit -m "feat: persist immutable verification proof"
```

### Task 7: Enforce verification at native and managed completion

**Files:**
- Create: `loopguard/src/loopguard/verify/hooks.py`
- Modify: `loopguard/src/loopguard/adapters/hook_entry.py`
- Modify: `loopguard/src/loopguard/control/daemon.py`
- Test: `loopguard/tests/integration/test_verified_completion.py`

- [ ] **Step 1: Write failing Stop-hook behavior tests**

```python
from loopguard.verify.hooks import stop_decision


def test_stop_blocks_when_required_check_has_new_failure(regression_verification):
    decision = stop_decision(regression_verification)
    assert decision.allow is False
    assert "new failure" in decision.message


def test_observe_only_mode_warns_without_blocking(regression_verification):
    decision = stop_decision(regression_verification, enforcement="observe")
    assert decision.allow is True
    assert decision.severity == "warning"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/integration/test_verified_completion.py`
Expected: FAIL because completion integration is absent.

- [ ] **Step 3: Implement surface-specific completion response**

The daemon returns a normalized `PolicyDecision`. Codex/Claude hook renderers translate it into
their documented Stop-hook response. Managed adapters continue the run with a concise evidence
message or mark it completed. Enforce a maximum of two automatic repair continuations per
verification fingerprint; further failures require human input.

- [ ] **Step 4: Run integration and complete suite**

Run: `cd loopguard && python -m pytest -q tests/verify tests/integration/test_verified_completion.py`
Expected: PASS.

- [ ] **Step 5: Commit verified completion**

```bash
git add loopguard/src/loopguard loopguard/tests/integration loopguard/tests/verify
git commit -m "feat: require evidence before verified completion"
```

## Completion gate

Run:

```bash
cd loopguard
python -m pytest -q tests/verify tests/integration/test_verified_completion.py
ruff check src/loopguard/verify tests/verify
```

Expected: all commands exit 0; new failures are never classified as pre-existing; and missing
required evidence cannot produce a verified verdict.
