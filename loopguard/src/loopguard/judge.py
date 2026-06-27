from __future__ import annotations

import json

from pydantic import BaseModel

from .event import LoopEvent
from .normalize import normalize_event
from .providers.base import LLMProvider

_JUDGE_SYSTEM = (
    "You are LoopGuard's loop judge. A deterministic detector flagged an AI agent's recent "
    "actions as a possible repetitive loop. Decide whether the agent is genuinely STUCK "
    "(repeating without progress) or actually making progress. If stuck, write a SPECIFIC, "
    "actionable correction telling the agent exactly what to do differently to break out. "
    'Respond with ONLY a JSON object: {"is_loop": boolean, "reasoning": string, '
    '"suggested_correction": string or null, "confidence": number between 0 and 1}.'
)


class JudgeVerdict(BaseModel):
    is_loop: bool
    reasoning: str = ""
    suggested_correction: str | None = None
    confidence: float = 0.5
    cost_usd: float = 0.0


def _defer(reason: str) -> JudgeVerdict:
    # Fail-safe: when the judge cannot decide, defer to Layer 1 (treat as a loop).
    return JudgeVerdict(is_loop=True, reasoning=reason, confidence=0.0)


class LLMJudge:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def judge(
        self,
        events: list[LoopEvent],
        task: str | None = None,
        detector: str | None = None,
    ) -> JudgeVerdict:
        prompt = self._render(events, task, detector)
        try:
            result = self.provider.complete(
                [
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=400,
            )
        except Exception as exc:  # noqa: BLE001 - any provider failure must fail safe
            return _defer(f"judge unavailable ({exc}); deferring to detector")
        verdict = self._parse(result.text)
        verdict.cost_usd = result.cost_usd
        return verdict

    @staticmethod
    def _render(events: list[LoopEvent], task: str | None, detector: str | None) -> str:
        lines: list[str] = []
        if task:
            lines.append(f"Agent task: {task}")
        if detector:
            lines.append(f"Detector that fired: {detector}")
        lines.append("Recent actions (normalized):")
        for i, e in enumerate(events, 1):
            lines.append(f"{i}. {normalize_event(e)}")
        return "\n".join(lines)

    @staticmethod
    def _parse(text: str) -> JudgeVerdict:
        raw = (text or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return JudgeVerdict(
                    is_loop=bool(data.get("is_loop", True)),
                    reasoning=str(data.get("reasoning", "")),
                    suggested_correction=(data.get("suggested_correction") or None),
                    confidence=float(data.get("confidence", 0.5)),
                )
            except Exception:  # noqa: BLE001 - malformed JSON falls through to defer
                pass
        return _defer("unparseable judge output; deferring to detector")
