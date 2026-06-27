from __future__ import annotations

from typing import Any

from loopguard.event import LoopEvent
from loopguard.guard import LoopGuard
from loopguard.pricing import cost_for

try:  # subclass the real base when litellm is installed; duck-type otherwise
    from litellm.integrations.custom_logger import CustomLogger as _Base
except Exception:  # noqa: BLE001
    class _Base:  # minimal stand-in
        pass


def _usage(response_obj: Any) -> tuple[int, int, int]:
    usage = getattr(response_obj, "usage", None) or {}
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    pt = int(get("prompt_tokens") or 0)
    ct = int(get("completion_tokens") or 0)
    tt = int(get("total_tokens") or (pt + ct))
    return pt, ct, tt


def _model(kwargs: dict, response_obj: Any) -> str:
    return (kwargs or {}).get("model") or getattr(response_obj, "model", "") or "unknown"


def _cost(kwargs: dict, response_obj: Any, pt: int, ct: int) -> float:
    try:
        import litellm

        c = litellm.completion_cost(completion_response=response_obj)
        if c:
            return float(c)
    except Exception:  # noqa: BLE001
        pass
    return cost_for(_model(kwargs, response_obj), pt, ct)


class LoopGuardLiteLLMCallback(_Base):
    def __init__(self, guard: LoopGuard, run_id: str = "litellm", agent: str = "litellm"):
        super().__init__()
        self.guard = guard
        self.run_id = run_id
        self.agent = agent

    def log_success_event(self, kwargs, response_obj, start_time=None, end_time=None):
        pt, ct, tt = _usage(response_obj)
        self.guard.observe(
            LoopEvent(
                run_id=self.run_id,
                agent=self.agent,
                kind="llm_call",
                input_text=str((kwargs or {}).get("messages", "")),
                output_text=str(response_obj),
                tokens=tt,
                cost_usd=_cost(kwargs, response_obj, pt, ct),
                metadata={"model": _model(kwargs, response_obj)},
            )
        )

    def log_failure_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.guard.observe(
            LoopEvent(
                run_id=self.run_id,
                agent=self.agent,
                kind="error",
                error=str(response_obj),
                metadata={"model": _model(kwargs, response_obj)},
            )
        )

    async def async_log_success_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.log_failure_event(kwargs, response_obj, start_time, end_time)
