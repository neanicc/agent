# Regression Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove whether an agent change satisfies its task without introducing regressions, while separating new failures from pre-existing failures.

**Architecture:** A proof contract defines required evidence. The verifier captures a baseline, computes an impact set, runs bounded commands, stores immutable evidence, and derives a deterministic verdict that native Stop hooks and managed sessions can enforce.

**Tech Stack:** Python 3.11+, Pydantic 2, asyncio subprocesses, pytest/JUnit parsing, TypeScript/Jest JSON parsing, Git, pytest

**Proof ownership rule:** a result is called `verified` only when LoopGuard recorded the task
intake, proof contract, clean pre-mutation repository/worktree snapshot, and required baseline
before the first mutating tool. Managed mode guarantees this ordering. Attached mode uses
`UserPromptSubmit` plus pre-tool hooks; if LoopGuard attached late or the native surface cannot
prove ordering, the final status is `inconclusive` or “checks passed without a trusted baseline,”
never `verified`.

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
    source_event_id: str
    source_prompt_hash: str
    acceptance: list[str] = Field(min_length=1)
    invariants: list[str]
    non_goals: list[str] = Field(default_factory=list)
    changed_scope: list[str] = Field(default_factory=list)
    checks: list[CheckSpec] = Field(min_length=1)
```

Add `CheckResult`, `Baseline`, `VerificationRun`, `EvidenceArtifact`, and `VerificationVerdict`
types. Use explicit statuses: `passed`, `failed`, `timed_out`, `skipped`, and `inconclusive`.
`Baseline` stores capture time, repository SHA, worktree hash, dirty/untracked manifest, owning
session, source prompt event, and `captured_before_first_mutation`. A contract may be supplied by
trusted repository config, a managed-run request, or an explicit user/agent contract event; store
source and hash. Acceptance text derived automatically from a raw prompt is a draft until a trusted
rule or actor confirms it.

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
- Create: `loopguard/src/loopguard/verify/trust.py`
- Test: `loopguard/tests/verify/test_discovery.py`
- Test: `loopguard/tests/verify/test_trust.py`
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
source path, content hash, trust state, requested capabilities, and risk classification for audit.
Repository-owned configuration, `AGENTS.md`, package scripts, and tool config are untrusted code.
They never become executable merely because discovery found them.

Create repository trust records bound to canonical repository identity and the exact command/config
hash. First use shows a preview with executable, arguments, working directory, environment names,
network/secrets request, timeout, and source. The user or an organization policy must approve it.
Changes invalidate approval. Deny command classes that deploy, publish, mutate Git history, access
credential stores, start privileged containers, escape the repository, or use an interactive
shell unless a separate explicit policy allows them.

Tests cover malicious package scripts, `../` cwd escape, symlink escape, shell metacharacters as
arguments, config-hash changes, revoked trust, inherited environment secrets, network requests,
and a benign approved command. Discovery itself remains side-effect free.

- [ ] **Step 4: Run discovery tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_discovery.py tests/verify/test_trust.py`
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
- Create: `loopguard/src/loopguard/verify/intake.py`
- Create: `loopguard/src/loopguard/verify/isolation.py`
- Test: `loopguard/tests/verify/test_baseline.py`
- Test: `loopguard/tests/verify/test_runner.py`
- Test: `loopguard/tests/verify/test_intake_ordering.py`

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
- Resolve real `cwd` beneath the bound worktree and reject symlink/path escape.
- Start with an empty environment plus an explicit allowlist; no agent, cloud, signing, SSH,
  package-registry, or user credential variables.
- Deny network by default and run through an available managed-agent/container/platform isolation
  provider with read-only host mounts and only the worktree writable.
- If the platform cannot provide an actual isolation boundary, label the run `unsandboxed` and
  require explicit per-command approval; never auto-run repository code under a false sandbox
  claim.
- Enforce timeout, CPU/memory/process/file/output limits, stdout/stderr byte limits, and process
  group/tree kill.
- Store raw output as an artifact and return a bounded parsed summary.
- Parse pytest node IDs, JUnit XML when configured, Jest JSON, and generic exit status.

`BaselineService.capture()` runs only checks marked `baseline` or checks needed to classify later
completion results.

`ProofIntakeService` consumes `UserPromptSubmit`/managed-run events, records the source prompt hash
and proof contract, classifies mutating tool calls, and atomically marks the first mutation.
Before the first mutation it captures repository/worktree state and required baseline checks.
Managed sessions fail closed if that cannot complete. Attached sessions follow explicit policy
(warn/block), but the stored verification remains inconclusive unless ordering is proven. Add
integration tests for late attach, prompt replaced before mutation, simultaneous mutation hooks,
baseline timeout, dirty initial tree, daemon restart between baseline and first write, and a
mutation attempted while baseline capture is in flight.

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
- Create: `loopguard/src/loopguard/verify/artifacts.py`
- Create: `loopguard/src/loopguard/verify/manifest.py`
- Create: `loopguard/src/loopguard/verify/service.py`
- Test: `loopguard/tests/verify/test_service.py`
- Test: `loopguard/tests/verify/test_artifacts.py`
- Test: `loopguard/tests/verify/test_recovery.py`

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

Store contract hash, repository SHA, explicit `repo_seq`/`session_seq`, command specs, artifact
references, summaries, isolation level, and verdict. Reject updates after terminal state. Link
every result to the exact working tree hash observed before the command started.

Use a content-addressed artifact store. Canonical bytes are redacted, classified, hashed, encrypted
with per-artifact authenticated encryption, written to a temporary file/object, fsynced/uploaded
with checksum validation, then atomically made visible. A signed manifest binds artifact hash,
size, media type, repository/worktree/verification IDs, command/result IDs, redaction policy,
encryption key ID, retention class, and creation time. Reading recomputes the hash and verifies the
manifest signature. Mutable paths/URLs and database hashes without stored bytes are not evidence.

Define retention classes for summaries, raw logs, screenshots/traces, and sensitive artifacts.
Deletion is a privileged audited tombstone/crypto-shred operation; a completed proof remains
immutable but can report that a retention-authorized artifact is no longer available. Tests cover
partial write/upload, hash mismatch, wrong key, signature failure, redaction, retention expiry,
deletion authorization, duplicate bytes, crash after object write before DB commit, and DB record
without object.

On daemon restart, runs in `baselining`, `active`, or `completing` become explicit `recovering`
records. Resume only idempotent work with matching worktree/contract/command hashes. Otherwise mark
the run `orphaned` or `inconclusive` with the exact reason; never silently start a second command or
call an abandoned run verified.

- [ ] **Step 4: Run service and migration tests**

Run: `cd loopguard && python -m pytest -q tests/verify/test_service.py tests/verify/test_artifacts.py tests/verify/test_recovery.py`
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

Completion policy distinguishes:

- `verified`: trusted contract and pre-mutation baseline plus all required evidence.
- `verified_with_preexisting_failures`: same proof chain with unchanged known failures.
- `checks_passed_unbaselined`: commands passed but attached ordering/baseline ownership is not
  proven; display as inconclusive, never a green verified badge.
- `regression`, `incomplete`, `inconclusive`, `orphaned`: explicit non-success outcomes.

Register proof intake, runner, artifact store, verdict service, and completion enforcement in
`DaemonServices`. The end-to-end test must start from a user-prompt event, capture baseline before
a mutating pre-tool event, journal the change, run verification, persist signed evidence, and
return the correct Stop/managed completion decision. Add late-attach and daemon-restart variants.

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

Expected: all commands exit 0; untrusted repository commands never auto-execute; new failures are
never classified as pre-existing; missing/late baseline, weak isolation, absent artifacts,
recovery ambiguity, and missing required evidence cannot produce a verified verdict.
