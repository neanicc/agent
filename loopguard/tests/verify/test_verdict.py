from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from loopguard.verify.models import (
    AcceptanceState,
    Baseline,
    CheckPhase,
    CheckResult,
    CheckSpec,
    CheckStatus,
    ContractSource,
    IsolationLevel,
    ProofContract,
    VerdictStatus,
)
from loopguard.verify.verdict import compare_results, derive_verdict


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _result(
    failures: list[str] | None = None,
    *,
    check_id: str = "unit",
    status: CheckStatus | None = None,
    artifacts: list[str] | None = None,
    isolation: IsolationLevel = IsolationLevel.SANDBOXED,
    parser_error: str | None = None,
) -> CheckResult:
    failure_ids = list(failures or [])
    resolved_status = status or (CheckStatus.FAILED if failure_ids else CheckStatus.PASSED)
    exit_code = 0 if resolved_status is CheckStatus.PASSED else 1
    if resolved_status in {CheckStatus.TIMED_OUT, CheckStatus.INCONCLUSIVE, CheckStatus.SKIPPED}:
        exit_code = None
    return CheckResult(
        check_id=check_id,
        status=resolved_status,
        started_at=NOW,
        completed_at=NOW,
        exit_code=exit_code,
        failure_ids=failure_ids,
        artifact_ids=list(artifacts if artifacts is not None else [f"artifact-{check_id}"]),
        isolation=isolation,
        parser_error=parser_error,
        worktree_hash="a" * 64,
    )


def _contract(*, acceptance_state: AcceptanceState = AcceptanceState.CONFIRMED) -> ProofContract:
    return ProofContract(
        task_id="task-1",
        source_event_id="prompt-1",
        source_prompt_hash="b" * 64,
        source=ContractSource.CONTRACT_EVENT,
        source_hash="c" * 64,
        acceptance_state=acceptance_state,
        acceptance=["The change satisfies the request"],
        invariants=["Existing behavior remains stable"],
        checks=[
            CheckSpec(id="unit", command=["pytest", "-q"]),
            CheckSpec(
                id="optional-lint",
                command=["ruff", "check", "."],
                required=False,
            ),
        ],
    )


def _baseline(
    *results: CheckResult,
    event_id: str = "prompt-1",
    ordered: bool = True,
) -> Baseline:
    return Baseline(
        baseline_id="baseline-1",
        captured_at=NOW,
        repository_sha="d" * 40,
        worktree_hash="a" * 64,
        owning_session_id="session-1",
        source_prompt_event_id=event_id,
        captured_before_first_mutation=ordered,
        results=list(results),
    )


@pytest.mark.parametrize(
    ("baseline", "current", "expected"),
    [
        (["old"], ["old"], VerdictStatus.VERIFIED_WITH_PREEXISTING_FAILURES),
        (["old"], [], VerdictStatus.VERIFIED),
        (["old"], ["old", "new"], VerdictStatus.REGRESSION),
        ([], ["new"], VerdictStatus.REGRESSION),
        ([], [], VerdictStatus.VERIFIED),
    ],
)
def test_failure_set_matrix(
    baseline: list[str],
    current: list[str],
    expected: VerdictStatus,
) -> None:
    verdict = compare_results(_result(baseline), _result(current))

    assert verdict.status is expected
    assert verdict.introduced_failures == sorted(set(current) - set(baseline))
    assert verdict.resolved_failures == sorted(set(baseline) - set(current))
    assert verdict.remaining_preexisting_failures == sorted(set(baseline) & set(current))


def test_aggregate_verdict_requires_acceptance_evidence_and_result_artifacts() -> None:
    contract = _contract()
    baseline = _baseline(_result([]))
    current = _result([])

    missing_acceptance = derive_verdict(contract, baseline, [current])
    missing_artifact = derive_verdict(
        contract,
        baseline,
        [_result([], artifacts=[])],
        acceptance_evidence_ids=["acceptance-1"],
    )
    verified = derive_verdict(
        contract,
        baseline,
        [current],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert missing_acceptance.status is VerdictStatus.INCOMPLETE
    assert missing_artifact.status is VerdictStatus.INCOMPLETE
    assert verified.status is VerdictStatus.VERIFIED
    assert verified.evidence_ids == ["acceptance-1", "artifact-unit"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CheckStatus.TIMED_OUT, VerdictStatus.INCONCLUSIVE),
        (CheckStatus.INCONCLUSIVE, VerdictStatus.INCONCLUSIVE),
        (CheckStatus.SKIPPED, VerdictStatus.INCOMPLETE),
    ],
)
def test_non_final_required_results_can_never_verify(
    status: CheckStatus,
    expected: VerdictStatus,
) -> None:
    verdict = derive_verdict(
        _contract(),
        _baseline(_result([])),
        [_result(status=status)],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert verdict.status is expected
    assert verdict.missing_required_checks == ["unit"]


def test_missing_or_duplicate_required_result_is_not_silently_selected() -> None:
    contract = _contract()
    baseline = _baseline(_result([]))

    missing = derive_verdict(
        contract,
        baseline,
        [],
        acceptance_evidence_ids=["acceptance-1"],
    )
    duplicate = derive_verdict(
        contract,
        baseline,
        [_result([]), _result([])],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert missing.status is VerdictStatus.INCOMPLETE
    assert duplicate.status is VerdictStatus.INCONCLUSIVE
    assert missing.missing_required_checks == duplicate.missing_required_checks == ["unit"]


def test_passing_checks_with_missing_or_late_baseline_are_explicitly_unbaselined() -> None:
    contract = _contract()
    current = [_result([])]

    missing = derive_verdict(
        contract,
        None,
        current,
        acceptance_evidence_ids=["acceptance-1"],
    )
    late = derive_verdict(
        contract,
        _baseline(_result([]), ordered=False),
        current,
        acceptance_evidence_ids=["acceptance-1"],
    )
    wrong_prompt = derive_verdict(
        contract,
        _baseline(_result([]), event_id="different-prompt"),
        current,
        acceptance_evidence_ids=["acceptance-1"],
    )

    expected = VerdictStatus.CHECKS_PASSED_UNBASELINED
    assert missing.status is late.status is wrong_prompt.status is expected


def test_weak_isolation_or_parser_ambiguity_is_inconclusive() -> None:
    contract = _contract()
    baseline = _baseline(_result([]))
    unsandboxed = derive_verdict(
        contract,
        baseline,
        [_result([], isolation=IsolationLevel.UNSANDBOXED)],
        acceptance_evidence_ids=["acceptance-1"],
    )
    parser_error = derive_verdict(
        contract,
        baseline,
        [_result([], parser_error="malformed_junit")],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert unsandboxed.status is parser_error.status is VerdictStatus.INCONCLUSIVE


def test_new_failure_wins_over_other_incomplete_checks_and_is_never_preexisting() -> None:
    contract = _contract().model_copy(
        update={
            "checks": [
                CheckSpec(id="unit", command=["pytest", "-q"]),
                CheckSpec(
                    id="typecheck",
                    command=["npm", "run", "typecheck"],
                    phase=CheckPhase.PR,
                ),
            ]
        }
    )
    baseline = _baseline(_result([], check_id="unit"), _result([], check_id="typecheck"))

    verdict = derive_verdict(
        contract,
        baseline,
        [_result(["tests/test_new.py::test_new"], check_id="unit")],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert verdict.status is VerdictStatus.REGRESSION
    assert verdict.introduced_failures == ["tests/test_new.py::test_new"]
    assert verdict.missing_required_checks == ["typecheck"]


def test_unchanged_generic_command_failure_stays_inconclusive_without_failure_ids() -> None:
    failed = _result([], status=CheckStatus.FAILED)

    verdict = derive_verdict(
        _contract(),
        _baseline(failed),
        [failed],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert verdict.status is VerdictStatus.INCONCLUSIVE


def test_optional_failure_does_not_weaken_a_complete_required_proof() -> None:
    verdict = derive_verdict(
        _contract(),
        _baseline(_result([])),
        [_result([]), _result(["lint"], check_id="optional-lint")],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert verdict.status is VerdictStatus.VERIFIED


def test_draft_acceptance_contract_cannot_produce_verified() -> None:
    verdict = derive_verdict(
        _contract(acceptance_state=AcceptanceState.DRAFT),
        _baseline(_result([])),
        [_result([])],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert verdict.status is VerdictStatus.INCOMPLETE


def test_baseline_hash_or_temporal_mismatch_cannot_claim_pre_mutation_ownership() -> None:
    contract = _contract()
    current = _result([])
    wrong_hash_result = _result([]).model_copy(update={"worktree_hash": "e" * 64})
    wrong_hash = derive_verdict(
        contract,
        _baseline(wrong_hash_result),
        [current],
        acceptance_evidence_ids=["acceptance-1"],
    )
    late_result = _result([]).model_copy(
        update={"completed_at": NOW + timedelta(seconds=1)}
    )
    temporally_late = derive_verdict(
        contract,
        _baseline(late_result),
        [current],
        acceptance_evidence_ids=["acceptance-1"],
    )

    expected = VerdictStatus.CHECKS_PASSED_UNBASELINED
    assert wrong_hash.status is temporally_late.status is expected


def test_required_results_from_different_worktrees_are_inconclusive() -> None:
    contract = _contract().model_copy(
        update={
            "checks": [
                CheckSpec(id="unit", command=["pytest", "-q"]),
                CheckSpec(id="typecheck", command=["npm", "run", "typecheck"]),
            ]
        }
    )
    baseline = _baseline(_result([]), _result([], check_id="typecheck"))
    changed_tree = _result([], check_id="typecheck").model_copy(
        update={"worktree_hash": "f" * 64}
    )

    verdict = derive_verdict(
        contract,
        baseline,
        [_result([]), changed_tree],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert verdict.status is VerdictStatus.INCONCLUSIVE


def test_structurally_contradictory_pass_payload_is_inconclusive() -> None:
    contradictory = _result([]).model_copy(update={"failure_ids": ["impossible"]})

    verdict = derive_verdict(
        _contract(),
        _baseline(_result([])),
        [contradictory],
        acceptance_evidence_ids=["acceptance-1"],
    )

    assert verdict.status is VerdictStatus.INCONCLUSIVE
