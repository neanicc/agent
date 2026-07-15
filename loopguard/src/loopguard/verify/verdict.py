from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .models import (
    AcceptanceState,
    Baseline,
    CheckPhase,
    CheckResult,
    CheckStatus,
    IsolationLevel,
    ProofContract,
    VerificationVerdict,
    VerdictStatus,
)


_FINAL_STATUSES = {CheckStatus.PASSED, CheckStatus.FAILED}


def compare_results(
    baseline: CheckResult,
    current: CheckResult,
) -> VerificationVerdict:
    """Compare one exact check without applying aggregate acceptance requirements."""
    if baseline.check_id != current.check_id:
        raise ValueError("baseline and current result IDs must match")
    evidence = sorted(set(baseline.artifact_ids + current.artifact_ids))
    if (
        baseline.isolation is IsolationLevel.UNSANDBOXED
        or current.isolation is IsolationLevel.UNSANDBOXED
        or baseline.parser_error is not None
        or current.parser_error is not None
        or _result_is_ambiguous(baseline)
        or _result_is_ambiguous(current)
    ):
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            missing=[current.check_id],
            evidence=evidence,
        )
    if current.status in {CheckStatus.TIMED_OUT, CheckStatus.INCONCLUSIVE}:
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            missing=[current.check_id],
            evidence=evidence,
        )
    if current.status is CheckStatus.SKIPPED:
        return _verdict(
            VerdictStatus.INCOMPLETE,
            missing=[current.check_id],
            evidence=evidence,
        )
    if baseline.status not in _FINAL_STATUSES:
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            missing=[current.check_id],
            evidence=evidence,
        )

    baseline_failures = set(baseline.failure_ids)
    current_failures = set(current.failure_ids)
    if baseline.status is CheckStatus.FAILED and not baseline_failures:
        if current.status is CheckStatus.FAILED:
            return _verdict(
                VerdictStatus.INCONCLUSIVE,
                missing=[current.check_id],
                evidence=evidence,
            )
        baseline_failures.add(_generic_failure_id(baseline))
    if current.status is CheckStatus.FAILED and not current_failures:
        if baseline.status is CheckStatus.FAILED:
            return _verdict(
                VerdictStatus.INCONCLUSIVE,
                missing=[current.check_id],
                evidence=evidence,
            )
        current_failures.add(_generic_failure_id(current))

    introduced = sorted(current_failures - baseline_failures)
    resolved = sorted(baseline_failures - current_failures)
    remaining = sorted(baseline_failures & current_failures)
    if introduced:
        status = VerdictStatus.REGRESSION
    elif remaining:
        status = VerdictStatus.VERIFIED_WITH_PREEXISTING_FAILURES
    else:
        status = VerdictStatus.VERIFIED
    return _verdict(
        status,
        introduced=introduced,
        resolved=resolved,
        remaining=remaining,
        evidence=evidence,
    )


def derive_verdict(
    contract: ProofContract,
    baseline: Baseline | None,
    current_results: Sequence[CheckResult],
    *,
    acceptance_evidence_ids: Sequence[str] = (),
) -> VerificationVerdict:
    required = {
        check.id
        for check in contract.checks
        if check.required and check.phase in {CheckPhase.COMPLETION, CheckPhase.PR}
    }
    current_by_id = _group_results(current_results)
    baseline_by_id = _group_results(baseline.results if baseline is not None else [])
    missing = sorted(
        check_id
        for check_id in required
        if len(current_by_id.get(check_id, [])) != 1
        or current_by_id[check_id][0].status not in _FINAL_STATUSES
    )
    duplicate_current = any(len(current_by_id.get(check_id, [])) > 1 for check_id in required)
    available_current = [
        current_by_id[check_id][0]
        for check_id in sorted(required)
        if len(current_by_id.get(check_id, [])) == 1
    ]
    evidence = set(acceptance_evidence_ids)
    for result in available_current:
        evidence.update(result.artifact_ids)

    comparisons: list[VerificationVerdict] = []
    baseline_ordered = (
        baseline is not None
        and baseline.captured_before_first_mutation
        and baseline.source_prompt_event_id == contract.source_event_id
        and all(
            result.completed_at <= current.started_at
            for result in baseline.results
            for current in available_current
        )
    )
    baseline_classifiable = baseline_ordered and all(
        len(baseline_by_id.get(check_id, [])) == 1
        and baseline_by_id[check_id][0].status in _FINAL_STATUSES
        and baseline_by_id[check_id][0].worktree_hash == baseline.worktree_hash
        for check_id in required
    )
    if baseline_classifiable:
        for check_id in sorted(required):
            baseline_result = baseline_by_id[check_id][0]
            evidence.update(baseline_result.artifact_ids)
            if len(current_by_id.get(check_id, [])) == 1:
                comparisons.append(
                    compare_results(baseline_result, current_by_id[check_id][0])
                )

    introduced = sorted(
        {failure for verdict in comparisons for failure in verdict.introduced_failures}
    )
    resolved = sorted(
        {failure for verdict in comparisons for failure in verdict.resolved_failures}
    )
    remaining = sorted(
        {
            failure
            for verdict in comparisons
            for failure in verdict.remaining_preexisting_failures
        }
    )
    if introduced:
        return _verdict(
            VerdictStatus.REGRESSION,
            introduced=introduced,
            resolved=resolved,
            remaining=remaining,
            missing=missing,
            evidence=sorted(evidence),
        )

    if duplicate_current:
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            resolved=resolved,
            remaining=remaining,
            missing=missing,
            evidence=sorted(evidence),
        )
    if (
        len({result.worktree_hash for result in available_current}) > 1
        or any(_result_is_ambiguous(result) for result in available_current)
    ):
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            resolved=resolved,
            remaining=remaining,
            missing=missing,
            evidence=sorted(evidence),
        )
    if any(
        result.status in {CheckStatus.TIMED_OUT, CheckStatus.INCONCLUSIVE}
        or result.parser_error is not None
        or result.isolation is IsolationLevel.UNSANDBOXED
        for result in available_current
    ):
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            resolved=resolved,
            remaining=remaining,
            missing=missing,
            evidence=sorted(evidence),
        )
    if missing:
        return _verdict(
            VerdictStatus.INCOMPLETE,
            resolved=resolved,
            remaining=remaining,
            missing=missing,
            evidence=sorted(evidence),
        )
    if not baseline_classifiable:
        status = (
            VerdictStatus.CHECKS_PASSED_UNBASELINED
            if all(result.status is CheckStatus.PASSED for result in available_current)
            else VerdictStatus.INCONCLUSIVE
        )
        return _verdict(status, evidence=sorted(evidence))
    if any(verdict.status is VerdictStatus.INCONCLUSIVE for verdict in comparisons):
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            resolved=resolved,
            remaining=remaining,
            evidence=sorted(evidence),
        )

    baseline_results = [baseline_by_id[check_id][0] for check_id in sorted(required)]
    if any(result.isolation is IsolationLevel.UNSANDBOXED for result in baseline_results):
        return _verdict(
            VerdictStatus.INCONCLUSIVE,
            resolved=resolved,
            remaining=remaining,
            evidence=sorted(evidence),
        )
    artifacts_complete = all(
        result.artifact_ids for result in [*baseline_results, *available_current]
    )
    acceptance_complete = (
        contract.acceptance_state is AcceptanceState.CONFIRMED
        and bool(acceptance_evidence_ids)
    )
    if not artifacts_complete or not acceptance_complete:
        return _verdict(
            VerdictStatus.INCOMPLETE,
            resolved=resolved,
            remaining=remaining,
            evidence=sorted(evidence),
        )
    status = (
        VerdictStatus.VERIFIED_WITH_PREEXISTING_FAILURES
        if remaining
        else VerdictStatus.VERIFIED
    )
    return _verdict(
        status,
        resolved=resolved,
        remaining=remaining,
        evidence=sorted(evidence),
    )


def _group_results(results: Sequence[CheckResult]) -> dict[str, list[CheckResult]]:
    grouped: dict[str, list[CheckResult]] = defaultdict(list)
    for result in results:
        grouped[result.check_id].append(result)
    return grouped


def _generic_failure_id(result: CheckResult) -> str:
    return f"check:{result.check_id}:exit_failure"


def _result_is_ambiguous(result: CheckResult) -> bool:
    return (
        result.status is CheckStatus.PASSED
        and bool(result.failure_ids)
        or result.status is CheckStatus.FAILED
        and (result.exit_code is None or result.exit_code == 0)
    )


def _verdict(
    status: VerdictStatus,
    *,
    introduced: Sequence[str] = (),
    resolved: Sequence[str] = (),
    remaining: Sequence[str] = (),
    missing: Sequence[str] = (),
    evidence: Sequence[str] = (),
) -> VerificationVerdict:
    return VerificationVerdict(
        status=status,
        introduced_failures=sorted(set(introduced)),
        resolved_failures=sorted(set(resolved)),
        remaining_preexisting_failures=sorted(set(remaining)),
        missing_required_checks=sorted(set(missing)),
        evidence_ids=sorted(set(evidence)),
    )
