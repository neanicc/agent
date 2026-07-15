from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from loopguard.verify.models import (
    AcceptanceState,
    Baseline,
    CheckResult,
    CheckSpec,
    CheckStatus,
    ContractSource,
    EvidenceArtifact,
    IsolationLevel,
    ProofContract,
    RunStatus,
    VerificationRun,
    VerificationVerdict,
    VerdictStatus,
)


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _contract(**overrides) -> ProofContract:
    values = {
        "task_id": "task-1",
        "source_event_id": "event-1",
        "source_prompt_hash": "a" * 64,
        "source": ContractSource.CONTRACT_EVENT,
        "source_hash": "b" * 64,
        "acceptance_state": AcceptanceState.CONFIRMED,
        "acceptance": ["Invalid credentials are rejected"],
        "invariants": ["Valid credentials still work"],
        "checks": [CheckSpec(id="unit", command=["pytest", "-q"])],
    }
    values.update(overrides)
    return ProofContract(**values)


def test_contract_requires_acceptance_and_completion_checks() -> None:
    with pytest.raises(ValidationError):
        _contract(acceptance=[])
    with pytest.raises(ValidationError):
        _contract(checks=[])
    with pytest.raises(ValidationError, match="required completion check"):
        _contract(
            checks=[
                CheckSpec(
                    id="baseline",
                    command=["pytest", "-q"],
                    phase="baseline",
                )
            ]
        )

    contract = _contract()

    assert contract.checks[0].required is True
    assert contract.checks[0].phase == "completion"


def test_contract_and_check_specs_are_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        CheckSpec(id="unit", command=[])
    with pytest.raises(ValidationError):
        CheckSpec(id="unit", command=["pytest"], timeout_seconds=0)
    with pytest.raises(ValidationError):
        CheckSpec(id="unit", command=["pytest"], invented=True)
    with pytest.raises(ValidationError):
        _contract(source_prompt_hash="not-a-sha256")


def test_prompt_derived_acceptance_stays_draft_until_a_trusted_contract_replaces_it() -> None:
    draft = _contract(
        source=ContractSource.PROMPT_DRAFT,
        acceptance_state=AcceptanceState.DRAFT,
    )
    assert draft.acceptance_state is AcceptanceState.DRAFT

    with pytest.raises(ValidationError, match="prompt-derived acceptance"):
        _contract(
            source=ContractSource.PROMPT_DRAFT,
            acceptance_state=AcceptanceState.CONFIRMED,
        )


def test_baseline_run_evidence_and_verdict_records_compose_without_raw_logs() -> None:
    result = CheckResult(
        check_id="unit",
        status=CheckStatus.PASSED,
        started_at=NOW,
        completed_at=NOW,
        exit_code=0,
        isolation=IsolationLevel.SANDBOXED,
        worktree_hash="c" * 64,
        artifact_ids=["artifact-1"],
    )
    baseline = Baseline(
        baseline_id="baseline-1",
        captured_at=NOW,
        repository_sha="d" * 40,
        worktree_hash="c" * 64,
        dirty_manifest=["M src/auth.py"],
        untracked_manifest=["notes.txt"],
        owning_session_id="session-1",
        source_prompt_event_id="event-1",
        captured_before_first_mutation=True,
        results=[result],
    )
    evidence = EvidenceArtifact(
        artifact_id="artifact-1",
        sha256="e" * 64,
        size_bytes=12,
        media_type="text/plain",
        retention_class="raw_logs",
        created_at=NOW,
    )
    verdict = VerificationVerdict(
        status=VerdictStatus.VERIFIED,
        evidence_ids=[evidence.artifact_id],
    )
    run = VerificationRun(
        run_id="run-1",
        contract=_contract(),
        status=RunStatus.COMPLETED,
        baseline=baseline,
        results=[result],
        evidence=[evidence],
        verdict=verdict,
        created_at=NOW,
        updated_at=NOW,
    )

    assert run.baseline.captured_before_first_mutation is True
    assert run.verdict is not None
    assert run.verdict.status is VerdictStatus.VERIFIED
    assert "pytest output" not in run.model_dump_json()
