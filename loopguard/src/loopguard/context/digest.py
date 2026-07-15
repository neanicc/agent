from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Set
from itertools import islice

from pydantic import BaseModel, ConfigDict, Field

from .models import ChangeRecord


_FAILING_VERIFICATION_STATES = {
    "error",
    "failed",
    "inconclusive",
    "regression",
    "timed_out",
}


class DigestBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_chars: int = Field(default=4_000, ge=128, le=100_000)
    max_records: int = Field(default=10_000, ge=1, le=100_000)
    max_symbols_per_entry: int = Field(default=20, ge=0, le=1_000)
    max_verifications_per_entry: int = Field(default=20, ge=0, le=1_000)


class ContextDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    since_repo_seq: int = Field(ge=0, le=2**63 - 1)
    next_repo_seq: int = Field(ge=0, le=2**63 - 1)
    included_count: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    truncated: bool


class DigestBuilder:
    def build(
        self,
        records: Iterable[ChangeRecord],
        *,
        since: int,
        budget: DigestBudget,
        collision_paths: Set[str] = frozenset(),
        verification_statuses: Mapping[str, str] | None = None,
        direct_dependency_paths: Set[str] = frozenset(),
    ) -> ContextDigest:
        if not 0 <= since <= 2**63 - 1:
            raise ValueError("digest cursor is outside the supported range")
        budget = DigestBudget.model_validate(budget)
        bounded = list(islice(records, budget.max_records + 1))
        if len(bounded) > budget.max_records:
            raise ValueError("digest input exceeds the record limit")
        validated = [ChangeRecord.model_validate(record) for record in bounded]
        repo_ids = {record.repo_id for record in validated}
        if len(repo_ids) > 1:
            raise ValueError("digest records must belong to one repository")
        unique = _unique_records(validated)
        next_repo_seq = max(since, max((record.repo_seq for record in unique), default=since))
        statuses = _verification_statuses(verification_statuses or {})
        candidates = [record for record in unique if record.repo_seq > since]
        candidates.sort(
            key=lambda record: _priority(
                record,
                collision_paths=collision_paths,
                verification_statuses=statuses,
                direct_dependency_paths=direct_dependency_paths,
            )
        )

        header = f"LoopGuard context since={since} next={next_repo_seq}\n"
        lines: list[str] = []
        omitted = 0
        reserved_footer_length = len(_footer(len(candidates), len(candidates)))
        for record in candidates:
            payload = _entry(
                record,
                budget=budget,
                collision=record.path in collision_paths,
                dependency=record.path in direct_dependency_paths,
                verification_statuses=statuses,
            )
            line = "- " + json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            candidate_lines = [*lines, line]
            candidate_text = header + "\n".join(candidate_lines)
            if candidate_lines:
                candidate_text += "\n"
            if len(candidate_text) + reserved_footer_length <= budget.max_chars:
                lines.append(line)
            else:
                omitted += 1
        omitted += len(candidates) - len(lines) - omitted
        footer = _footer(len(lines), omitted)
        text = header + "\n".join(lines)
        if lines:
            text += "\n"
        text += footer
        if len(text) > budget.max_chars:
            raise ValueError("digest budget is too small for its cursor header")
        return ContextDigest(
            text=text,
            since_repo_seq=since,
            next_repo_seq=next_repo_seq,
            included_count=len(lines),
            omitted_count=omitted,
            truncated=omitted > 0,
        )


def _unique_records(records: list[ChangeRecord]) -> list[ChangeRecord]:
    unique: dict[str, ChangeRecord] = {}
    encoded: dict[str, str] = {}
    for record in records:
        value = record.model_dump_json()
        existing = encoded.get(record.record_id)
        if existing is not None and existing != value:
            raise ValueError("digest record ID has conflicting semantics")
        unique[record.record_id] = record
        encoded[record.record_id] = value
    return list(unique.values())


def _verification_statuses(values: Mapping[str, str]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for verification_id, status in values.items():
        normalized_id = str(verification_id).strip()
        normalized_status = str(status).strip().lower()
        if (
            not normalized_id
            or not normalized_status
            or len(normalized_id) > 512
            or len(normalized_status) > 64
            or "\x00" in normalized_id
            or "\x00" in normalized_status
        ):
            raise ValueError("verification status entries must be bounded and non-empty")
        statuses[normalized_id] = normalized_status
    return statuses


def _priority(
    record: ChangeRecord,
    *,
    collision_paths: Set[str],
    verification_statuses: Mapping[str, str],
    direct_dependency_paths: Set[str],
) -> tuple[int, int, int, int, str, str]:
    failing = any(
        verification_statuses.get(verification_id, "unknown")
        in _FAILING_VERIFICATION_STATES
        for verification_id in record.verification_ids
    )
    return (
        -int(record.path in collision_paths),
        -int(failing),
        -int(record.path in direct_dependency_paths),
        -record.repo_seq,
        record.path,
        record.record_id,
    )


def _entry(
    record: ChangeRecord,
    *,
    budget: DigestBudget,
    collision: bool,
    dependency: bool,
    verification_statuses: Mapping[str, str],
) -> dict[str, object]:
    symbols = sorted(record.symbols)
    verification_ids = sorted(record.verification_ids)
    return {
        "actor": record.actor,
        "collision": collision,
        "dependency": dependency,
        "path": record.path,
        "repo_seq": record.repo_seq,
        "symbols": symbols[: budget.max_symbols_per_entry],
        "symbols_omitted": max(0, len(symbols) - budget.max_symbols_per_entry),
        "verification": [
            {
                "id": verification_id,
                "status": verification_statuses.get(verification_id, "unknown"),
            }
            for verification_id in verification_ids[: budget.max_verifications_per_entry]
        ],
        "verifications_omitted": max(
            0, len(verification_ids) - budget.max_verifications_per_entry
        ),
    }


def _footer(included: int, omitted: int) -> str:
    return f"Summary included={included} omitted={omitted}"
