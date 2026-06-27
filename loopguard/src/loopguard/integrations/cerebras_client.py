from __future__ import annotations

import os
from typing import Any

MISSING_KEY_MESSAGE = (
    "CEREBRAS_API_KEY is not set. Run the local demo without Cerebras or set your Cerebras key."
)


class CerebrasLLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("CEREBRAS_API_KEY")
        if not self.api_key:
            raise RuntimeError(MISSING_KEY_MESSAGE)
        self.model = model or os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
        try:
            from cerebras.cloud.sdk import Cerebras
        except ImportError as exc:
            raise RuntimeError(
                'Install Cerebras support with: pip install "loopguard[cerebras]"'
            ) from exc
        self.client = Cerebras(api_key=self.api_key)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools=None,
        parallel_tool_calls: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 512,
        response_format=None,
    ):
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            tools=tools,
            parallel_tool_calls=parallel_tool_calls,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format is not None:
            kwargs["response_format"] = response_format
        return self.client.chat.completions.create(**kwargs)
