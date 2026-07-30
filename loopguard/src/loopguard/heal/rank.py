"""Deterministic evidence-only repair ranking."""

from __future__ import annotations

from pydantic import Field

from loopguard.heal.models import CandidateEvaluation, RepairModel


class RankingResult(RepairModel):
    winner_id: str | None = Field(default=None, min_length=1, max_length=128)
    ordered: tuple[str, ...] = Field(default=(), max_length=16)
    rejected: dict[str, str] = Field(default_factory=dict, max_length=16)
    rationale: str = Field(min_length=1, max_length=512)


def rank_candidates(candidates: list[CandidateEvaluation]) -> RankingResult:
    if len(candidates) > 16:
        raise ValueError("at most 16 candidate evaluations can be ranked")
    identifiers = [candidate.candidate_id for candidate in candidates]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("candidate evaluations must have unique identities")
    rejected: dict[str, str] = {}
    eligible: list[CandidateEvaluation] = []
    for candidate in candidates:
        reason = _rejection(candidate)
        if reason is None:
            eligible.append(candidate)
        else:
            rejected[candidate.candidate_id] = reason
    eligible.sort(
        key=lambda candidate: (
            len(candidate.contract_delta.changes),
            candidate.risk_penalty,
            candidate.changed_files,
            candidate.changed_lines,
            candidate.verification_duration_ms,
            candidate.candidate_id,
        )
    )
    winner = eligible[0].candidate_id if eligible else None
    rationale = (
        "No candidate passed every required deterministic check."
        if winner is None
        else (
            f"{winner} won by contract compatibility, risk, changed files, changed lines, "
            "verification duration, then stable candidate ID."
        )
    )
    return RankingResult(
        winner_id=winner,
        ordered=tuple(candidate.candidate_id for candidate in eligible),
        rejected=dict(sorted(rejected.items())),
        rationale=rationale,
    )


def _rejection(candidate: CandidateEvaluation) -> str | None:
    if candidate.required_inconclusive:
        return "required_check_inconclusive"
    if not candidate.replay_passed:
        return "replay_failed"
    if not candidate.regression_passed:
        return "regression_failed"
    if not candidate.security_passed:
        return "security_failed"
    if candidate.contract_delta.breaking:
        return "contract_breaking"
    if candidate.rejection_reason is not None:
        return candidate.rejection_reason
    return None
