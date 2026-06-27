from __future__ import annotations

import logging

_LOG = logging.getLogger("loopguard.pricing")

# $ per 1,000,000 tokens: (input, output). Extend as needed.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-oss-120b": (0.25, 0.69),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-3-5-sonnet-latest": (3.00, 15.00),
    "claude-3-5-haiku-latest": (0.80, 4.00),
}

_WARNED: set[str] = set()


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    key = model.lower()
    price = PRICING.get(key) or PRICING.get(key.split("/")[-1])
    if price is None:
        if model not in _WARNED:
            _LOG.warning("No pricing entry for model %r; reporting $0.00", model)
            _WARNED.add(model)
        return 0.0
    price_in, price_out = price
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000
