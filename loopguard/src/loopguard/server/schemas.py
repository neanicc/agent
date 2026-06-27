from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..decision import LoopDecision
from ..event import LoopEvent


class StartRunRequest(BaseModel):
    project_id: str = "npm-manifest"
    mode: Literal["flag", "auto"] = "flag"
    model: str = "cerebras/gpt-oss-120b"
    provider: str = "auto"
    task: str | None = None  # override the project task (used by the custom project)


class InterveneRequest(BaseModel):
    action: Literal["terminate", "approve", "inject", "continue_once", "allowlist"]
    message: str | None = None


def _total_cost(totals: dict) -> float:
    return float(totals.get("cost_usd", 0.0)) + float(totals.get("judge_cost_usd", 0.0))


def event_message(event: LoopEvent, decision: LoopDecision, totals: dict, step: int = 0) -> dict:
    is_err = bool(event.error)
    return {
        "type": "event",
        "data": {
            "step": step,
            "agent": event.agent,
            "tool": event.tool_name,
            "args": event.tool_args or {},
            "output": (event.error or event.output_text or "")[:300],
            "is_error": is_err,
            "tokens": event.tokens,
            "cost_usd": event.cost_usd,
            "total_tokens": totals.get("tokens", 0),
            "total_cost": _total_cost(totals),  # agent + judge spend (the true total)
            "agent_cost": totals.get("cost_usd", 0.0),
            "judge_cost": totals.get("judge_cost_usd", 0.0),
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


def auto_fix_message(decision: LoopDecision, step: int, applied: bool) -> dict:
    """Auto-mode log entry: the guard caught a loop and (usually) injected a fix."""
    return {
        "type": "auto_fix",
        "data": {
            "step": step,
            "detector": decision.detector,
            "judge_reasoning": decision.judge_reasoning,
            "judge_confidence": decision.judge_confidence,
            "applied_fix": decision.suggested_message if applied else None,
            "terminated": not applied,
        },
    }


def allowlist_message(tools: list[str], all_tools: list[str]) -> dict:
    """Emitted when the operator allowlists the tool(s) behind a flagged loop."""
    return {
        "type": "allowlisted",
        "data": {"tools": tools, "allowlist": all_tools},
    }
