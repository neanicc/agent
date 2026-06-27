from __future__ import annotations

from typing import Any

from loopguard.event import LoopEvent
from loopguard.guard import LoopGuard


class LoopGuardLiteLLMCallback:
    def __init__(self, guard: LoopGuard, run_id: str = "litellm", agent: str = "litellm"):
        self.guard = guard
        self.run_id = run_id
        self.agent = agent

    def log_pre_api_call(self, model: str, messages: list[dict[str, Any]], kwargs: dict[str, Any]):
        self.guard.observe(
            LoopEvent(
                run_id=self.run_id,
                agent=self.agent,
                kind="llm_call",
                input_text=str(messages),
                metadata={"model": model, **(kwargs or {})},
            )
        )

    def log_success_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time=None, end_time=None
    ):
        self.guard.observe(
            LoopEvent(
                run_id=self.run_id,
                agent=self.agent,
                kind="agent_message",
                output_text=str(response_obj),
                metadata=kwargs or {},
            )
        )

    def log_failure_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time=None, end_time=None
    ):
        self.guard.observe(
            LoopEvent(
                run_id=self.run_id,
                agent=self.agent,
                kind="error",
                error=str(response_obj),
                metadata=kwargs or {},
            )
        )

    async def async_log_success_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.log_failure_event(kwargs, response_obj, start_time, end_time)
