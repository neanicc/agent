"""LoopGuard's stable public API with dependency-light lazy exports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import AgentResult, run_agent
    from .config import LoopGuardConfig
    from .decision import LoopDecision
    from .event import LoopEvent
    from .exceptions import LoopDetectedError
    from .guard import LoopGuard
    from .judge import JudgeVerdict, LLMJudge
    from .providers import make_provider

__all__ = [
    "LoopGuard",
    "LoopGuardConfig",
    "LoopDecision",
    "LoopDetectedError",
    "LoopEvent",
    "LLMJudge",
    "JudgeVerdict",
    "make_provider",
    "run_agent",
    "AgentResult",
]

_EXPORTS = {
    "AgentResult": ("loopguard.agent", "AgentResult"),
    "JudgeVerdict": ("loopguard.judge", "JudgeVerdict"),
    "LLMJudge": ("loopguard.judge", "LLMJudge"),
    "LoopDecision": ("loopguard.decision", "LoopDecision"),
    "LoopDetectedError": ("loopguard.exceptions", "LoopDetectedError"),
    "LoopEvent": ("loopguard.event", "LoopEvent"),
    "LoopGuard": ("loopguard.guard", "LoopGuard"),
    "LoopGuardConfig": ("loopguard.config", "LoopGuardConfig"),
    "make_provider": ("loopguard.providers", "make_provider"),
    "run_agent": ("loopguard.agent", "run_agent"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'loopguard' has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
