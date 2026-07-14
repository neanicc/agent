# Preference Engine Implementation Plan

> **For implementation agents:** Execute this plan task-by-task and track every checkbox. In Codex, use the native plan, debugging, review, and verification tools available in the host. In Claude Code, use `superpowers:subagent-driven-development` or `superpowers:executing-plans` when installed. A missing named workflow is never a blocker; perform the equivalent TDD and verification steps directly.

> **Command convention:** Resolve one absolute, supported virtualenv interpreter as `$PY`. In every shell snippet, read bare `python` as `$PY`, `python -m pip` as `$PY -m pip`, and `ruff` as `$PY -m ruff`; never assume those executables are on `PATH`.

**Goal:** Give LoopGuard durable, editable product and design preferences without turning subjective model output into unreviewable hard policy.

**Architecture:** Sources compile into ordered hard rules, repository conventions, explicit user preferences, and learned soft preferences. Deterministic evaluators run first; a bounded visual critic can add evidence for UI changes, but only explicit hard rules may block automatically.

**Tech Stack:** Python 3.11+, Pydantic 2, TOML/YAML, SQLite, JSON Schema, Playwright artifacts, pytest

All illustrative fixtures (`fixtures`, `profile`, `ui_diff`, `axe_artifact`, `request`,
`violation`, and `ui_verification`) must be implemented in
`loopguard/tests/preferences/conftest.py` or an explicitly listed integration-support module.

---

### Task 1: Define preference sources, rules, and verdicts

**Files:**
- Create: `loopguard/src/loopguard/preferences/__init__.py`
- Create: `loopguard/src/loopguard/preferences/models.py`
- Test: `loopguard/tests/preferences/test_models.py`

- [ ] **Step 1: Write failing severity-boundary tests**

```python
import pytest

from loopguard.preferences.models import PreferenceRule


def test_learned_rule_cannot_be_blocking():
    with pytest.raises(ValueError, match="learned preferences must be soft"):
        PreferenceRule(
            id="dense-layout",
            source="learned",
            severity="block",
            statement="Prefer compact task cards",
        )


def test_explicit_rule_can_be_blocking():
    rule = PreferenceRule(
        id="dynamic-type",
        source="explicit",
        severity="block",
        statement="All iOS text supports Dynamic Type",
    )
    assert rule.severity == "block"
```

- [ ] **Step 2: Verify missing package**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_models.py`
Expected: FAIL because `loopguard.preferences` does not exist.

- [ ] **Step 3: Implement strict preference types**

```python
class PreferenceRule(BaseModel):
    id: str
    source: Literal["safety", "repository", "explicit", "learned"]
    severity: Literal["block", "warn", "inform"]
    statement: str
    scopes: list[str] = Field(default_factory=list)
    evaluator: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def learned_is_soft(self):
        if self.source == "learned" and self.severity == "block":
            raise ValueError("learned preferences must be soft")
        return self
```

Add `PreferenceProfile`, `PreferenceEvidence`, and `PreferenceVerdict`. Every verdict cites rule,
artifact, evaluator version, and confidence where applicable.

- [ ] **Step 4: Run model tests**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_models.py`
Expected: PASS.

- [ ] **Step 5: Commit preference contracts**

```bash
git add loopguard/src/loopguard/preferences loopguard/tests/preferences
git commit -m "feat: define editable preference policy"
```

### Task 2: Compile repository and user preference sources

**Files:**
- Create: `loopguard/src/loopguard/preferences/compiler.py`
- Create: `loopguard/src/loopguard/preferences/defaults.toml`
- Modify: `loopguard/pyproject.toml`
- Create: `loopguard/tests/preferences/conftest.py`
- Test: `loopguard/tests/preferences/test_compiler.py`
- Test fixtures: `loopguard/tests/fixtures/preferences/repo/`

- [ ] **Step 1: Write failing precedence tests**

```python
from loopguard.preferences.compiler import PreferenceCompiler


def test_explicit_repo_rule_overrides_default_without_deleting_safety(fixtures):
    profile = PreferenceCompiler().compile(
        repo=fixtures / "repo",
        user_profile={"rules": [{"id": "density", "statement": "Prefer compact layouts",
                                  "source": "explicit", "severity": "warn"}]},
    )
    assert profile.rule("density").statement == "Prefer compact layouts"
    assert profile.rule("wcag-contrast").source == "safety"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_compiler.py`
Expected: FAIL because the compiler is missing.

- [ ] **Step 3: Implement explicit source readers**

Read:

- Built-in safety/accessibility defaults.
- `.loopguard/preferences.toml`.
- Repository design token files named in that TOML.
- A bounded `## Design preferences` section from `AGENTS.md` or `CLAUDE.md`.
- User profile passed by the daemon.
- Learned soft preferences from the preference store.

Merge by stable rule ID. Safety rules cannot be deleted; organization policy can raise but not
lower their severity. Emit a source manifest with hashes for audit. Repository-owned instructions
are untrusted input: they can suggest repository-scoped rules but cannot weaken safety/organization
policy, grant tool/network access, alter evaluator code, or become a blocking user rule without an
authorized promotion. Parse only the documented bounded schema/section, treat its text as data,
and reject duplicate IDs, include/path escapes, oversized files, aliases/anchors that expand
unboundedly, and unsupported token formats.

Add a `preferences` optional dependency group for bounded YAML/JSON Schema/image metadata parsing
used by the implementation, and extend `all-dev` plus its metadata test. Prefer standard-library
TOML and do not add a parser dependency that the chosen file formats do not need.

- [ ] **Step 4: Run compiler and malformed-source tests**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_compiler.py`
Expected: PASS.

- [ ] **Step 5: Commit source compilation**

```bash
git add loopguard/src/loopguard/preferences loopguard/tests/preferences \
  loopguard/tests/fixtures/preferences loopguard/pyproject.toml
git commit -m "feat: compile repository and user preferences"
```

### Task 3: Implement deterministic evaluators

**Files:**
- Create: `loopguard/src/loopguard/preferences/evaluators.py`
- Create: `loopguard/src/loopguard/preferences/accessibility.py`
- Test: `loopguard/tests/preferences/test_evaluators.py`

- [ ] **Step 1: Write failing token and accessibility tests**

```python
from loopguard.preferences.evaluators import evaluate_artifacts


def test_unknown_color_literal_warns_when_tokens_are_required(profile, ui_diff):
    verdicts = evaluate_artifacts(profile, ui_diff.with_added("#12ABEF"))
    verdict = next(item for item in verdicts if item.rule_id == "use-design-tokens")
    assert verdict.status == "violation"
    assert verdict.severity == "warn"


def test_missing_accessible_name_blocks(profile, axe_artifact):
    verdicts = evaluate_artifacts(profile, axe_artifact.with_violation("button-name"))
    assert any(item.severity == "block" for item in verdicts)
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_evaluators.py`
Expected: FAIL because evaluators are absent.

- [ ] **Step 3: Implement evaluator registry**

```python
class PreferenceEvaluator(Protocol):
    name: str
    version: str
    def supports(self, artifact: EvidenceArtifact) -> bool: ...
    def evaluate(
        self, rule: PreferenceRule, artifact: EvidenceArtifact
    ) -> list[PreferenceVerdict]: ...
```

Initial deterministic evaluators:

- Design-token literal detection.
- Required component/import usage.
- Web axe accessibility result mapping.
- iOS accessibility snapshot requirements.
- Text-size/Dynamic Type metadata.
- Reduced-motion policy.
- Screenshot dimensions and reference presence.

Do not implement generic aesthetic scoring in this task.

- [ ] **Step 4: Run evaluator tests**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_evaluators.py`
Expected: PASS.

- [ ] **Step 5: Commit deterministic preference evaluation**

```bash
git add loopguard/src/loopguard/preferences loopguard/tests/preferences
git commit -m "feat: evaluate deterministic design preferences"
```

### Task 4: Record approvals and learn soft preferences

**Files:**
- Create: `loopguard/src/loopguard/preferences/store.py`
- Create: `loopguard/src/loopguard/preferences/learning.py`
- Test: `loopguard/tests/preferences/test_learning.py`

- [ ] **Step 1: Write failing promotion tests**

```python
from loopguard.preferences.learning import PreferenceLearner


def test_repeated_same_choice_creates_soft_rule(tmp_path):
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db", minimum_examples=3)
    for decision_id in ("d1", "d2", "d3"):
        learner.record(decision_id, choice="compact", context={"surface": "task-card"})
    rules = learner.derive_rules()
    assert rules[0].source == "learned"
    assert rules[0].severity == "inform"


def test_conflicting_choices_do_not_create_rule(tmp_path):
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db", minimum_examples=3)
    for i, choice in enumerate(("compact", "spacious", "compact")):
        learner.record(f"d{i}", choice=choice, context={"surface": "task-card"})
    assert learner.derive_rules() == []
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_learning.py`
Expected: FAIL because the learner is missing.

- [ ] **Step 3: Implement conservative derivation**

Store explicit operator choices with artifact hashes and context dimensions. Derive a candidate
only after the configured minimum consistent examples and minimum confidence. Learned rules start
as `inform`, are visible/editable in UI, and require explicit promotion to become `warn` or
`block`. Bind examples and promotions to tenant/user/repository scope and an authenticated actor.
Deleting a learned rule also records a negative example. Revoked/deleted preference data stops
contributing immediately; retain only the minimum audited tombstone required by policy.

- [ ] **Step 4: Run learning tests**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_learning.py`
Expected: PASS.

- [ ] **Step 5: Commit preference learning**

```bash
git add loopguard/src/loopguard/preferences loopguard/tests/preferences
git commit -m "feat: learn reviewable soft preferences"
```

### Task 5: Add a budgeted visual critic

**Files:**
- Create: `loopguard/src/loopguard/preferences/visual_critic.py`
- Test: `loopguard/tests/preferences/test_visual_critic.py`

- [ ] **Step 1: Write failing eligibility and schema tests**

```python
from loopguard.preferences.visual_critic import VisualCritic


def test_critic_skips_without_reference_or_budget(fake_model):
    critic = VisualCritic(fake_model)
    assert critic.evaluate(request(reference=None, budget_usd="0.02")).status == "skipped"
    assert critic.evaluate(request(reference="ref.png", budget_usd="0")).status == "skipped"
    assert fake_model.calls == []


def test_critic_cannot_return_block_for_soft_rule(fake_model):
    fake_model.return_json({"status": "violation", "severity": "block",
                            "evidence": "spacing differs"})
    verdict = VisualCritic(fake_model).evaluate(request(reference="ref.png", source="learned"))
    assert verdict.severity == "inform"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_visual_critic.py`
Expected: FAIL because `VisualCritic` is missing.

- [ ] **Step 3: Implement eligibility, structured output, and downgrade**

Require UI task classification, current screenshot, reference or explicit visual rules, and
reserved critic budget. Send only the relevant screenshot crop, rule text, and artifact metadata.
Require JSON matching `PreferenceVerdict`. Clamp severity to the source rule's configured severity
and store model, prompt version, cost, and artifact hashes.

Treat screenshot text, repository rule text, filenames, and metadata as hostile data that may
contain instructions. Use a fixed structured prompt, delimit data, prohibit tool use, validate
strict JSON, and ignore any data-supplied request to change policy, reveal prompts, fetch URLs, or
increase severity. Redact image/text evidence before provider calls and respect local-only policy.
Add prompt-injection fixtures in source code, terminal output, screenshot OCR text, rule text, and
filenames; all must remain inert.

- [ ] **Step 4: Run critic tests**

Run: `cd loopguard && python -m pytest -q tests/preferences/test_visual_critic.py`
Expected: PASS without a live API.

- [ ] **Step 5: Commit bounded visual evaluation**

```bash
git add loopguard/src/loopguard/preferences loopguard/tests/preferences
git commit -m "feat: add budgeted visual preference critic"
```

### Task 6: Integrate preference verdicts with verification

**Files:**
- Create: `loopguard/src/loopguard/preferences/service.py`
- Modify: `loopguard/src/loopguard/verify/service.py`
- Test: `loopguard/tests/integration/test_ui_preferences.py`

- [ ] **Step 1: Write failing hard/soft integration tests**

```python
def test_soft_design_violation_does_not_fail_verification(ui_verification):
    ui_verification.add_preference_verdict(violation(severity="warn"))
    assert ui_verification.complete().status == "verified"


def test_hard_accessibility_violation_fails_verification(ui_verification):
    ui_verification.add_preference_verdict(violation(severity="block"))
    verdict = ui_verification.complete()
    assert verdict.status == "incomplete"
    assert verdict.missing_required_checks == ["preference:accessible-name"]
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/integration/test_ui_preferences.py`
Expected: FAIL because preference evidence is not part of verification.

- [ ] **Step 3: Add preference evidence links**

Verification stores preference verdict IDs and treats only `block` as a required-check failure.
Warnings appear in the proof record and agent context digest. A user override records actor,
reason, and scope without changing the original verdict.

Register the compiler, deterministic evaluators, preference store/learner, and optional visual
critic in `DaemonServices`. Cache profiles by source-manifest hash and invalidate transactionally
on file/user/org/learned-rule changes. Daemon restart must preserve the exact effective policy
version referenced by an in-flight verification rather than silently reevaluating it under a new
profile.

- [ ] **Step 4: Run preference and integration tests**

Run: `cd loopguard && python -m pytest -q tests/preferences tests/integration/test_ui_preferences.py`
Expected: PASS.

- [ ] **Step 5: Commit preference-aware proof**

```bash
git add loopguard/src/loopguard/preferences loopguard/src/loopguard/verify \
  loopguard/tests/integration loopguard/tests/preferences
git commit -m "feat: include design preferences in verification"
```

## Completion gate

Run:

```bash
cd loopguard
python -m pytest -q tests/preferences tests/integration/test_ui_preferences.py
```

Expected: all tests pass; deterministic rules work offline; learned preferences remain soft; and
visual evaluation cannot exceed budget, obey hostile repository/screenshot instructions, or
silently escalate severity. In-flight proof records retain their original effective policy version
across preference changes and daemon restart.
