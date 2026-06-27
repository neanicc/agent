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


def test_budget_trip_is_never_suppressed_by_judge():
    # A budget cap is a hard ceiling: the judge must not be able to wave it away.
    judge = _StubJudge(JudgeVerdict(is_loop=False, reasoning="looks fine"))
    guard = LoopGuard(
        LoopGuardConfig(
            action="flag",
            max_tool_calls=1,
            enable_exact=False,
            enable_semantic=False,
            enable_pingpong=False,
        ),
        judge=judge,
    )
    assert not guard.observe(tool_event("a")).tripped
    d = guard.observe(tool_event("b"))
    assert d.tripped is True and d.detector == "budget"
    assert judge.calls == 0  # budget never consults the judge


def test_judge_consulted_once_per_run_per_detector():
    judge = _StubJudge(JudgeVerdict(is_loop=True, reasoning="stuck"))
    guard = LoopGuard(
        LoopGuardConfig(
            action="flag", enable_exact=False, enable_budget=False, enable_pingpong=False
        ),
        judge=judge,
    )
    # Trip, then keep feeding similar events that keep the semantic detector tripping.
    _trip(guard)
    for path in ["x/package.json", "y/package.json", "z/package.json"]:
        guard.observe(tool_event(path), task="find package.json")
    assert judge.calls == 1  # cached after the first trip for this (run, detector)


def test_judge_cost_counts_toward_total_budget():
    # Judge spend must not be invisible to the cost ceiling.
    judge = _StubJudge(JudgeVerdict(is_loop=True, reasoning="stuck", cost_usd=0.01))
    guard = LoopGuard(
        LoopGuardConfig(
            action="warn",
            max_cost_usd=0.005,
            enable_exact=False,
            enable_pingpong=False,
        ),
        judge=judge,
    )
    # First trip consults the judge (adds $0.01 judge cost > $0.005 ceiling).
    _trip(guard)
    # Next observe sees the ceiling exceeded by judge cost and trips budget.
    d = guard.observe(tool_event("more/package.json"), task="t")
    assert d.tripped is True and d.detector == "budget"
    assert "cost" in d.reason.lower()
