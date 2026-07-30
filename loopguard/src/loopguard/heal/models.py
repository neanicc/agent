"""Immutable contracts for the pipeline-repair state machine."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GIT_SHA_PATTERN = r"^[0-9a-f]{40,64}$"


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


class RepairModel(BaseModel):
    """Strict value object suitable for durable workflow serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class FailureEvent(RepairModel):
    failure_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    source: Literal["airflow", "openlineage", "github_actions", "webhook"]
    repo_id: str = Field(min_length=1, max_length=256, pattern=_IDENTIFIER_PATTERN)
    revision: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    pipeline: str = Field(min_length=1, max_length=256)
    step: str = Field(min_length=1, max_length=256)
    error_type: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=16_384)
    stack_trace_artifact_id: str | None = Field(
        default=None, min_length=1, max_length=256, pattern=_IDENTIFIER_PATTERN
    )
    schema_artifact_ids: tuple[str, ...] = Field(default=(), max_length=64)
    fixture_artifact_id: str | None = Field(
        default=None, min_length=1, max_length=256, pattern=_IDENTIFIER_PATTERN
    )
    tenant_id: str = Field(
        default="local", min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def fixture(cls, **updates: object) -> Self:
        """Return bounded deterministic defaults for tests and local examples."""

        values: dict[str, object] = {
            "failure_id": "failure-1",
            "source": "airflow",
            "repo_id": "repo-1",
            "revision": "a" * 40,
            "pipeline": "coordinate-import",
            "step": "normalize",
            "error_type": "TypeError",
            "message": "could not convert coordinate",
            "stack_trace_artifact_id": "artifact-stack",
            "schema_artifact_ids": ("artifact-schema",),
            "fixture_artifact_id": "artifact-fixture",
        }
        values.update(updates)
        return cls.model_validate(values)


class CandidatePatch(RepairModel):
    candidate_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    base_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    patch_artifact_id: str = Field(min_length=1, max_length=256, pattern=_IDENTIFIER_PATTERN)
    patch_sha256: str = Field(pattern=_SHA256_PATTERN)
    changed_files: tuple[str, ...] = Field(default=(), max_length=64)
    changed_lines: int = Field(ge=0, le=10_000)
    strategy: str = Field(default="unspecified", min_length=1, max_length=256)
    generator_evidence_artifact_ids: tuple[str, ...] = Field(default=(), max_length=64)


class DataContractDelta(RepairModel):
    changes: tuple[dict[str, str], ...] = Field(default=(), max_length=512)
    breaking: bool = False


class CandidateEvaluation(RepairModel):
    candidate_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    replay_passed: bool = False
    regression_passed: bool = False
    security_passed: bool = False
    required_inconclusive: bool = False
    contract_delta: DataContractDelta = Field(default_factory=DataContractDelta)
    evidence_artifact_ids: tuple[str, ...] = Field(default=(), max_length=128)
    verification_duration_ms: int = Field(default=0, ge=0, le=86_400_000)
    rejection_reason: str | None = Field(default=None, min_length=1, max_length=512)

    @property
    def verified(self) -> bool:
        return (
            self.replay_passed
            and self.regression_passed
            and self.security_passed
            and not self.required_inconclusive
            and not self.contract_delta.breaking
            and self.rejection_reason is None
        )


class PublicationRecord(RepairModel):
    repository_id: str = Field(min_length=1, max_length=256, pattern=_IDENTIFIER_PATTERN)
    installation_id: int = Field(gt=0)
    branch: str = Field(min_length=1, max_length=255, pattern=_IDENTIFIER_PATTERN)
    base_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    head_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    patch_sha256: str = Field(pattern=_SHA256_PATTERN)
    pull_request_number: int = Field(gt=0)
    pull_request_url: HttpUrl
    published_at: datetime


_ALLOWED_TRANSITIONS: dict[RepairState, frozenset[RepairState]] = {
    RepairState.INTAKE: frozenset(
        {RepairState.REPRODUCING, RepairState.FAILED, RepairState.CANCELLED}
    ),
    RepairState.REPRODUCING: frozenset(
        {
            RepairState.NOT_REPRODUCIBLE,
            RepairState.PLANNING,
            RepairState.FAILED,
            RepairState.CANCELLED,
        }
    ),
    RepairState.NOT_REPRODUCIBLE: frozenset(),
    RepairState.PLANNING: frozenset(
        {RepairState.GENERATING, RepairState.FAILED, RepairState.CANCELLED}
    ),
    RepairState.GENERATING: frozenset(
        {RepairState.EVALUATING, RepairState.FAILED, RepairState.CANCELLED}
    ),
    RepairState.EVALUATING: frozenset(
        {RepairState.RANKED, RepairState.FAILED, RepairState.CANCELLED}
    ),
    RepairState.RANKED: frozenset(
        {RepairState.AWAITING_PUBLICATION, RepairState.FAILED, RepairState.CANCELLED}
    ),
    RepairState.AWAITING_PUBLICATION: frozenset(
        {RepairState.PUBLISHING, RepairState.FAILED, RepairState.CANCELLED}
    ),
    RepairState.PUBLISHING: frozenset(
        {RepairState.COMPLETED, RepairState.FAILED, RepairState.CANCELLED}
    ),
    RepairState.COMPLETED: frozenset(),
    RepairState.FAILED: frozenset(),
    RepairState.CANCELLED: frozenset(),
}

_REPRODUCTION_REQUIRED = frozenset(
    {
        RepairState.PLANNING,
        RepairState.GENERATING,
        RepairState.EVALUATING,
        RepairState.RANKED,
        RepairState.AWAITING_PUBLICATION,
        RepairState.PUBLISHING,
        RepairState.COMPLETED,
    }
)


class RepairRun(RepairModel):
    repair_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    failure: FailureEvent
    state: RepairState = RepairState.INTAKE
    state_version: int = Field(default=0, ge=0)
    reproduced: bool = False
    reproduction_artifact_id: str | None = Field(
        default=None, min_length=1, max_length=256, pattern=_IDENTIFIER_PATTERN
    )
    candidates: tuple[CandidatePatch, ...] = Field(default=(), max_length=16)
    evaluations: tuple[CandidateEvaluation, ...] = Field(default=(), max_length=16)
    winning_candidate_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    publication: PublicationRecord | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def fixture(cls, **updates: object) -> Self:
        values: dict[str, object] = {
            "repair_id": "repair-1",
            "failure": FailureEvent.fixture(),
        }
        values.update(updates)
        return cls.model_validate(values)

    def transition(
        self,
        target: RepairState,
        *,
        publication: PublicationRecord | None = None,
    ) -> Self:
        """Return the next immutable state after enforcing safety prerequisites."""

        if target is RepairState.COMPLETED and publication is None and self.publication is None:
            raise ValueError("publication record required before completion")

        if (
            target
            in {
                RepairState.AWAITING_PUBLICATION,
                RepairState.PUBLISHING,
                RepairState.COMPLETED,
            }
            and self._verified_candidate() is None
        ):
            raise ValueError("verified candidate required before publication")

        if target in _REPRODUCTION_REQUIRED and (
            not self.reproduced or self.reproduction_artifact_id is None
        ):
            raise ValueError("reproduction required before advancing repair")

        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"invalid repair transition: {self.state.value} -> {target.value}")

        effective_publication = publication if publication is not None else self.publication
        return self.model_copy(
            update={
                "state": target,
                "state_version": self.state_version + 1,
                "publication": effective_publication,
                "updated_at": datetime.now(UTC),
            }
        )

    def _verified_candidate(self) -> CandidatePatch | None:
        if self.winning_candidate_id is None:
            return None
        candidate = next(
            (item for item in self.candidates if item.candidate_id == self.winning_candidate_id),
            None,
        )
        evaluation = next(
            (item for item in self.evaluations if item.candidate_id == self.winning_candidate_id),
            None,
        )
        if candidate is None or evaluation is None or not evaluation.verified:
            return None
        return candidate
