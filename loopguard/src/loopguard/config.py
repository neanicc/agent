from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoopGuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_size: int = Field(default=8, gt=0, le=100_000)
    trip_count: int = Field(default=3, gt=0, le=10_000)
    semantic_threshold: float = Field(default=0.86, ge=0, le=1)
    exact_threshold: int = Field(default=3, gt=0, le=10_000)
    enable_semantic: bool = True
    enable_exact: bool = True
    enable_pingpong: bool = True
    enable_budget: bool = True
    max_tool_calls: int | None = Field(default=30, gt=0)
    max_cost_usd: float | None = Field(default=1.00, ge=0)
    action: Literal["pause", "raise", "warn", "flag", "auto"] = "pause"
    enable_judge: bool = True
    judge_reservation_usd: Decimal = Field(default=Decimal("0.01"), ge=0, allow_inf_nan=False)
    judge_policy_version: str = Field(default="detector-policy-v1", min_length=1, max_length=128)
    judge_prompt_version: str = Field(default="judge-prompt-v1", min_length=1, max_length=128)
    judge_cache_ttl_seconds: float = Field(default=300, gt=0, le=86_400)
    judge_cache_max_entries: int = Field(default=512, gt=0, le=100_000)
    allowlisted_tools: list[str] = Field(default_factory=list)
    ignored_arg_keys: list[str] = Field(
        default_factory=lambda: ["timestamp", "nonce", "request_id", "trace_id", "session_id"]
    )
    redact_keys: list[str] = Field(
        default_factory=lambda: ["api_key", "token", "password", "secret", "authorization"]
    )
