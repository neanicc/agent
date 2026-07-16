from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from loopguard.router.budgets import ContextBudget, SessionBudget


def test_optional_services_cannot_exceed_guard_overhead() -> None:
    budget = SessionBudget(
        total=Decimal("2.00"),
        guard_overhead=Decimal("0.04"),
        judge=Decimal("0.02"),
        context=Decimal("0.01"),
        visual_critic=Decimal("0.01"),
    )

    assert budget.reserve("judge", Decimal("0.015")).allowed
    denied = budget.reserve("judge", Decimal("0.010"))

    assert denied.allowed is False
    assert denied.reason == "service_budget_exhausted"
    assert denied.remaining == Decimal("0.005")


def test_reservations_are_atomic_under_concurrency() -> None:
    budget = SessionBudget(
        total=Decimal("1"),
        guard_overhead=Decimal("0.05"),
        judge=Decimal("0.05"),
        context=Decimal("0"),
        visual_critic=Decimal("0"),
    )

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(
            executor.map(lambda _index: budget.reserve("judge", Decimal("0.01")), range(20))
        )

    assert sum(result.allowed for result in results) == 5
    assert budget.spent("judge") == Decimal("0.05")
    assert budget.total_reserved == Decimal("0.05")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total": Decimal("0.01"), "guard_overhead": Decimal("0.02")},
        {
            "total": Decimal("1"),
            "guard_overhead": Decimal("0.02"),
            "judge": Decimal("0.02"),
            "context": Decimal("0.01"),
        },
        {"total": Decimal("NaN"), "guard_overhead": Decimal("0")},
        {"total": 1.0, "guard_overhead": Decimal("0")},
    ],
)
def test_invalid_or_float_budget_configuration_is_rejected(kwargs) -> None:
    with pytest.raises((TypeError, ValueError)):
        SessionBudget(**kwargs)


def test_context_budget_enforces_characters_and_tokens_independently() -> None:
    budget = ContextBudget(max_characters=1_000, max_tokens_estimate=100)

    assert budget.check(characters=900, tokens_estimate=90).allowed
    assert budget.check(characters=1_001, tokens_estimate=90).reason == "character_limit_exceeded"
    assert budget.check(characters=900, tokens_estimate=101).reason == "token_limit_exceeded"


def test_unknown_service_and_non_positive_reservations_fail_closed() -> None:
    budget = SessionBudget(
        total=Decimal("1"),
        guard_overhead=Decimal("0.1"),
        judge=Decimal("0.1"),
    )

    assert budget.reserve("unknown", Decimal("0.01")).reason == "unknown_service"
    assert budget.reserve("judge", Decimal("0")).reason == "invalid_amount"
    assert budget.reserve("judge", Decimal("-1")).reason == "invalid_amount"
