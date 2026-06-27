from loopguard import live_demo
from loopguard.providers.base import LLMResult


class _TC:
    def __init__(self, i):
        self.id = f"c{i}"
        self.type = "function"
        self.function = type(
            "F", (), {"name": "read_file", "arguments": '{"path": "package.json"}'}
        )()


class _FakeProvider:
    model = "fake/model"

    def __init__(self):
        self.n = 0

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        self.n += 1
        # After a correction is injected as a user msg, stop looping with a final answer.
        for m in messages[2:]:
            if m.get("role") == "user" and "pyproject" in str(m.get("content", "")).lower():
                return LLMResult(
                    text="Found it.", prompt_tokens=10, completion_tokens=2, total_tokens=12
                )
        return LLMResult(
            text="",
            tool_calls=[_TC(self.n)],
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            cost_usd=0.001,
        )


def test_run_live_with_mocked_provider_and_judge(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
    fake = _FakeProvider()
    monkeypatch.setattr(live_demo, "make_provider", lambda model, provider: fake)

    from loopguard.judge import JudgeVerdict

    class _Judge:
        def judge(self, events, task=None, detector=None):
            return JudgeVerdict(
                is_loop=True,
                reasoning="stuck on package.json",
                suggested_correction="This is a Python project; read pyproject.toml",
                cost_usd=0.0005,
            )

    monkeypatch.setattr(live_demo, "LLMJudge", lambda provider: _Judge())
    monkeypatch.chdir(tmp_path)
    live_demo.run_live(model="fake/model", mode="auto", root=str(tmp_path))
    out = capsys.readouterr().out
    assert "read_file" in out
    assert "pyproject.toml" in out  # the real correction was injected
