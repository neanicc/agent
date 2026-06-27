from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .event import LoopEvent


class LoopDecision(BaseModel):
    allowed: bool = True
    tripped: bool = False
    reason: str = "allowed"
    detector: str | None = None
    similarity: float | None = None
    matching_events: list[LoopEvent] = Field(default_factory=list)
    suggested_message: str | None = None
    judged: bool = False
    judge_reasoning: str | None = None
    judge_confidence: float | None = None
    developer_action: Literal["terminate", "continue_once", "allowlist", "inject", "none"] = "none"
