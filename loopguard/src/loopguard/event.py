from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class LoopEvent(BaseModel):
    run_id: str
    agent: str
    kind: Literal["llm_call", "tool_call", "tool_result", "agent_message", "error"]
    input_text: str | None = None
    output_text: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    error: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
