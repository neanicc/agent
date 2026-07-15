from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    model_validator,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _nonempty(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return normalized


def _sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError("value must be a lowercase SHA-256 digest")
    return normalized


def _git_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not _GIT_SHA.fullmatch(normalized):
        raise ValueError("repository SHA must be a full SHA-1 or SHA-256 digest")
    return normalized


NonEmptyStr = Annotated[str, AfterValidator(_nonempty)]
Sha256 = Annotated[str, AfterValidator(_sha256)]
GitSha = Annotated[str, AfterValidator(_git_sha)]


class CheckPhase(StrEnum):
    BASELINE = "baseline"
    IMPACTED = "impacted"
    COMPLETION = "completion"
    PR = "pr"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"


class ContractSource(StrEnum):
    REPOSITORY_CONFIG = "repository_config"
    MANAGED_RUN = "managed_run"
    CONTRACT_EVENT = "contract_event"
    PROMPT_DRAFT = "prompt_draft"


class AcceptanceState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class IsolationLevel(StrEnum):
    SANDBOXED = "sandboxed"
    UNSANDBOXED = "unsandboxed"


class RunStatus(StrEnum):
    CREATED = "created"
    BASELINING = "baselining"
    ACTIVE = "active"
    COMPLETING = "completing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    RECOVERING = "recovering"
    ORPHANED = "orphaned"


class VerdictStatus(StrEnum):
    VERIFIED = "verified"
    VERIFIED_WITH_PREEXISTING_FAILURES = "verified_with_preexisting_failures"
    CHECKS_PASSED_UNBASELINED = "checks_passed_unbaselined"
    REGRESSION = "regression"
    INCOMPLETE = "incomplete"
    INCONCLUSIVE = "inconclusive"
    ORPHANED = "orphaned"


class RetentionClass(StrEnum):
    SUMMARIES = "summaries"
    RAW_LOGS = "raw_logs"
    SCREENSHOTS_TRACES = "screenshots_traces"
    SENSITIVE = "sensitive"


class ProofModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckSpec(ProofModel):
    id: NonEmptyStr
    command: list[NonEmptyStr] = Field(min_length=1, max_length=256)
    cwd: NonEmptyStr = "."
    required: bool = True
    timeout_seconds: PositiveInt = Field(default=300, le=86_400)
    phase: CheckPhase = CheckPhase.COMPLETION


class ProofContract(ProofModel):
    task_id: NonEmptyStr
    source_event_id: NonEmptyStr
    source_prompt_hash: Sha256
    source: ContractSource
    source_hash: Sha256
    acceptance_state: AcceptanceState
    acceptance: list[NonEmptyStr] = Field(min_length=1, max_length=256)
    invariants: list[NonEmptyStr] = Field(default_factory=list, max_length=256)
    non_goals: list[NonEmptyStr] = Field(default_factory=list, max_length=256)
    changed_scope: list[NonEmptyStr] = Field(default_factory=list, max_length=4096)
    checks: list[CheckSpec] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def prompt_acceptance_cannot_confirm_itself(self) -> ProofContract:
        if (
            self.source is ContractSource.PROMPT_DRAFT
            and self.acceptance_state is AcceptanceState.CONFIRMED
        ):
            raise ValueError("prompt-derived acceptance must remain draft until trusted confirmation")
        check_ids = [check.id for check in self.checks]
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("proof contract check IDs must be unique")
        if not any(
            check.required and check.phase in {CheckPhase.COMPLETION, CheckPhase.PR}
            for check in self.checks
        ):
            raise ValueError("proof contract requires at least one required completion check")
        return self


class CheckResult(ProofModel):
    check_id: NonEmptyStr
    status: CheckStatus
    started_at: AwareDatetime
    completed_at: AwareDatetime
    exit_code: int | None = None
    failure_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=100_000)
    artifact_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=1024)
    isolation: IsolationLevel
    parser_error: NonEmptyStr | None = None
    worktree_hash: Sha256

    @model_validator(mode="after")
    def validate_result_timing_and_exit_status(self) -> CheckResult:
        if self.completed_at < self.started_at:
            raise ValueError("check completion cannot precede its start")
        if self.status is CheckStatus.PASSED and self.exit_code != 0:
            raise ValueError("a passed check requires exit code zero")
        if self.status is CheckStatus.TIMED_OUT and self.exit_code is not None:
            raise ValueError("a timed-out check cannot claim an exit code")
        return self


class Baseline(ProofModel):
    baseline_id: NonEmptyStr
    captured_at: AwareDatetime
    repository_sha: GitSha
    worktree_hash: Sha256
    dirty_manifest: list[NonEmptyStr] = Field(default_factory=list, max_length=100_000)
    untracked_manifest: list[NonEmptyStr] = Field(default_factory=list, max_length=100_000)
    owning_session_id: NonEmptyStr
    source_prompt_event_id: NonEmptyStr
    captured_before_first_mutation: bool
    results: list[CheckResult] = Field(default_factory=list, max_length=1024)


class EvidenceArtifact(ProofModel):
    artifact_id: NonEmptyStr
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: NonEmptyStr
    retention_class: RetentionClass
    created_at: AwareDatetime


class VerificationVerdict(ProofModel):
    status: VerdictStatus
    introduced_failures: list[NonEmptyStr] = Field(default_factory=list, max_length=100_000)
    resolved_failures: list[NonEmptyStr] = Field(default_factory=list, max_length=100_000)
    remaining_preexisting_failures: list[NonEmptyStr] = Field(
        default_factory=list,
        max_length=100_000,
    )
    missing_required_checks: list[NonEmptyStr] = Field(default_factory=list, max_length=1024)
    evidence_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=1024)


class VerificationRun(ProofModel):
    run_id: NonEmptyStr
    contract: ProofContract
    status: RunStatus
    baseline: Baseline | None = None
    results: list[CheckResult] = Field(default_factory=list, max_length=4096)
    evidence: list[EvidenceArtifact] = Field(default_factory=list, max_length=4096)
    verdict: VerificationVerdict | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    repo_seq: int = Field(default=0, ge=0)
    session_seq: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_run_timing(self) -> VerificationRun:
        if self.updated_at < self.created_at:
            raise ValueError("verification update cannot precede creation")
        if self.status is RunStatus.COMPLETED and self.verdict is None:
            raise ValueError("completed verification requires a verdict")
        return self
