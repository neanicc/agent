from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated


_IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9._:-]{0,127})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PARAMETERS_BYTES = 64 * 1024


def _bounded_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return normalized


def _identifier(value: str) -> str:
    normalized = value.strip().lower()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError("value must be a stable lowercase identifier")
    return normalized


NonEmptyText = Annotated[str, AfterValidator(_bounded_text)]
Identifier = Annotated[str, AfterValidator(_identifier)]


def _sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError("value must be a lowercase SHA-256 digest")
    return normalized


Sha256 = Annotated[str, AfterValidator(_sha256)]


class RuleSource(StrEnum):
    SAFETY = "safety"
    REPOSITORY = "repository"
    EXPLICIT = "explicit"
    LEARNED = "learned"


class RuleSeverity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    INFORM = "inform"


class PreferenceStatus(StrEnum):
    PASS = "pass"
    VIOLATION = "violation"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"


class PreferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PreferenceRule(PreferenceModel):
    id: Identifier
    source: RuleSource
    severity: RuleSeverity
    statement: NonEmptyText = Field(max_length=8_192)
    scopes: list[Identifier] = Field(default_factory=list, max_length=128)
    evaluator: Identifier | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("rule scopes must be unique")
        return value

    @field_validator("parameters")
    @classmethod
    def bounded_json_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("rule parameters must be finite JSON data") from exc
        if len(encoded) > _MAX_PARAMETERS_BYTES:
            raise ValueError("rule parameters exceed 64 KiB")
        _validate_json_shape(value)
        return value

    @model_validator(mode="after")
    def learned_is_soft(self) -> PreferenceRule:
        if self.source is RuleSource.LEARNED and self.severity is RuleSeverity.BLOCK:
            raise ValueError("learned preferences must be soft")
        return self


class PreferenceSourceManifest(PreferenceModel):
    source_id: Identifier
    kind: Literal[
        "builtin",
        "repository",
        "design_tokens",
        "instructions",
        "organization",
        "user",
        "learned",
    ]
    locator: NonEmptyText = Field(max_length=1_024)
    sha256: Sha256
    size_bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    trusted: bool


class PreferenceProfile(PreferenceModel):
    schema_version: Literal[1] = 1
    profile_id: Identifier
    rules: list[PreferenceRule] = Field(default_factory=list, max_length=4_096)
    design_tokens: dict[str, Any] = Field(default_factory=dict)
    source_manifest: list[PreferenceSourceManifest] = Field(default_factory=list, max_length=1_024)

    @field_validator("design_tokens")
    @classmethod
    def bounded_design_tokens(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 1024 * 1024:
            raise ValueError("compiled design tokens exceed 1 MiB")
        _validate_json_shape(value)
        return value

    @model_validator(mode="after")
    def unique_rule_ids(self) -> PreferenceProfile:
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("preference rule IDs must be unique")
        source_ids = [source.source_id for source in self.source_manifest]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("preference source IDs must be unique")
        return self

    def rule(self, rule_id: str) -> PreferenceRule | None:
        normalized = rule_id.strip().lower()
        return next((rule for rule in self.rules if rule.id == normalized), None)


class PreferenceEvidence(PreferenceModel):
    schema_version: Literal[1] = 1
    evidence_id: Identifier
    rule_id: Identifier
    artifact_id: NonEmptyText = Field(max_length=512)
    evaluator: Identifier
    evaluator_version: NonEmptyText = Field(max_length=128)
    summary: NonEmptyText = Field(max_length=16_384)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def bounded_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        PreferenceRule.bounded_json_parameters(value)
        return value


class PreferenceVerdict(PreferenceModel):
    schema_version: Literal[1] = 1
    verdict_id: Identifier
    rule_id: Identifier
    artifact_id: NonEmptyText = Field(max_length=512)
    evaluator: Identifier
    evaluator_version: NonEmptyText = Field(max_length=128)
    status: PreferenceStatus
    severity: RuleSeverity
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=1_024)
    summary: NonEmptyText = Field(max_length=16_384)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    model_id: NonEmptyText | None = Field(default=None, max_length=256)
    prompt_version: Identifier | None = None
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    input_artifact_hashes: list[Sha256] = Field(default_factory=list, max_length=16)
    budget_reservation_id: Identifier | None = None

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("verdict evidence IDs must be unique")
        return value

    @field_validator("cost_usd")
    @classmethod
    def finite_cost(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("verdict cost must be finite")
        return value

    @field_validator("input_artifact_hashes")
    @classmethod
    def unique_artifact_hashes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("verdict artifact hashes must be unique")
        return value


def _validate_json_shape(value: Any, *, depth: int = 0, items: list[int] | None = None) -> None:
    if depth > 16:
        raise ValueError("JSON data exceeds maximum nesting depth")
    if items is None:
        items = [0]
    items[0] += 1
    if items[0] > 4_096:
        raise ValueError("JSON data contains too many values")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise ValueError("JSON object keys must be bounded non-empty strings")
            _validate_json_shape(child, depth=depth + 1, items=items)
    elif isinstance(value, list):
        for child in value:
            _validate_json_shape(child, depth=depth + 1, items=items)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("value is not valid JSON")
