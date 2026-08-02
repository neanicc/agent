"""Sanitized, evidence-rich draft pull-request reporting."""

from __future__ import annotations

import html
import json
from typing import Self

from pydantic import Field, model_validator

from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    FailureEvent,
    RepairModel,
)
from loopguard.heal.rank import RankingResult


class RepairReport(RepairModel):
    repair_id: str = Field(min_length=1, max_length=128)
    failure: FailureEvent
    reproduction_command: tuple[str, ...] = Field(min_length=1, max_length=64)
    reproduction_artifact_id: str = Field(min_length=1, max_length=256)
    candidates: tuple[CandidatePatch, ...] = Field(min_length=1, max_length=16)
    evaluations: tuple[CandidateEvaluation, ...] = Field(min_length=1, max_length=16)
    ranking: RankingResult
    winning_candidate_id: str = Field(min_length=1, max_length=128)
    rollback: str = Field(min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def winner_has_verified_evidence(self) -> Self:
        candidate = next(
            (item for item in self.candidates if item.candidate_id == self.winning_candidate_id),
            None,
        )
        evaluation = next(
            (item for item in self.evaluations if item.candidate_id == self.winning_candidate_id),
            None,
        )
        if (
            candidate is None
            or evaluation is None
            or not evaluation.verified
            or self.ranking.winner_id != self.winning_candidate_id
        ):
            raise ValueError("repair report winner requires matching verified evidence")
        return self


def render_report(report: RepairReport) -> str:
    evaluations = {item.candidate_id: item for item in report.evaluations}
    winner = evaluations[report.winning_candidate_id]
    matrix: list[str] = [
        "| Candidate | Strategy | Replay | Regression | Security | Contract | Files | Lines |",
        "|---|---|---:|---:|---:|---|---:|---:|",
    ]
    for candidate in report.candidates:
        evaluation = evaluations.get(candidate.candidate_id)
        if evaluation is None:
            row = ("not evaluated", "—", "—", "unknown")
        else:
            row = (
                _yes(evaluation.replay_passed),
                _yes(evaluation.regression_passed),
                _yes(evaluation.security_passed),
                "breaking" if evaluation.contract_delta.breaking else "compatible",
            )
        matrix.append(
            "| "
            + " | ".join(
                (
                    _cell(candidate.candidate_id),
                    _cell(candidate.strategy),
                    *row,
                    str(len(candidate.changed_files)),
                    str(candidate.changed_lines),
                )
            )
            + " |"
        )
    delta = (
        "No contract changes detected."
        if not winner.contract_delta.changes
        else "\n".join(
            f"- `{_cell(change.get('path', 'unknown'))}`: "
            f"`{_cell(change.get('from', 'unknown'))}` → "
            f"`{_cell(change.get('to', 'unknown'))}`"
            for change in winner.contract_delta.changes
        )
    )
    evidence = "\n".join(f"- `{_cell(artifact)}`" for artifact in winner.evidence_artifact_ids)
    command = json.dumps(list(report.reproduction_command), ensure_ascii=True)
    text = f"""\
## LoopGuard verified pipeline repair

### Original reproduction

- Failure fingerprint: `{report.failure.fingerprint or "unavailable"}`
- Pipeline / step: `{_cell(report.failure.pipeline)}` / `{_cell(report.failure.step)}`
- Command (argument array): `{_cell(command)}`
- Evidence: `{_cell(report.reproduction_artifact_id)}`

### Candidate matrix

{chr(10).join(matrix)}

Winning rationale: {_plain(report.ranking.rationale)}

### Root cause and repair

The verified `{_cell(report.winning_candidate_id)}` candidate applies the
`{_cell(next(item.strategy for item in report.candidates if item.candidate_id == report.winning_candidate_id))}`
boundary strategy. The original failure reproduced before repair, and the same replay passed after
the patch.

### Data contract delta

{delta}

### Verification evidence

{evidence or "- No artifact IDs were reported."}

Verification duration: {winner.verification_duration_ms} ms. Risk penalty: {winner.risk_penalty}.
Winning patch: `{_cell(next(item.patch_artifact_id for item in report.candidates if item.candidate_id == report.winning_candidate_id))}`.

### Rollback

{_plain(report.rollback)}
"""
    if len(text.encode()) > 64 * 1024:
        raise ValueError("repair report exceeds the GitHub body budget")
    return text


def _yes(value: bool) -> str:
    return "pass" if value else "fail"


def _cell(value: str) -> str:
    return (
        html.escape(value, quote=True)
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("\n", " ")
    )


def _plain(value: str) -> str:
    return html.escape(value, quote=True).replace("\r", " ").replace("\n", " ")
