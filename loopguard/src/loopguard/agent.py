from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .event import LoopEvent
from .guard import LoopGuard
from .providers.base import LLMProvider


@dataclass
class AgentResult:
    steps: int
    final_text: str | None
    stopped_by_guard: bool


def _tc_id(tc: Any) -> str:
    return getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "call")


def _tc_name_args(tc: Any) -> tuple[str, dict]:
    fn = getattr(tc, "function", None)
    if fn is None and isinstance(tc, dict):
        fn = tc.get("function", {})
    name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else "tool")
    raw = getattr(fn, "arguments", None)
    if raw is None and isinstance(fn, dict):
        raw = fn.get("arguments", "{}")
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:  # noqa: BLE001 - malformed tool args become empty
        args = {}
    return name, args


def _serialize_tc(tc: Any) -> dict:
    name, _ = _tc_name_args(tc)
    fn = getattr(tc, "function", None)
    raw = getattr(fn, "arguments", None) if fn is not None else "{}"
    if not isinstance(raw, str):
        raw = json.dumps(raw or {})
    return {"id": _tc_id(tc), "type": "function", "function": {"name": name, "arguments": raw}}


def run_agent(
    provider: LLMProvider,
    *,
    system: str,
    task: str,
    tools_schema: list[dict],
    tool_impls: dict[str, Callable[..., str]],
    guard: LoopGuard | None = None,
    run_id: str = "agent",
    agent_name: str = "agent",
    max_steps: int = 14,
    on_event: Callable[[int, str, dict, str], None] | None = None,
) -> AgentResult:
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    for step in range(1, max_steps + 1):
        result = provider.complete(messages, tools=tools_schema)
        entry: dict[str, Any] = {"role": "assistant", "content": result.text or ""}
        if result.tool_calls:
            entry["tool_calls"] = [_serialize_tc(tc) for tc in result.tool_calls]
        messages.append(entry)

        if not result.tool_calls:
            return AgentResult(step, result.text, False)

        correction: str | None = None
        first = True
        for tc in result.tool_calls:
            name, args = _tc_name_args(tc)
            impl = tool_impls.get(name)
            output = impl(**args) if impl else f"Error: unknown tool {name}"
            if on_event:
                on_event(step, name, args, output)
            messages.append({"role": "tool", "tool_call_id": _tc_id(tc), "content": output})
            is_err = output.startswith("Error:")
            if guard is not None:
                event = LoopEvent(
                    run_id=run_id,
                    agent=agent_name,
                    kind="tool_call",
                    tool_name=name,
                    tool_args=args,
                    output_text=None if is_err else output,
                    error=output if is_err else None,
                    tokens=result.total_tokens if first else 0,
                    cost_usd=result.cost_usd if first else 0.0,
                )
                decision = guard.observe(event, task=task)
                if not decision.allowed:
                    return AgentResult(step, None, True)
                if decision.suggested_message:
                    correction = decision.suggested_message
            first = False

        if correction:
            messages.append({"role": "user", "content": correction})
    return AgentResult(max_steps, None, False)


@dataclass
class _AgentCtx:
    name: str
    messages: list[dict]
    done: bool = False
    final_text: str | None = None


def run_multi_agent(
    provider: LLMProvider,
    *,
    agents: list[tuple[str, str]],  # (agent_name, system_prompt)
    task: str,
    tools_schema: list[dict],
    tool_impls: dict[str, Callable[..., str]],
    guard: LoopGuard | None = None,
    run_id: str = "multi",
    max_steps: int = 12,
    on_event: Callable[[int, str, dict, str], None] | None = None,
) -> AgentResult:
    """Run several real agents round-robin, one step each, against a shared guard.

    The agents genuinely interleave (A-B-A-B), so when they share a wrong assumption
    their tool calls oscillate and LoopGuard's ping-pong / semantic detectors trip.
    A judge correction is injected into every agent so the team breaks out together.
    """
    ctxs = [
        _AgentCtx(name=name, messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ])
        for name, system in agents
    ]
    step = 0
    for _round in range(max_steps):
        for ctx in ctxs:
            if ctx.done:
                continue
            step += 1
            result = provider.complete(ctx.messages, tools=tools_schema)
            entry: dict[str, Any] = {"role": "assistant", "content": result.text or ""}
            if result.tool_calls:
                entry["tool_calls"] = [_serialize_tc(tc) for tc in result.tool_calls]
            ctx.messages.append(entry)
            if not result.tool_calls:
                ctx.done = True
                ctx.final_text = result.text
                continue
            correction: str | None = None
            first = True
            for tc in result.tool_calls:
                name, args = _tc_name_args(tc)
                impl = tool_impls.get(name)
                output = impl(**args) if impl else f"Error: unknown tool {name}"
                if on_event:
                    on_event(step, ctx.name, args, output)
                ctx.messages.append(
                    {"role": "tool", "tool_call_id": _tc_id(tc), "content": output}
                )
                is_err = output.startswith("Error:")
                if guard is not None:
                    event = LoopEvent(
                        run_id=run_id,
                        agent=ctx.name,
                        kind="tool_call",
                        tool_name=name,
                        tool_args=args,
                        output_text=None if is_err else output,
                        error=output if is_err else None,
                        tokens=result.total_tokens if first else 0,
                        cost_usd=result.cost_usd if first else 0.0,
                    )
                    decision = guard.observe(event, task=task)
                    if not decision.allowed:
                        return AgentResult(step, None, True)
                    if decision.suggested_message:
                        correction = decision.suggested_message
                first = False
            if correction:
                for c in ctxs:  # inject into every agent so the team recovers together
                    c.messages.append({"role": "user", "content": correction})
        if all(c.done for c in ctxs):
            break
    final = next((c.final_text for c in ctxs if c.final_text), None)
    return AgentResult(step, final, False)
