from __future__ import annotations

from .base import LLMProvider, LLMResult


def make_provider(model: str = "cerebras/gpt-oss-120b", provider: str = "auto") -> LLMProvider:
    """Build a provider. provider in {"auto","litellm","cerebras"}.

    "auto": prefer LiteLLM (universal); if litellm is not installed, fall back to the
    direct Cerebras SDK when the model is a cerebras model.
    """
    if provider == "cerebras":
        from .cerebras_provider import CerebrasProvider

        return CerebrasProvider(model=model.split("/")[-1])
    if provider == "litellm":
        from .litellm_provider import LiteLLMProvider

        return LiteLLMProvider(model=model)
    if provider == "auto":
        try:
            from .litellm_provider import LiteLLMProvider

            return LiteLLMProvider(model=model)
        except RuntimeError:
            from .cerebras_provider import CerebrasProvider

            return CerebrasProvider(model=model.split("/")[-1])
    raise ValueError(f"Unknown provider {provider!r}")


__all__ = ["LLMProvider", "LLMResult", "make_provider"]
