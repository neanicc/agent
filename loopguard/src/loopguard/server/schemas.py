from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..decision import LoopDecision
from ..event import LoopEvent


class StartRunRequest(BaseModel):
    mode: Literal["flag", "auto"] = "flag"
    model: str = "cerebras/gpt-oss-120b"
    provider: str = "auto"


class InterveneRequest(BaseModel):
    action: Literal["terminate", "approve", "inject", "continue_once"]
    message: str | None = None


def event_message(event: LoopEvent, decision: LoopDecision, totals: dict) -> dict:
    is_err = bool(event.error)
    return {
        "type": "event",
        "data": {
            "tool": event.tool_name,
            "args": event.tool_args or {},
            "output": (event.error or event.output_text or "")[:300],
            "is_error": is_err,
            "tokens": event.tokens,
            "cost_usd": event.cost_usd,
            "total_tokens": totals.get("tokens", 0),
            "total_cost": totals.get("cost_usd", 0.0),
            "tripped": decision.tripped,
        },
    }


def decision_message(decision: LoopDecision) -> dict:
    return {
        "type": "decision_required",
        "data": {
            "detector": decision.detector,
            "similarity": decision.similarity,
            "reason": decision.reason,
            "judge_reasoning": decision.judge_reasoning,
            "judge_confidence": decision.judge_confidence,
            "suggested_message": decision.suggested_message,
        },
    }
