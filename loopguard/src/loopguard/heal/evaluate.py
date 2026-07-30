"""Ordered, fail-closed evaluation of generated pipeline repairs."""

from __future__ import annotations

import hashlib
import os
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from loopguard.heal.candidates import CandidateBudget, PatchPolicy, validate_unified_patch
from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    DataContractDelta,
    RepairModel,
)
from loopguard.heal.sandbox import WorktreeCheckout
from loopguard.verify.isolation import IsolationProvider
from loopguard.verify.models import (
    CheckPhase,
    CheckSpec,
    CheckStatus as VerificationCheckStatus,
)
from loopguard.verify.runner import CommandRunner, ExecutionAuthorization


class EvaluationStage(StrEnum):
    REPRODUCTION = "reproduction"
    FIXTURE_SCHEMA = "fixture_schema"
    DATA_QUALITY = "data_quality"
    IMPACTED_TESTS = "impacted_tests"
    SECURITY = "security"
    STATIC = "static"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INCONCLUSIVE = "inconclusive"


class EvaluationCheck(RepairModel):
    check_id: str = Field(min_length=1, max_length=128)
    stage: EvaluationStage
    command: tuple[str, ...] = Field(min_length=1, max_length=64)
    cwd: str = Field(default=".", min_length=1, max_length=512)
    timeout_seconds: int = Field(default=300, ge=1, le=3_600)
    required: bool = True

    @model_validator(mode="after")
    def direct_bounded_command(self) -> EvaluationCheck:
        shells = {"bash", "cmd", "dash", "fish", "powershell", "pwsh", "sh", "zsh"}
        if Path(self.command[0]).name.lower() in shells:
            raise ValueError("evaluation checks cannot invoke a shell")
        if any(
            not value or len(value) > 4_096 or "\x00" in value or "\n" in value or "\r" in value
            for value in self.command
        ):
            raise ValueError("evaluation command is invalid")
        cwd = Path(self.cwd)
        if cwd.is_absolute() or ".." in cwd.parts:
            raise ValueError("evaluation cwd must stay inside the worktree")
        if not self.required:
            raise ValueError("repair evaluation plan checks must be required")
        return self


class EvaluationPlan(RepairModel):
    reproduction: EvaluationCheck
    fixture_checks: tuple[EvaluationCheck, ...] = Field(min_length=1, max_length=64)
    data_quality_checks: tuple[EvaluationCheck, ...] = Field(min_length=1, max_length=64)
    impacted_checks: tuple[EvaluationCheck, ...] = Field(min_length=1, max_length=128)
    security_checks: tuple[EvaluationCheck, ...] = Field(min_length=1, max_length=64)
    static_checks: tuple[EvaluationCheck, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def ordered_stage_contract(self) -> EvaluationPlan:
        expected = (
            ((self.reproduction,), EvaluationStage.REPRODUCTION),
            (
                self.fixture_checks,
                EvaluationStage.FIXTURE_SCHEMA,
            ),
            (
                self.data_quality_checks,
                EvaluationStage.DATA_QUALITY,
            ),
            (
                self.impacted_checks,
                EvaluationStage.IMPACTED_TESTS,
            ),
            (
                self.security_checks,
                EvaluationStage.SECURITY,
            ),
            (
                self.static_checks,
                EvaluationStage.STATIC,
            ),
        )
        identifiers: list[str] = []
        for checks, stage in expected:
            if any(check.stage is not stage for check in checks):
                raise ValueError(f"{stage.value} plan contains a check for another stage")
            identifiers.extend(check.check_id for check in checks)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("evaluation check IDs must be unique")
        return self

    def ordered(self) -> tuple[EvaluationCheck, ...]:
        return (
            self.reproduction,
            *self.fixture_checks,
            *self.data_quality_checks,
            *self.impacted_checks,
            *self.security_checks,
            *self.static_checks,
        )


class CheckOutcome(RepairModel):
    check_id: str = Field(min_length=1, max_length=128)
    stage: EvaluationStage
    status: CheckStatus
    duration_ms: int = Field(ge=0, le=86_400_000)
    artifact_ids: tuple[str, ...] = Field(default=(), max_length=128)


class ContractAnalysis(RepairModel):
    delta: DataContractDelta
    duration_ms: int = Field(ge=0, le=86_400_000)
    artifact_ids: tuple[str, ...] = Field(default=(), max_length=128)


class EvaluationRecord(RepairModel):
    evaluation: CandidateEvaluation
    checks: tuple[CheckOutcome, ...] = ()


class PatchReader(Protocol):
    def read_patch(self, artifact_id: str, *, maximum_bytes: int) -> bytes: ...


class EvaluationRunner(Protocol):
    async def run(self, check: EvaluationCheck, *, worktree: Path) -> CheckOutcome: ...


class ContractAnalyzer(Protocol):
    async def analyze(self, *, worktree: Path) -> ContractAnalysis: ...


class BlobWriter(Protocol):
    def put_blob(self, payload: bytes, *, suffix: str = ".bin") -> str: ...


class IsolatedCommandEvaluationRunner:
    """Adapter over the existing offline/resource-bounded verification runner."""

    def __init__(
        self,
        *,
        python_executable: Path,
        artifacts: BlobWriter,
        isolation_provider: IsolationProvider | None = None,
    ) -> None:
        self.python_executable = python_executable
        self.artifacts = artifacts
        self.isolation_provider = isolation_provider

    async def run(self, check: EvaluationCheck, *, worktree: Path) -> CheckOutcome:
        runner = CommandRunner(
            worktree=worktree,
            python_executable=self.python_executable,
            isolation_provider=self.isolation_provider,
        )
        execution = await runner.run(
            CheckSpec(
                id=check.check_id,
                command=list(check.command),
                cwd=check.cwd,
                required=True,
                timeout_seconds=check.timeout_seconds,
                phase=CheckPhase.COMPLETION,
            ),
            authorization=ExecutionAuthorization(
                approved=True,
                allow_unsandboxed=False,
                network=False,
            ),
        )
        status = {
            VerificationCheckStatus.PASSED: CheckStatus.PASSED,
            VerificationCheckStatus.FAILED: CheckStatus.FAILED,
            VerificationCheckStatus.TIMED_OUT: CheckStatus.TIMED_OUT,
            VerificationCheckStatus.INCONCLUSIVE: CheckStatus.INCONCLUSIVE,
            VerificationCheckStatus.SKIPPED: CheckStatus.INCONCLUSIVE,
        }[execution.result.status]
        output_id = self.artifacts.put_blob(
            execution.stdout + b"\n--- stderr ---\n" + execution.stderr,
            suffix=".log",
        )
        duration = execution.result.completed_at - execution.result.started_at
        return CheckOutcome(
            check_id=check.check_id,
            stage=check.stage,
            status=status,
            duration_ms=max(0, int(duration.total_seconds() * 1_000)),
            artifact_ids=(output_id,),
        )


class CandidateEvaluator:
    def __init__(
        self,
        *,
        repository: Path,
        worktree_root: Path,
        patch_reader: PatchReader,
        runner: EvaluationRunner,
        contract_analyzer: ContractAnalyzer,
    ) -> None:
        self.repository = repository.expanduser().resolve(strict=True)
        self.worktree_root = worktree_root.expanduser().absolute()
        if self.worktree_root.is_symlink():
            raise ValueError("evaluation worktree root cannot be a symlink")
        if self.worktree_root.is_relative_to(self.repository) or self.repository.is_relative_to(
            self.worktree_root
        ):
            raise ValueError("evaluation worktrees cannot overlap the repository")
        self.worktree_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.worktree_root.resolve(strict=True) != self.worktree_root:
            raise ValueError("evaluation worktree root cannot contain a symlink")
        self.worktree_root.chmod(0o700)
        self.patch_reader = patch_reader
        self.runner = runner
        self.contract_analyzer = contract_analyzer

    async def evaluate(
        self,
        candidate: CandidatePatch,
        plan: EvaluationPlan,
    ) -> EvaluationRecord:
        try:
            patch = self.patch_reader.read_patch(
                candidate.patch_artifact_id,
                maximum_bytes=64 * 1024 * 1024,
            )
        except (OSError, ValueError):
            return self._rejected(candidate, "patch_artifact_invalid")
        if hashlib.sha256(patch).hexdigest() != candidate.patch_sha256:
            return self._rejected(candidate, "patch_artifact_invalid")
        admission = validate_unified_patch(
            patch,
            policy=PatchPolicy(
                allowed_paths=("**",),
                allow_binary=True,
                allow_dependency_locks=True,
                allow_ci=True,
                allow_secret_paths=True,
                allow_infrastructure=True,
                allow_migrations=True,
            ),
            budget=CandidateBudget(
                max_files=64,
                max_changed_lines=10_000,
                max_patch_bytes=64 * 1024 * 1024,
            ),
        )
        if (
            not admission.accepted
            or admission.changed_files != candidate.changed_files
            or admission.changed_lines != candidate.changed_lines
        ):
            return self._rejected(candidate, "patch_metadata_mismatch")

        checks: list[CheckOutcome] = []
        evaluation_id = hashlib.sha256(
            f"{candidate.candidate_id}:{candidate.base_sha}".encode()
        ).hexdigest()[:20]
        try:
            with WorktreeCheckout(
                self.repository,
                revision=candidate.base_sha,
                root=self.worktree_root,
                repair_id=f"evaluate-{evaluation_id}",
            ) as materialized:
                if not _apply_patch(materialized.path, patch):
                    return self._rejected(candidate, "patch_apply_failed")
                for check in plan.ordered():
                    try:
                        outcome = await self.runner.run(check, worktree=materialized.path)
                    except Exception:
                        outcome = CheckOutcome(
                            check_id=check.check_id,
                            stage=check.stage,
                            status=CheckStatus.INCONCLUSIVE,
                            duration_ms=0,
                        )
                    if outcome.check_id != check.check_id or outcome.stage is not check.stage:
                        outcome = CheckOutcome(
                            check_id=check.check_id,
                            stage=check.stage,
                            status=CheckStatus.INCONCLUSIVE,
                            duration_ms=0,
                        )
                    checks.append(outcome)
                    if outcome.status is not CheckStatus.PASSED:
                        return self._from_checks(
                            candidate,
                            checks,
                            rejection_reason=f"{check.stage.value}_{outcome.status.value}",
                        )
                try:
                    contract = await self.contract_analyzer.analyze(worktree=materialized.path)
                except Exception:
                    return self._from_checks(
                        candidate,
                        checks,
                        rejection_reason="contract_analysis_inconclusive",
                        required_inconclusive=True,
                    )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
            return self._from_checks(
                candidate,
                checks,
                rejection_reason="evaluation_runtime_inconclusive",
                required_inconclusive=True,
            )
        return self._from_checks(candidate, checks, contract=contract)

    def _from_checks(
        self,
        candidate: CandidatePatch,
        checks: list[CheckOutcome],
        *,
        contract: ContractAnalysis | None = None,
        rejection_reason: str | None = None,
        required_inconclusive: bool | None = None,
    ) -> EvaluationRecord:
        passed = {outcome.stage for outcome in checks if outcome.status is CheckStatus.PASSED}
        inconclusive = any(
            outcome.status in {CheckStatus.TIMED_OUT, CheckStatus.INCONCLUSIVE}
            for outcome in checks
        )
        evidence = tuple(artifact for outcome in checks for artifact in outcome.artifact_ids) + (
            () if contract is None else contract.artifact_ids
        )
        duration = sum(outcome.duration_ms for outcome in checks)
        if contract is not None:
            duration += contract.duration_ms
        delta = DataContractDelta() if contract is None else contract.delta
        effective_rejection = rejection_reason
        if effective_rejection is None and delta.breaking:
            effective_rejection = "contract_breaking"
        evaluation = CandidateEvaluation(
            candidate_id=candidate.candidate_id,
            replay_passed=EvaluationStage.REPRODUCTION in passed,
            regression_passed={
                EvaluationStage.FIXTURE_SCHEMA,
                EvaluationStage.DATA_QUALITY,
                EvaluationStage.IMPACTED_TESTS,
            }.issubset(passed),
            security_passed={
                EvaluationStage.SECURITY,
                EvaluationStage.STATIC,
            }.issubset(passed),
            required_inconclusive=(
                inconclusive if required_inconclusive is None else required_inconclusive
            ),
            contract_delta=delta,
            evidence_artifact_ids=evidence,
            verification_duration_ms=duration,
            changed_files=len(candidate.changed_files),
            changed_lines=candidate.changed_lines,
            risk_penalty=_risk_penalty(candidate, delta),
            rejection_reason=effective_rejection,
        )
        return EvaluationRecord(evaluation=evaluation, checks=tuple(checks))

    def _rejected(self, candidate: CandidatePatch, reason: str) -> EvaluationRecord:
        return self._from_checks(candidate, [], rejection_reason=reason)


def _apply_patch(worktree: Path, patch: bytes) -> bool:
    environment = {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    command = ["git", "apply", "--whitespace=error-all", "-"]
    checked = subprocess.run(
        [*command[:2], "--check", *command[2:]],
        cwd=worktree,
        env=environment,
        input=patch,
        capture_output=True,
    )
    if checked.returncode != 0:
        return False
    applied = subprocess.run(
        command,
        cwd=worktree,
        env=environment,
        input=patch,
        capture_output=True,
    )
    return applied.returncode == 0


def _risk_penalty(candidate: CandidatePatch, delta: DataContractDelta) -> int:
    strategy_penalty = 0 if candidate.strategy == "normalize_ingestion_boundary" else 1
    return min(10_000, strategy_penalty + len(delta.changes))
