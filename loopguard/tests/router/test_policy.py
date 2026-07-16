from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from loopguard.router.catalog import ModelCatalog, ModelSpec
from loopguard.router.policy import PolicyConfigurationError, RouterPolicy, RoutingPolicyError

NOW = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("task_type", "risk", "phase", "model", "effort"),
    [
        ("search", "low", "implement", "fast", "low"),
        ("edit", "medium", "implement", "standard", "medium"),
        ("migration", "high", "plan", "deep", "high"),
        ("migration", "high", "verify", "standard", "high"),
    ],
)
def test_default_policy(task_type, risk, phase, model, effort, catalog, profile) -> None:
    decision = RouterPolicy.default(catalog).route(profile(task_type, risk), phase, at=NOW)

    assert (decision.model_id, decision.effort) == (model, effort)
    assert decision.automatic is True
    assert decision.policy_version == "deterministic-v1"
    assert decision.matched_rule
    assert decision.reason is None


def test_candidate_tie_breaks_by_exact_cost_then_stable_model_id(catalog, profile) -> None:
    policy = RouterPolicy.from_toml(
        """
version = "tie-v1"

[[rules]]
name = "candidate"
priority = 10
phases = ["implement"]
task_types = ["edit"]
risks = ["medium"]
allowed_model_tags = ["candidate"]
effort = "medium"
max_input_cost_per_million = "10"
max_output_cost_per_million = "10"
fallback_rule = "__current__"
""",
        _candidate_catalog(),
        current_model_id="current",
        current_effort="medium",
    )

    decision = policy.route(profile("edit", "medium"), "implement", at=NOW)

    assert decision.model_id == "alpha"
    assert decision.considered_models == ["alpha", "beta", "costly", "current"]
    assert decision.rejected["costly"] == "higher_ranked_candidate"


def test_equal_priority_rules_tie_break_by_cost_before_rule_name(profile) -> None:
    catalog = ModelCatalog(
        [
            _model(
                "expensive",
                tags={"a-tag"},
                input_price=Decimal("5"),
                output_price=Decimal("5"),
            ),
            _model(
                "cheap",
                tags={"z-tag"},
                input_price=Decimal("1"),
                output_price=Decimal("1"),
            ),
        ]
    )
    policy = RouterPolicy.from_toml(
        """
version = "priority-v1"

[[rules]]
name = "alphabetically-first"
priority = 10
phases = ["implement"]
allowed_model_tags = ["a-tag"]
effort = "medium"
fallback_rule = "__current__"

[[rules]]
name = "alphabetically-last"
priority = 10
phases = ["implement"]
allowed_model_tags = ["z-tag"]
effort = "medium"
fallback_rule = "__current__"
""",
        catalog,
    )

    decision = policy.route(profile("edit", "medium"), "implement", at=NOW)

    assert decision.model_id == "cheap"
    assert decision.matched_rule == "alphabetically-last"
    assert decision.rejected["expensive"] == "higher_ranked_candidate"


def test_policy_explains_rejections_and_follows_named_fallback(profile) -> None:
    policy = RouterPolicy.from_toml(
        """
version = "fallback-v1"

[[rules]]
name = "cheap-deep"
priority = 100
phases = ["plan"]
task_types = ["migration"]
risks = ["high"]
allowed_model_tags = ["deep"]
effort = "high"
max_output_cost_per_million = "1"
fallback_rule = "balanced"

[[rules]]
name = "balanced"
priority = 1
phases = ["plan"]
allowed_model_tags = ["standard"]
effort = "high"
max_output_cost_per_million = "10"
fallback_rule = "__current__"
""",
        _fallback_catalog(),
        current_model_id="standard",
        current_effort="high",
    )

    decision = policy.route(profile("migration", "high"), "plan", at=NOW)

    assert decision.model_id == "standard"
    assert decision.matched_rule == "balanced"
    assert decision.fallback_path == ["cheap-deep", "balanced"]
    assert decision.rejected["deep"] == "output_price_above_rule_limit"


def test_surface_without_selection_preserves_current_model_and_effort(catalog, profile) -> None:
    decision = RouterPolicy.default(catalog).route(
        profile("edit", "medium"),
        "implement",
        surface="attached",
        can_select_model=False,
        current_model_id="user-selected",
        current_effort="high",
        at=NOW,
    )

    assert (decision.model_id, decision.effort) == ("user-selected", "high")
    assert decision.automatic is False
    assert decision.reason == "surface_capability_unavailable"
    assert decision.rejected == {"automatic": "surface_capability_unavailable"}


def test_stale_catalog_disables_automatic_routing(catalog, profile) -> None:
    stale = ModelCatalog(
        catalog.models,
        catalog_version=catalog.catalog_version,
        status="stale",
        stale_reason="signature_invalid",
    )
    policy = RouterPolicy.default(
        stale,
        current_model_id="standard",
        current_effort="medium",
    )

    decision = policy.route(profile("search", "low"), "implement", at=NOW)

    assert decision.model_id == "standard"
    assert decision.automatic is False
    assert decision.reason == "catalog_stale:signature_invalid"


def test_unknown_prices_cannot_win_automatic_cost_ranking(profile) -> None:
    base = _model("unknown", tags={"candidate"}, input_price=None, output_price=None)
    catalog = ModelCatalog([base])
    policy = RouterPolicy.from_toml(
        """
version = "unknown-v1"

[[rules]]
name = "candidate"
priority = 1
phases = ["implement"]
allowed_model_tags = ["candidate"]
effort = "medium"
fallback_rule = "__current__"
""",
        catalog,
        current_model_id="existing",
        current_effort="medium",
    )

    decision = policy.route(profile("edit", "medium"), "implement", at=NOW)

    assert decision.model_id == "existing"
    assert decision.automatic is False
    assert decision.reason == "no_eligible_model"
    assert decision.rejected["unknown"] == "price_unknown"


@pytest.mark.parametrize(
    ("policy_text", "message"),
    [
        (
            """
version = "bad"
[[rules]]
name = "one"
priority = 1
phases = ["implement"]
effort = "medium"
fallback_rule = "missing"
""",
            "unknown fallback",
        ),
        (
            """
version = "bad"
[[rules]]
name = "one"
priority = 1
phases = ["implement"]
effort = "medium"
fallback_rule = "two"
[[rules]]
name = "two"
priority = 2
phases = ["implement"]
effort = "medium"
fallback_rule = "one"
""",
            "cycle",
        ),
        ("version = 'bad'\nunknown = true", "validation"),
    ],
)
def test_invalid_policy_configuration_fails_closed(catalog, policy_text, message) -> None:
    with pytest.raises(PolicyConfigurationError, match=message):
        RouterPolicy.from_toml(policy_text, catalog)


def test_missing_current_model_is_an_explicit_error(catalog, profile) -> None:
    with pytest.raises(RoutingPolicyError, match="current model"):
        RouterPolicy.default(catalog).route(
            profile("edit", "medium"),
            "implement",
            can_select_model=False,
            at=NOW,
        )


def _model(
    model_id: str,
    *,
    tags: set[str],
    input_price: Decimal | None,
    output_price: Decimal | None,
) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        provider="test",
        surfaces={"managed"},
        efforts={"medium", "high"},
        tags=tags,
        input_cost_per_million=input_price,
        output_cost_per_million=output_price,
        max_context_tokens=256_000,
        source="test-policy-catalog",
        source_kind="test",
        observed_at=NOW,
        effective_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=1),
    )


def _candidate_catalog() -> ModelCatalog:
    return ModelCatalog(
        [
            _model(
                "current",
                tags={"current"},
                input_price=Decimal("5"),
                output_price=Decimal("5"),
            ),
            _model(
                "costly",
                tags={"candidate"},
                input_price=Decimal("2"),
                output_price=Decimal("3"),
            ),
            _model(
                "beta",
                tags={"candidate"},
                input_price=Decimal("1"),
                output_price=Decimal("1"),
            ),
            _model(
                "alpha",
                tags={"candidate"},
                input_price=Decimal("1"),
                output_price=Decimal("1"),
            ),
        ]
    )


def _fallback_catalog() -> ModelCatalog:
    return ModelCatalog(
        [
            _model(
                "deep",
                tags={"deep"},
                input_price=Decimal("10"),
                output_price=Decimal("20"),
            ),
            _model(
                "standard",
                tags={"standard"},
                input_price=Decimal("3"),
                output_price=Decimal("6"),
            ),
        ]
    )
