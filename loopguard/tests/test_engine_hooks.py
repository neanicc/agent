from loopguard import LoopGuard, LoopGuardConfig
from loopguard.judge import JudgeVerdict

from conftest import tool_event


def test_on_observe_fires_for_every_event():
    seen = []
    guard = LoopGuard(
        LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False),
        on_observe=lambda e, d: seen.append((e.tool_name, d.tripped)),
    )
    guard.observe(tool_event("a"))
    guard.observe(tool_event("b"))
    assert len(seen) == 2
    assert seen[0] == ("read_file", False)


class _StubJudge:
    def judge(self, events, task=None, detector=None):
        return JudgeVerdict(is_loop=True, reasoning="stuck", suggested_correction="fix it")


def test_on_pause_overrides_terminal_and_is_honored():
    captured = {}

    def fake_pause(decision):
        captured["reason"] = decision.judge_reasoning
        decision.developer_action = "terminate"
        decision.allowed = False
        return decision

    guard = LoopGuard(
        LoopGuardConfig(action="pause", enable_exact=False, enable_budget=False),
        judge=_StubJudge(),
        on_pause=fake_pause,
    )
    d = None
    for path in ["./package.json", "package.json", "/app/package.json"]:
        d = guard.observe(tool_event(path), task="t")
    assert captured["reason"] == "stuck"  # on_pause saw the judged decision
    assert d.allowed is False and d.developer_action == "terminate"
