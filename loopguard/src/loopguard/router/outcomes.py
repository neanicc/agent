from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, field_validator

from .store import OutcomeStore


CostStatus = Literal["complete", "partial", "unknown"]


class RoutingOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    routing_id: str = Field(min_length=1, max_length=512)
    policy_version: str | None = Field(default=None, max_length=128)
    selected_model: str | None = Field(default=None, max_length=256)
    actual_model: str | None = Field(default=None, max_length=256)
    effort: str | None = Field(default=None, max_length=64)
    phase: Literal["plan", "implement", "verify", "repair"] | None = None
    matched_rule: str | None = Field(default=None, max_length=128)
    automatic: bool | None = None
    experiment_group: Literal["control", "routed"] | None = None
    routing_mode: Literal["control", "shadow", "holdout", "automatic"] | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    provider_reported_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    estimated_agent_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    agent_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    guard_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    verification_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    repair_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    verified: bool | None
    verification_verdict: str | None = Field(default=None, max_length=128)
    regressions: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    interruptions: int = Field(default=0, ge=0)
    repairs: int = Field(default=0, ge=0)
    wall_time_ms: int = Field(ge=0)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(
        "policy_version",
        "selected_model",
        "actual_model",
        "effort",
        "matched_rule",
        "verification_verdict",
    )
    @classmethod
    def _optional_text_is_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("outcome text fields must not be empty")
        return normalized

    @field_validator("regressions")
    @classmethod
    def _regressions_are_bounded(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value or len(value) > 2_048 for value in normalized):
            raise ValueError("regression identifiers must be non-empty and bounded")
        if len(set(normalized)) != len(normalized):
            raise ValueError("regression identifiers must be unique")
        return normalized

    @computed_field
    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    @computed_field
    @property
    def known_total(self) -> Decimal | None:
        values = self._cost_components()
        known = [value for value in values if value is not None]
        return sum(known, Decimal("0")) if known else None

    @computed_field
    @property
    def total_cost(self) -> Decimal | None:
        values = self._cost_components()
        if any(value is None for value in values):
            return None
        return sum((value for value in values if value is not None), Decimal("0"))

    @computed_field
    @property
    def cost_status(self) -> CostStatus:
        values = self._cost_components()
        if all(value is not None for value in values):
            return "complete"
        if any(value is not None for value in values):
            return "partial"
        return "unknown"

    def _cost_components(self) -> tuple[Decimal | None, ...]:
        return self.agent_cost, self.guard_cost, self.verification_cost, self.repair_cost


class OutcomeRecorder:
    def __init__(self, store: OutcomeStore) -> None:
        self.store = store

    @classmethod
    def for_path(cls, path: str | Path) -> OutcomeRecorder:
        return cls(OutcomeStore(Path(path)))

    def record(self, **values: object) -> RoutingOutcome:
        for name in (
            "provider_reported_cost",
            "estimated_agent_cost",
            "agent_cost",
            "guard_cost",
            "verification_cost",
            "repair_cost",
        ):
            value = values.get(name)
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal or None")
        outcome = RoutingOutcome.model_validate(values)
        self.store.insert(outcome)
        return outcome

    def get(self, routing_id: str) -> RoutingOutcome:
        return self.store.get(routing_id)

    def list(self, *, limit: int = 100) -> list[RoutingOutcome]:
        return self.store.list(limit=limit)

    def close(self) -> None:
        self.store.close()
