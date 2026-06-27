from loopguard import LoopGuard, LoopGuardConfig
from loopguard.judge import JudgeVerdict

from conftest import tool_event


class _StubJudge:
    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = 0

    def judge(self, events, task=None, detector=None):
        self.calls += 1
        return self._verdict


def _trip(guard):
    d = None
    for path in ["./package.json", "package.json", "/app/package.json"]:
        d = guard.observe(tool_event(path), task="find package.json")
    return d


def test_judge_false_positive_is_suppressed():
    judge = _StubJudge(JudgeVerdict(is_loop=False, reasoning="progress"))
    guard = LoopGuard(
        LoopGuardConfig(action="flag", enable_exact=False, enable_budget=False), judge=judge
    )
    d = _trip(guard)
    assert judge.calls == 1
    assert d.tripped is False and d.allowed is True
    assert d.judged is True


def test_auto_mode_injects_correction():
    judge = _StubJudge(
        JudgeVerdict(is_loop=True, reasoning="stuck", suggested_correction="read pyproject.toml")
    )
    guard = LoopGuard(
        LoopGuardConfig(action="auto", enable_exact=False, enable_budget=False), judge=judge
    )
    d = _trip(guard)
    assert d.developer_action == "inject"
    assert d.allowed is True
    assert d.suggested_message == "read pyproject.toml"
    assert d.judge_reasoning == "stuck"


def test_flag_mode_is_non_blocking():
    judge = _StubJudge(JudgeVerdict(is_loop=True, reasoning="stuck"))
    guard = LoopGuard(
        LoopGuardConfig(action="flag", enable_exact=False, enable_budget=False), judge=judge
    )
    d = _trip(guard)
    assert d.tripped is True and d.allowed is True
    assert d.developer_action == "none"


def test_summary_tracks_judge_cost():
    judge = _StubJudge(JudgeVerdict(is_loop=True, reasoning="stuck", cost_usd=0.002))
    guard = LoopGuard(
        LoopGuardConfig(action="flag", enable_exact=False, enable_budget=False), judge=judge
    )
    _trip(guard)
    assert guard.summary()["judge_cost_usd"] == 0.002
