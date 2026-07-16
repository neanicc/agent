from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
SourceKind = Literal["product_config", "provider_discovery", "admin_policy", "test"]
CatalogStatus = Literal["fresh", "stale"]
SignatureVerifier = Callable[[str, bytes, str], bool]


class CatalogTrustError(ValueError):
    """A catalog update could not be trusted or used at the requested time."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"model catalog rejected: {reason}")


class ModelSpec(BaseModel):
    """Immutable provider metadata used to evaluate a routing candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(max_length=256)
    provider: str = Field(max_length=128)
    surfaces: frozenset[str] = Field(min_length=1, max_length=32)
    efforts: frozenset[str] = Field(min_length=1, max_length=32)
    input_cost_per_million: NonNegativeDecimal | None = None
    output_cost_per_million: NonNegativeDecimal | None = None
    max_context_tokens: int | None = Field(default=None, gt=0)
    capabilities: frozenset[str] = Field(default_factory=frozenset, max_length=256)
    tags: frozenset[str] = Field(default_factory=frozenset, max_length=256)
    observed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(max_length=512)
    source_kind: SourceKind = "provider_discovery"
    effective_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    provider_revision: str | None = Field(default=None, max_length=256)
    capability_evidence: frozenset[tuple[str, str]] = Field(
        default_factory=frozenset, max_length=256
    )

    @field_validator("id", "provider", "source")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("provider_revision")
    @classmethod
    def _non_empty_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider_revision must not be empty")
        return normalized

    @field_validator("surfaces", "efforts", "capabilities", "tags")
    @classmethod
    def _non_empty_tokens(cls, values: frozenset[str]) -> frozenset[str]:
        normalized = frozenset(value.strip() for value in values)
        if "" in normalized:
            raise ValueError("capability tokens must not be empty")
        return normalized

    @field_validator("capability_evidence", mode="before")
    @classmethod
    def _normalize_evidence(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return frozenset((str(key), str(evidence)) for key, evidence in value.items())
        return value

    @field_validator("capability_evidence")
    @classmethod
    def _validate_evidence(cls, values: frozenset[tuple[str, str]]) -> frozenset[tuple[str, str]]:
        normalized = frozenset((key.strip(), evidence.strip()) for key, evidence in values)
        if any(not key or not evidence for key, evidence in normalized):
            raise ValueError("capability_evidence keys and values must not be empty")
        if any(len(key) > 256 or len(evidence) > 2_048 for key, evidence in normalized):
            raise ValueError("capability_evidence exceeds its size limit")
        return normalized

    @model_validator(mode="after")
    def _validate_lifetime(self) -> ModelSpec:
        effective_at = self.effective_at or self.observed_at
        if self.expires_at is not None and self.expires_at <= effective_at:
            raise ValueError("expires_at must be later than effective_at")
        evidence_capabilities = {capability for capability, _source in self.capability_evidence}
        if not evidence_capabilities.issubset(self.capabilities):
            raise ValueError("capability_evidence must refer to declared capabilities")
        return self


class CatalogDocument(BaseModel):
    """A signed, bounded-lifetime catalog update from a trusted source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    catalog_version: str = Field(max_length=256)
    source: str = Field(max_length=512)
    key_id: str = Field(max_length=256)
    signature: str = Field(max_length=16_384)
    observed_at: AwareDatetime
    effective_at: AwareDatetime
    expires_at: AwareDatetime
    models: tuple[ModelSpec, ...] = Field(min_length=1, max_length=10_000)

    @field_validator("catalog_version", "source", "key_id", "signature")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_document(self) -> CatalogDocument:
        if self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be later than effective_at")
        _ordered_unique_models(self.models)
        return self

    def signed_payload(self) -> bytes:
        """Return deterministic UTF-8 JSON suitable for an injected verifier."""
        payload = self.model_dump(mode="json", exclude={"signature", "models"})
        payload["models"] = [
            _canonical_model(model) for model in _ordered_unique_models(self.models)
        ]
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True, init=False)
class ModelCatalog:
    """An immutable snapshot of known models and its trust/freshness state."""

    models: tuple[ModelSpec, ...]
    catalog_version: str
    source: str | None
    key_id: str | None
    signature: str | None
    observed_at: datetime | None
    effective_at: datetime | None
    expires_at: datetime | None
    status: CatalogStatus
    stale_reason: str | None

    def __init__(
        self,
        models: Iterable[ModelSpec],
        *,
        catalog_version: str = "unversioned",
        source: str | None = None,
        key_id: str | None = None,
        signature: str | None = None,
        observed_at: datetime | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
        status: CatalogStatus = "fresh",
        stale_reason: str | None = None,
    ) -> None:
        ordered = _ordered_unique_models(models)
        if not ordered:
            raise ValueError("model catalog must contain at least one model")
        if status == "fresh" and stale_reason is not None:
            raise ValueError("a fresh catalog cannot have a stale reason")
        if status == "stale" and not stale_reason:
            raise ValueError("a stale catalog must include a stale reason")
        object.__setattr__(self, "models", ordered)
        object.__setattr__(
            self, "catalog_version", _required_text(catalog_version, "catalog_version")
        )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "signature", signature)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stale_reason", stale_reason)

    @classmethod
    def from_signed_document(
        cls,
        document: CatalogDocument,
        *,
        verifier: SignatureVerifier,
        now: datetime | None = None,
        last_known_valid: ModelCatalog | None = None,
    ) -> ModelCatalog:
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("catalog verification time must be timezone-aware")
        reason: str | None = None
        try:
            signature_valid = (
                verifier(
                    document.key_id,
                    document.signed_payload(),
                    document.signature,
                )
                is True
            )
        except Exception:
            signature_valid = False
        if not signature_valid:
            reason = "signature_invalid"
        elif checked_at < document.effective_at:
            reason = "not_effective"
        elif checked_at >= document.expires_at:
            reason = "expired"
        if reason is not None:
            if last_known_valid is not None:
                return last_known_valid._as_stale(reason)
            raise CatalogTrustError(reason)
        return cls(
            document.models,
            catalog_version=document.catalog_version,
            source=document.source,
            key_id=document.key_id,
            signature=document.signature,
            observed_at=document.observed_at,
            effective_at=document.effective_at,
            expires_at=document.expires_at,
        )

    def eligible(
        self,
        *,
        surface: str,
        effort: str,
        capabilities: Iterable[str] = (),
        max_input_cost_per_million: Decimal | int | str | None = None,
        max_output_cost_per_million: Decimal | int | str | None = None,
        min_context_tokens: int | None = None,
        at: datetime | None = None,
    ) -> list[ModelSpec]:
        required_capabilities = frozenset(capabilities)
        input_limit = _decimal_limit(max_input_cost_per_million, "input cost limit")
        output_limit = _decimal_limit(max_output_cost_per_million, "output cost limit")
        if min_context_tokens is not None and min_context_tokens <= 0:
            raise ValueError("minimum context tokens must be positive")
        checked_at = at or datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("catalog eligibility time must be timezone-aware")
        if self.status == "fresh" and (
            (self.effective_at is not None and checked_at < self.effective_at)
            or (self.expires_at is not None and checked_at >= self.expires_at)
        ):
            return []
        eligible: list[ModelSpec] = []
        for model in self.models:
            effective_at = model.effective_at or model.observed_at
            if checked_at < effective_at or (
                model.expires_at is not None and checked_at >= model.expires_at
            ):
                continue
            if surface not in model.surfaces or effort not in model.efforts:
                continue
            if not required_capabilities.issubset(model.capabilities):
                continue
            if not _within_limit(model.input_cost_per_million, input_limit):
                continue
            if not _within_limit(model.output_cost_per_million, output_limit):
                continue
            if min_context_tokens is not None and (
                model.max_context_tokens is None or model.max_context_tokens < min_context_tokens
            ):
                continue
            eligible.append(model)
        return eligible

    def _as_stale(self, reason: str) -> ModelCatalog:
        return ModelCatalog(
            self.models,
            catalog_version=self.catalog_version,
            source=self.source,
            key_id=self.key_id,
            signature=self.signature,
            observed_at=self.observed_at,
            effective_at=self.effective_at,
            expires_at=self.expires_at,
            status="stale",
            stale_reason=reason,
        )


def _ordered_unique_models(models: Iterable[ModelSpec]) -> tuple[ModelSpec, ...]:
    ordered = tuple(sorted(models, key=lambda model: (model.id, model.provider)))
    seen: set[str] = set()
    for model in ordered:
        if model.id in seen:
            raise ValueError(f"duplicate model id: {model.id}")
        seen.add(model.id)
    return ordered


def _canonical_model(model: ModelSpec) -> dict[str, object]:
    payload = model.model_dump(
        mode="json",
        exclude={"surfaces", "efforts", "capabilities", "tags", "capability_evidence"},
    )
    payload["surfaces"] = sorted(model.surfaces)
    payload["efforts"] = sorted(model.efforts)
    payload["capabilities"] = sorted(model.capabilities)
    payload["tags"] = sorted(model.tags)
    payload["capability_evidence"] = [
        [capability, evidence] for capability, evidence in sorted(model.capability_evidence)
    ]
    return payload


def _decimal_limit(value: Decimal | int | str | None, name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not converted.is_finite() or converted < 0:
        raise ValueError(f"{name} must be a finite non-negative decimal")
    return converted


def _within_limit(value: Decimal | None, limit: Decimal | None) -> bool:
    if limit is None:
        return True
    return value is not None and value <= limit


def _required_text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized
