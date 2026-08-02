from __future__ import annotations

import threading
from dataclasses import dataclass
from decimal import Decimal


_SERVICES = ("judge", "context", "visual_critic")


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    service: str
    requested: Decimal
    allowed: bool
    reserved: Decimal
    remaining: Decimal
    reason: str


class SessionBudget:
    """Thread-safe optional-service allocations beneath a hard session ceiling."""

    def __init__(
        self,
        *,
        total: Decimal,
        guard_overhead: Decimal,
        judge: Decimal = Decimal("0"),
        context: Decimal = Decimal("0"),
        visual_critic: Decimal = Decimal("0"),
    ) -> None:
        values = {
            "total": total,
            "guard_overhead": guard_overhead,
            "judge": judge,
            "context": context,
            "visual_critic": visual_critic,
        }
        for name, value in values.items():
            _validate_decimal(value, name)
        if guard_overhead > total:
            raise ValueError("guard overhead cannot exceed the total session budget")
        allocations = {"judge": judge, "context": context, "visual_critic": visual_critic}
        if sum(allocations.values(), Decimal("0")) > guard_overhead:
            raise ValueError("optional service allocations cannot exceed guard overhead")
        self.total = total
        self.guard_overhead = guard_overhead
        self._allocations = allocations
        self._spent = {service: Decimal("0") for service in _SERVICES}
        self._lock = threading.Lock()

    def reserve(self, service: str, amount: Decimal) -> BudgetReservation:
        if service not in self._allocations:
            return BudgetReservation(
                service=service,
                requested=amount if isinstance(amount, Decimal) else Decimal("0"),
                allowed=False,
                reserved=Decimal("0"),
                remaining=Decimal("0"),
                reason="unknown_service",
            )
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            return BudgetReservation(
                service=service,
                requested=amount if isinstance(amount, Decimal) else Decimal("0"),
                allowed=False,
                reserved=Decimal("0"),
                remaining=self.remaining(service),
                reason="invalid_amount",
            )
        with self._lock:
            service_remaining = self._allocations[service] - self._spent[service]
            overhead_remaining = self.guard_overhead - sum(self._spent.values(), Decimal("0"))
            remaining = min(service_remaining, overhead_remaining)
            if amount > service_remaining:
                return BudgetReservation(
                    service, amount, False, Decimal("0"), remaining, "service_budget_exhausted"
                )
            if amount > overhead_remaining:
                return BudgetReservation(
                    service, amount, False, Decimal("0"), remaining, "guard_overhead_exhausted"
                )
            self._spent[service] += amount
            remaining = min(
                self._allocations[service] - self._spent[service],
                self.guard_overhead - sum(self._spent.values(), Decimal("0")),
            )
            return BudgetReservation(service, amount, True, amount, remaining, "reserved")

    def spent(self, service: str) -> Decimal:
        if service not in self._spent:
            raise KeyError(service)
        with self._lock:
            return self._spent[service]

    def remaining(self, service: str) -> Decimal:
        if service not in self._spent:
            raise KeyError(service)
        with self._lock:
            return min(
                self._allocations[service] - self._spent[service],
                self.guard_overhead - sum(self._spent.values(), Decimal("0")),
            )

    @property
    def total_reserved(self) -> Decimal:
        with self._lock:
            return sum(self._spent.values(), Decimal("0"))


@dataclass(frozen=True, slots=True)
class ContextBudgetDecision:
    allowed: bool
    characters: int
    tokens_estimate: int
    reason: str


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_characters: int
    max_tokens_estimate: int

    def __post_init__(self) -> None:
        if self.max_characters <= 0 or self.max_tokens_estimate <= 0:
            raise ValueError("context limits must be positive")

    def check(self, *, characters: int, tokens_estimate: int) -> ContextBudgetDecision:
        if characters < 0 or tokens_estimate < 0:
            return ContextBudgetDecision(False, characters, tokens_estimate, "invalid_estimate")
        if characters > self.max_characters:
            return ContextBudgetDecision(
                False, characters, tokens_estimate, "character_limit_exceeded"
            )
        if tokens_estimate > self.max_tokens_estimate:
            return ContextBudgetDecision(False, characters, tokens_estimate, "token_limit_exceeded")
        return ContextBudgetDecision(True, characters, tokens_estimate, "within_limits")


def _validate_decimal(value: object, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")
