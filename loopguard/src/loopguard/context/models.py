from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _nonempty(value: str) -> str:
    normalized = value.strip()
    if not normalized or "\x00" in normalized:
        raise ValueError("value must not be empty")
    return normalized


def _repository_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("path must use repository-relative POSIX form")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must remain repository-relative")
    return path.as_posix()


NonEmptyStr = Annotated[str, AfterValidator(_nonempty)]


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChangeObservation(ContextModel):
    observation_id: NonEmptyStr = Field(max_length=512)
    control_event_id: NonEmptyStr = Field(max_length=512)
    repo_id: NonEmptyStr = Field(max_length=512)
    repo_seq: int = Field(ge=1)
    worktree_id: NonEmptyStr = Field(max_length=512)
    path: str = Field(max_length=4096)
    actor: NonEmptyStr = Field(max_length=1024)
    before_hash: NonEmptyStr | None = Field(default=None, max_length=256)
    after_hash: NonEmptyStr | None = Field(default=None, max_length=256)
    patch: str | None = Field(default=None, max_length=1_000_000)
    symbols: list[NonEmptyStr] = Field(default_factory=list, max_length=4096)
    verification_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=4096)
    reconciliation_id: NonEmptyStr | None = Field(default=None, max_length=512)
    observation_kind: Literal["change", "reconciliation.current_state"] = "change"
    history_complete: bool = True
    recovery_reason: Literal["overflow", "restart"] | None = None
    observed_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _repository_path(value)

    @field_validator("symbols", "verification_ids")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("list values must be unique")
        return values

    @model_validator(mode="after")
    def reconciliation_truth_is_explicit(self) -> ChangeObservation:
        if self.observation_kind == "reconciliation.current_state":
            if self.recovery_reason is None or self.history_complete:
                raise ValueError("recovered current state cannot claim complete history")
        elif self.recovery_reason is not None or not self.history_complete:
            raise ValueError("ordinary changes cannot carry recovery semantics")
        return self


class ChangeRecord(ContextModel):
    record_id: NonEmptyStr = Field(max_length=512)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    repo_id: NonEmptyStr = Field(max_length=512)
    repo_seq: int = Field(ge=1)
    worktree_id: NonEmptyStr = Field(max_length=512)
    path: str = Field(max_length=4096)
    actor: NonEmptyStr = Field(max_length=1024)
    before_hash: NonEmptyStr | None = Field(default=None, max_length=256)
    after_hash: NonEmptyStr | None = Field(default=None, max_length=256)
    patch: str | None = Field(default=None, max_length=1_000_000)
    symbols: list[NonEmptyStr] = Field(default_factory=list, max_length=4096)
    verification_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=4096)
    provenance: list[ChangeObservation] = Field(default_factory=list, max_length=10_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _repository_path(value)

    @field_validator("content_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("content fingerprint must be a SHA-256 digest")
        return normalized

    @field_validator("symbols", "verification_ids")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("list values must be unique")
        return values


class ContextCheckpoint(ContextModel):
    repo_id: NonEmptyStr = Field(max_length=512)
    repo_seq: int = Field(ge=0)
    commit_sha: NonEmptyStr | None = Field(default=None, max_length=64)
    worktree_hash: NonEmptyStr = Field(max_length=256)
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
