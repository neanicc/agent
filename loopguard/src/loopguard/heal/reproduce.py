"""Evidence-gated failure reproduction."""

from __future__ import annotations

import hashlib
from typing import Literal, Protocol

from pydantic import Field

from loopguard.heal.intake import FailurePayloadRejected, normalize_failure
from loopguard.heal.models import FailureEvent, RepairModel
from loopguard.heal.sandbox import (
    ArtifactPolicyViolation,
    SandboxExecution,
    SandboxRuntimeError,
    SandboxSpec,
    validate_artifacts,
)

ReproductionReason = Literal[
    "fingerprint_match",
    "fingerprint_mismatch",
    "failure_not_observed",
    "sandbox_timeout",
    "invalid_observation",
    "sandbox_failed",
]


class SandboxRuntime(Protocol):
    def run(self, spec: SandboxSpec) -> SandboxExecution: ...


class ReproductionResult(RepairModel):
    reproduced: bool
    reason: ReproductionReason
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    repository_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    command: tuple[str, ...]
    exit_code: int
    timed_out: bool
    output_artifact_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_paths: tuple[str, ...]
    assurance: Literal["test", "local_docker", "hosted_isolated"]


class ReproductionService:
    def __init__(self, runtime: SandboxRuntime) -> None:
        self.runtime = runtime

    def reproduce(self, failure: FailureEvent, spec: SandboxSpec) -> ReproductionResult:
        if failure.fingerprint is None:
            raise ValueError("failure fingerprint is required for reproduction")
        spec.validate_policy()
        if failure.repo_id != spec.repository_id or failure.revision != spec.repository_sha:
            raise ValueError("sandbox repository revision does not match failure evidence")
        try:
            execution = self.runtime.run(spec)
            if execution.assurance == "hosted_isolated":
                assert execution.hosted_evidence is not None
                execution.hosted_evidence.validate_for(spec)
            if len(execution.stdout) + len(execution.stderr) > spec.limits.output_bytes:
                raise SandboxRuntimeError("sandbox output exceeds policy")
            artifacts = validate_artifacts(
                spec.artifact_path,
                maximum_bytes=spec.limits.artifact_bytes,
                maximum_files=spec.limits.artifact_files,
            )
        except (SandboxRuntimeError, ArtifactPolicyViolation):
            return self._result(
                failure,
                spec,
                execution=None,
                reason="sandbox_failed",
                observed_fingerprint=None,
                artifacts=(),
            )

        output_id = _output_artifact(execution.stdout, execution.stderr)
        if execution.timed_out:
            return self._result(
                failure,
                spec,
                execution=execution,
                reason="sandbox_timeout",
                observed_fingerprint=None,
                artifacts=artifacts,
                output_id=output_id,
            )
        if execution.exit_code == 0:
            return self._result(
                failure,
                spec,
                execution=execution,
                reason="failure_not_observed",
                observed_fingerprint=None,
                artifacts=artifacts,
                output_id=output_id,
            )
        if execution.observed_source is None or execution.observed_payload is None:
            return self._result(
                failure,
                spec,
                execution=execution,
                reason="invalid_observation",
                observed_fingerprint=None,
                artifacts=artifacts,
                output_id=output_id,
            )
        try:
            observed = normalize_failure(
                execution.observed_source,
                execution.observed_payload,
            )
        except FailurePayloadRejected:
            return self._result(
                failure,
                spec,
                execution=execution,
                reason="invalid_observation",
                observed_fingerprint=None,
                artifacts=artifacts,
                output_id=output_id,
            )
        matched = observed.fingerprint == failure.fingerprint
        return self._result(
            failure,
            spec,
            execution=execution,
            reason="fingerprint_match" if matched else "fingerprint_mismatch",
            observed_fingerprint=observed.fingerprint,
            artifacts=artifacts,
            output_id=output_id,
        )

    def _result(
        self,
        failure: FailureEvent,
        spec: SandboxSpec,
        *,
        execution: SandboxExecution | None,
        reason: ReproductionReason,
        observed_fingerprint: str | None,
        artifacts: tuple[str, ...],
        output_id: str | None = None,
    ) -> ReproductionResult:
        if failure.fingerprint is None:
            raise ValueError("failure fingerprint is required")
        return ReproductionResult(
            reproduced=reason == "fingerprint_match",
            reason=reason,
            expected_fingerprint=failure.fingerprint,
            observed_fingerprint=observed_fingerprint,
            repository_sha=spec.repository_sha,
            image_digest=spec.image.rsplit("@", 1)[1],
            command=spec.command,
            exit_code=execution.exit_code if execution is not None else -1,
            timed_out=execution.timed_out if execution is not None else False,
            output_artifact_id=output_id or _output_artifact(b"", b""),
            artifact_paths=artifacts,
            assurance=execution.assurance if execution is not None else "test",
        )


def _output_artifact(stdout: bytes, stderr: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(len(stdout).to_bytes(8, "big"))
    digest.update(stdout)
    digest.update(len(stderr).to_bytes(8, "big"))
    digest.update(stderr)
    return f"sha256:{digest.hexdigest()}"
