"""Deterministic strategy planning for pipeline repair candidates."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from loopguard.heal.models import FailureEvent, RepairModel


class RepairStrategy(StrEnum):
    NORMALIZE_INGESTION_BOUNDARY = "normalize_ingestion_boundary"
    SCHEMA_VALIDATION_AND_COERCION = "schema_validation_and_coercion"
    BACKWARD_COMPATIBLE_DRIFT_ADAPTER = "backward_compatible_drift_adapter"


_STRATEGY_OBJECTIVES: dict[RepairStrategy, str] = {
    RepairStrategy.NORMALIZE_INGESTION_BOUNDARY: (
        "Normalize the malformed or drifting value once at the ingestion boundary."
    ),
    RepairStrategy.SCHEMA_VALIDATION_AND_COERCION: (
        "Strengthen schema validation and narrowly coerce compatible values."
    ),
    RepairStrategy.BACKWARD_COMPATIBLE_DRIFT_ADAPTER: (
        "Add a backward-compatible adapter for the observed upstream version drift."
    ),
}


class HostileEvidenceEnvelope(RepairModel):
    """Bounded pipeline evidence that is always treated as non-authoritative data."""

    authority: Literal["untrusted_data"] = "untrusted_data"
    failure_message: str = Field(default="", max_length=16_384)
    logs: str = Field(default="", max_length=32_768)
    fixture: str = Field(default="", max_length=32_768)
    contract: str = Field(default="", max_length=32_768)


class RepairStrategyBrief(RepairModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    repository_id: str = Field(min_length=1, max_length=256)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    strategy: RepairStrategy
    objective: str = Field(min_length=1, max_length=512)
    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=128)
    evidence: HostileEvidenceEnvelope

    @field_validator("allowed_paths")
    @classmethod
    def safe_relative_patterns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if (
                not value
                or len(value) > 512
                or value.startswith(("/", "\\"))
                or "\x00" in value
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
                or any(part == ".." for part in value.replace("\\", "/").split("/"))
            ):
                raise ValueError("allowed repair paths must be safe relative patterns")
        return values

    def prompt_context(self) -> str:
        """Return deterministic JSON with evidence visibly marked as hostile data."""

        return json.dumps(
            {
                "repository_id": self.repository_id,
                "base_sha": self.base_sha,
                "strategy": self.strategy.value,
                "objective": self.objective,
                "allowed_paths": list(self.allowed_paths),
                "evidence": self.evidence.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class RepairPlanner:
    """Emit independent strategy briefs; planning itself never writes code."""

    _strategies = tuple(RepairStrategy)

    def plan(
        self,
        *,
        failure: FailureEvent,
        evidence: HostileEvidenceEnvelope,
        allowed_paths: tuple[str, ...],
        count: int = 3,
    ) -> tuple[RepairStrategyBrief, ...]:
        if not 1 <= count <= len(self._strategies):
            raise ValueError(f"candidate count must be between 1 and {len(self._strategies)}")
        if failure.fingerprint is None:
            raise ValueError("candidate planning requires a fingerprinted failure")
        briefs: list[RepairStrategyBrief] = []
        identity = hashlib.sha256(
            f"{failure.failure_id}:{failure.revision}:{failure.fingerprint}".encode()
        ).hexdigest()[:16]
        for index, strategy in enumerate(self._strategies[:count], start=1):
            briefs.append(
                RepairStrategyBrief(
                    candidate_id=f"candidate-{identity}-{index}",
                    repository_id=failure.repo_id,
                    base_sha=failure.revision,
                    strategy=strategy,
                    objective=_STRATEGY_OBJECTIVES[strategy],
                    allowed_paths=allowed_paths,
                    evidence=evidence,
                )
            )
        return tuple(briefs)
