from __future__ import annotations

from ..pricing import cost_for
from .base import LLMResult


def _import_litellm():
    try:
        import litellm
    except ImportError as exc:  # pragma: no cover - exercised via make_provider
        raise RuntimeError('Install litellm support with: pip install "loopguard[litellm]"') from exc
    return litellm


class LiteLLMProvider:
    def __init__(self, model: str):
        self.model = model
        self._litellm = _import_litellm()

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512) -> LLMResult:
        resp = self._litellm.completion(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        tt = int(getattr(usage, "total_tokens", 0) or (pt + ct))
        cost = 0.0
        try:
            cost = float(self._litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:
            cost = 0.0
        if not cost:
            cost = cost_for(self.model, pt, ct)
        return LLMResult(
            text=getattr(msg, "content", "") or "",
            tool_calls=list(getattr(msg, "tool_calls", None) or []),
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            cost_usd=cost,
            raw=resp,
        )
