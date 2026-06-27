from loopguard.providers.base import LLMResult, LLMProvider


class _FakeProvider:
    model = "fake/model"

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        return LLMResult(text="ok", prompt_tokens=10, completion_tokens=5, total_tokens=15)


def test_llmresult_defaults():
    r = LLMResult(text="hi")
    assert r.tool_calls == [] and r.cost_usd == 0.0 and r.total_tokens == 0


def test_fake_satisfies_protocol():
    p = _FakeProvider()
    assert isinstance(p, LLMProvider)
    out = p.complete([{"role": "user", "content": "x"}])
    assert out.text == "ok" and out.total_tokens == 15


def test_litellm_provider_normalizes_response(monkeypatch):
    from loopguard.providers.litellm_provider import LiteLLMProvider

    class _Msg:
        content = "hello"
        tool_calls = []

    class _Usage:
        prompt_tokens = 100
        completion_tokens = 20
        total_tokens = 120

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]
        usage = _Usage()

    import loopguard.providers.litellm_provider as mod

    fake_litellm = type("L", (), {
        "completion": staticmethod(lambda **kw: _Resp()),
        "completion_cost": staticmethod(lambda **kw: 0.0012),
    })()
    monkeypatch.setattr(mod, "_import_litellm", lambda: fake_litellm)
    p = LiteLLMProvider("openai/gpt-4o")
    out = p.complete([{"role": "user", "content": "hi"}])
    assert out.text == "hello"
    assert out.prompt_tokens == 100 and out.completion_tokens == 20
    assert out.cost_usd == 0.0012
