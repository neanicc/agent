from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResult:
    text: str
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    raw: Any = None


@runtime_checkable
class LLMProvider(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict],
        *,
        tools: list | None = None,
        temperature: float = 0.2,
        max_tokens: int = 512,
    ) -> LLMResult: ...
