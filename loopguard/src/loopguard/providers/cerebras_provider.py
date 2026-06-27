from __future__ import annotations

from ..pricing import cost_for
from .base import LLMResult


class CerebrasProvider:
    def __init__(self, model: str | None = None):
        from ..integrations.cerebras_client import CerebrasLLMClient

        self._client = CerebrasLLMClient(model=model)
        self.model = self._client.model

    def complete(
        self, messages, *, tools=None, temperature=0.2, max_tokens=512, response_format=None
    ) -> LLMResult:
        resp = self._client.chat(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        tt = int(getattr(usage, "total_tokens", 0) or (pt + ct))
        return LLMResult(
            text=getattr(msg, "content", "") or "",
            tool_calls=list(getattr(msg, "tool_calls", None) or []),
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            cost_usd=cost_for(self.model, pt, ct),
            raw=resp,
        )
