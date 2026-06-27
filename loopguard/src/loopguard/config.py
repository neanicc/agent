from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoopGuardConfig(BaseModel):
    window_size: int = 8
    trip_count: int = 3
    semantic_threshold: float = 0.86
    exact_threshold: int = 3
    enable_semantic: bool = True
    enable_exact: bool = True
    enable_pingpong: bool = True
    enable_budget: bool = True
    max_tool_calls: int | None = 30
    max_cost_usd: float | None = 1.00
    action: Literal["pause", "raise", "warn"] = "pause"
    allowlisted_tools: list[str] = Field(default_factory=list)
    ignored_arg_keys: list[str] = Field(
        default_factory=lambda: ["timestamp", "nonce", "request_id", "trace_id", "session_id"]
    )
    redact_keys: list[str] = Field(
        default_factory=lambda: ["api_key", "token", "password", "secret", "authorization"]
    )
