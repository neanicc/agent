from loopguard.judge import JudgeVerdict, LLMJudge
from loopguard.providers.base import LLMResult

from conftest import tool_event


class _ScriptedProvider:
    model = "fake/model"

    def __init__(self, text):
        self._text = text

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        return LLMResult(text=self._text, prompt_tokens=50, completion_tokens=20, cost_usd=0.0007)


def test_judge_confirms_loop_and_extracts_correction():
    provider = _ScriptedProvider(
        '{"is_loop": true, "reasoning": "same file repeatedly",'
        ' "suggested_correction": "Read pyproject.toml instead", "confidence": 0.9}'
    )
    verdict = LLMJudge(provider).judge([tool_event(), tool_event()], task="find package.json")
    assert verdict.is_loop is True
    assert verdict.suggested_correction == "Read pyproject.toml instead"
    assert verdict.confidence == 0.9
    assert verdict.cost_usd == 0.0007


def test_judge_suppresses_false_positive():
    provider = _ScriptedProvider(
        'Here is my answer: {"is_loop": false, "reasoning": "making progress",'
        ' "suggested_correction": null, "confidence": 0.8}'
    )
    verdict = LLMJudge(provider).judge([tool_event()], task="t")
    assert verdict.is_loop is False
    assert verdict.suggested_correction is None


def test_judge_unparseable_defers_to_detector():
    verdict = LLMJudge(_ScriptedProvider("not json at all")).judge([tool_event()])
    assert verdict.is_loop is True  # fail-safe: defer to Layer 1
    assert verdict.confidence == 0.0


def test_judge_provider_error_defers_to_detector():
    class _Boom:
        model = "x"

        def complete(self, *a, **k):
            raise RuntimeError("network down")

    verdict = LLMJudge(_Boom()).judge([tool_event()])
    assert verdict.is_loop is True
    assert verdict.confidence == 0.0
