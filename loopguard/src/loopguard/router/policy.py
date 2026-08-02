from __future__ import annotations

import tomllib
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .catalog import ModelCatalog, ModelSpec
from .features import Risk, TaskProfile, TaskType


Phase = Literal["plan", "implement", "verify", "repair"]
BoundedDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
_PHASES = frozenset({"plan", "implement", "verify", "repair"})


class PolicyConfigurationError(ValueError):
    """The policy document is malformed, ambiguous, or unsafe to execute."""


class RoutingPolicyError(RuntimeError):
    """A routing decision cannot be produced without inventing state."""


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=128)
    priority: int = Field(ge=-1_000_000, le=1_000_000)
    phases: frozenset[Phase] = Field(min_length=1, max_length=4)
    task_types: frozenset[TaskType] = Field(default_factory=frozenset, max_length=7)
    risks: frozenset[Risk] = Field(default_factory=frozenset, max_length=3)
    min_complexity: int | None = Field(default=None, ge=0, le=100)
    max_complexity: int | None = Field(default=None, ge=0, le=100)
    requires_all: frozenset[str] = Field(default_factory=frozenset, max_length=128)
    requires_any: frozenset[str] = Field(default_factory=frozenset, max_length=128)
    allowed_model_tags: frozenset[str] = Field(default_factory=frozenset, max_length=256)
    required_capabilities: frozenset[str] = Field(default_factory=frozenset, max_length=256)
    effort: str = Field(max_length=64)
    max_input_cost_per_million: BoundedDecimal | None = None
    max_output_cost_per_million: BoundedDecimal | None = None
    fallback_rule: str = Field(max_length=128)

    @field_validator(
        "name",
        "effort",
        "fallback_rule",
    )
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy text fields must not be empty")
        return normalized

    @field_validator(
        "requires_all",
        "requires_any",
        "allowed_model_tags",
        "required_capabilities",
    )
    @classmethod
    def _bounded_tokens(cls, values: frozenset[str]) -> frozenset[str]:
        normalized = frozenset(value.strip() for value in values)
        if "" in normalized or any(len(value) > 128 for value in normalized):
            raise ValueError("policy tokens must be non-empty and bounded")
        return normalized

    @model_validator(mode="after")
    def _complexity_range_is_ordered(self) -> PolicyRule:
        if (
            self.min_complexity is not None
            and self.max_complexity is not None
            and self.min_complexity > self.max_complexity
        ):
            raise ValueError("minimum complexity cannot exceed maximum complexity")
        return self

    def matches(self, profile: TaskProfile, phase: Phase) -> bool:
        if phase not in self.phases:
            return False
        if self.task_types and profile.task_type not in self.task_types:
            return False
        if self.risks and profile.risk not in self.risks:
            return False
        if self.min_complexity is not None and profile.complexity_score < self.min_complexity:
            return False
        if self.max_complexity is not None and profile.complexity_score > self.max_complexity:
            return False
        if not self.requires_all.issubset(profile.requires):
            return False
        return not self.requires_any or not self.requires_any.isdisjoint(profile.requires)


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(max_length=128)
    rules: tuple[PolicyRule, ...] = Field(min_length=1, max_length=1_000)

    @field_validator("version")
    @classmethod
    def _version_is_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy version must not be empty")
        return normalized


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str
    model_id: str
    effort: str
    phase: Phase
    matched_rule: str
    considered_models: list[str]
    rejected: dict[str, str]
    automatic: bool
    reason: str | None = None
    fallback_path: list[str] = Field(default_factory=list)


class RouterPolicy:
    """Evaluate versioned rules without model calls or ambient provider state."""

    def __init__(
        self,
        document: PolicyDocument,
        catalog: ModelCatalog,
        *,
        current_model_id: str | None = None,
        current_effort: str | None = None,
    ) -> None:
        self.document = document
        self.catalog = catalog
        self.current_model_id = current_model_id
        self.current_effort = current_effort
        self._rules = _validated_rules(document.rules)

    @classmethod
    def default(
        cls,
        catalog: ModelCatalog,
        *,
        current_model_id: str | None = None,
        current_effort: str | None = None,
    ) -> RouterPolicy:
        try:
            source = files("loopguard.router").joinpath("default-policy.toml").read_text("utf-8")
        except OSError as exc:
            raise PolicyConfigurationError("default policy resource is unavailable") from exc
        return cls.from_toml(
            source,
            catalog,
            current_model_id=current_model_id,
            current_effort=current_effort,
        )

    @classmethod
    def from_toml(
        cls,
        source: str,
        catalog: ModelCatalog,
        *,
        current_model_id: str | None = None,
        current_effort: str | None = None,
    ) -> RouterPolicy:
        document = validate_policy_toml(source)
        return cls(
            document,
            catalog,
            current_model_id=current_model_id,
            current_effort=current_effort,
        )

    def route(
        self,
        profile: TaskProfile,
        phase: Phase,
        *,
        surface: str = "managed",
        can_select_model: bool = True,
        current_model_id: str | None = None,
        current_effort: str | None = None,
        at: datetime | None = None,
    ) -> RoutingDecision:
        if phase not in _PHASES:
            raise RoutingPolicyError(f"unsupported routing phase: {phase}")
        checked_at = at or datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise RoutingPolicyError("routing time must be timezone-aware")
        active_model = current_model_id or self.current_model_id
        active_effort = current_effort or self.current_effort
        if not can_select_model:
            return self._retain_current(
                phase,
                active_model,
                active_effort,
                reason="surface_capability_unavailable",
            )
        if self.catalog.status == "stale":
            return self._retain_current(
                phase,
                active_model,
                active_effort,
                reason=f"catalog_stale:{self.catalog.stale_reason}",
            )
        if self.catalog.effective_at is not None and checked_at < self.catalog.effective_at:
            return self._retain_current(
                phase, active_model, active_effort, reason="catalog_not_effective"
            )
        if self.catalog.expires_at is not None and checked_at >= self.catalog.expires_at:
            return self._retain_current(
                phase, active_model, active_effort, reason="catalog_expired"
            )

        matching = sorted(
            (rule for rule in self._rules.values() if rule.matches(profile, phase)),
            key=lambda rule: (-rule.priority, rule.name),
        )
        if not matching:
            return self._retain_current(
                phase, active_model, active_effort, reason="no_matching_rule"
            )
        rejected: dict[str, str] = {}
        highest_priority = matching[0].priority
        leading_rules = [rule for rule in matching if rule.priority == highest_priority]
        options: list[tuple[Decimal, str, str, ModelSpec, PolicyRule]] = []
        for candidate_rule in leading_rules:
            selected, rule_rejections = self._select(
                candidate_rule,
                profile=profile,
                surface=surface,
                checked_at=checked_at,
            )
            for model_id, reason in rule_rejections.items():
                rejected.setdefault(model_id, reason)
            if selected is not None:
                assert selected.input_cost_per_million is not None
                assert selected.output_cost_per_million is not None
                options.append(
                    (
                        selected.input_cost_per_million + selected.output_cost_per_million,
                        selected.id,
                        candidate_rule.name,
                        selected,
                        candidate_rule,
                    )
                )
        if options:
            options.sort(key=lambda option: option[:3])
            _cost, _model_id, _rule_name, selected, selected_rule = options[0]
            for _cost, _model_id, _rule_name, lower_model, _lower_rule in options[1:]:
                if lower_model.id != selected.id:
                    rejected[lower_model.id] = "higher_ranked_candidate"
            rejected.pop(selected.id, None)
            return RoutingDecision(
                policy_version=self.document.version,
                model_id=selected.id,
                effort=selected_rule.effort,
                phase=phase,
                matched_rule=selected_rule.name,
                considered_models=[model.id for model in self.catalog.models],
                rejected=dict(sorted(rejected.items())),
                automatic=True,
                fallback_path=[selected_rule.name],
            )

        rule = leading_rules[0]
        fallback_path: list[str] = [rule.name]
        if rule.fallback_rule == "__current__":
            return self._retain_current(
                phase,
                active_model,
                active_effort,
                reason="no_eligible_model",
                matched_rule=rule.name,
                rejected=rejected,
                fallback_path=fallback_path,
            )
        rule = self._rules[rule.fallback_rule]
        while True:
            fallback_path.append(rule.name)
            selected, rule_rejections = self._select(
                rule,
                profile=profile,
                surface=surface,
                checked_at=checked_at,
            )
            for model_id, reason in rule_rejections.items():
                rejected.setdefault(model_id, reason)
            if selected is not None:
                rejected.pop(selected.id, None)
                return RoutingDecision(
                    policy_version=self.document.version,
                    model_id=selected.id,
                    effort=rule.effort,
                    phase=phase,
                    matched_rule=rule.name,
                    considered_models=[model.id for model in self.catalog.models],
                    rejected=dict(sorted(rejected.items())),
                    automatic=True,
                    fallback_path=fallback_path,
                )
            if rule.fallback_rule == "__current__":
                return self._retain_current(
                    phase,
                    active_model,
                    active_effort,
                    reason="no_eligible_model",
                    matched_rule=rule.name,
                    rejected=rejected,
                    fallback_path=fallback_path,
                )
            rule = self._rules[rule.fallback_rule]

    def _select(
        self,
        rule: PolicyRule,
        *,
        profile: TaskProfile,
        surface: str,
        checked_at: datetime,
    ) -> tuple[ModelSpec | None, dict[str, str]]:
        candidates: list[ModelSpec] = []
        rejected: dict[str, str] = {}
        for model in self.catalog.models:
            reason = _rejection_reason(
                model,
                rule=rule,
                profile=profile,
                surface=surface,
                checked_at=checked_at,
            )
            if reason is None:
                candidates.append(model)
            else:
                rejected[model.id] = reason
        candidates.sort(
            key=lambda model: (
                _expected_cost(model),
                model.id,
            )
        )
        if not candidates:
            return None, rejected
        for model in candidates[1:]:
            rejected[model.id] = "higher_ranked_candidate"
        return candidates[0], rejected

    def _retain_current(
        self,
        phase: Phase,
        model_id: str | None,
        effort: str | None,
        *,
        reason: str,
        matched_rule: str = "__current__",
        rejected: dict[str, str] | None = None,
        fallback_path: list[str] | None = None,
    ) -> RoutingDecision:
        if not model_id or not effort:
            raise RoutingPolicyError(
                f"{reason}; current model and effort are required to preserve user selection"
            )
        reasons = dict(rejected or {})
        reasons.setdefault("automatic", reason)
        return RoutingDecision(
            policy_version=self.document.version,
            model_id=model_id,
            effort=effort,
            phase=phase,
            matched_rule=matched_rule,
            considered_models=[model.id for model in self.catalog.models],
            rejected=dict(sorted(reasons.items())),
            automatic=False,
            reason=reason,
            fallback_path=list(fallback_path or []),
        )


def _rejection_reason(
    model: ModelSpec,
    *,
    rule: PolicyRule,
    profile: TaskProfile,
    surface: str,
    checked_at: datetime,
) -> str | None:
    effective_at = model.effective_at or model.observed_at
    if checked_at < effective_at:
        return "model_not_effective"
    if model.expires_at is not None and checked_at >= model.expires_at:
        return "model_metadata_expired"
    if surface not in model.surfaces:
        return "surface_not_supported"
    if rule.effort not in model.efforts:
        return "effort_not_supported"
    if rule.allowed_model_tags and rule.allowed_model_tags.isdisjoint(model.tags):
        return "model_tag_not_allowed"
    if not rule.required_capabilities.issubset(model.capabilities):
        return "required_capability_missing"
    if (
        model.max_context_tokens is not None
        and profile.context_tokens_estimate > model.max_context_tokens
    ):
        return "context_window_too_small"
    if model.input_cost_per_million is None or model.output_cost_per_million is None:
        return "price_unknown"
    if (
        rule.max_input_cost_per_million is not None
        and model.input_cost_per_million > rule.max_input_cost_per_million
    ):
        return "input_price_above_rule_limit"
    if (
        rule.max_output_cost_per_million is not None
        and model.output_cost_per_million > rule.max_output_cost_per_million
    ):
        return "output_price_above_rule_limit"
    return None


def _expected_cost(model: ModelSpec) -> Decimal:
    if model.input_cost_per_million is None or model.output_cost_per_million is None:
        raise AssertionError("eligible routing candidates must have known prices")
    return model.input_cost_per_million + model.output_cost_per_million


def _validated_rules(rules: Iterable[PolicyRule]) -> dict[str, PolicyRule]:
    by_name: dict[str, PolicyRule] = {}
    for rule in rules:
        if rule.name in by_name:
            raise PolicyConfigurationError(f"duplicate policy rule: {rule.name}")
        by_name[rule.name] = rule
    for rule in by_name.values():
        if rule.fallback_rule != "__current__" and rule.fallback_rule not in by_name:
            raise PolicyConfigurationError(
                f"unknown fallback rule {rule.fallback_rule!r} in {rule.name!r}"
            )
    for start in by_name:
        seen: set[str] = set()
        current = start
        while current != "__current__":
            if current in seen:
                raise PolicyConfigurationError(f"fallback cycle detected from {start!r}")
            seen.add(current)
            current = by_name[current].fallback_rule
    return by_name


def validate_policy_toml(source: str) -> PolicyDocument:
    if len(source.encode("utf-8")) > 1_000_000:
        raise PolicyConfigurationError("policy exceeds the 1 MB size limit")
    try:
        payload = tomllib.loads(source)
        document = PolicyDocument.model_validate(payload)
        _validated_rules(document.rules)
        return document
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise PolicyConfigurationError("policy validation failed") from exc
