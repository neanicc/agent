from loopguard import LoopGuard, LoopGuardConfig

from conftest import tool_event


def test_budget_max_tool_calls_trips():
    guard = LoopGuard(
        LoopGuardConfig(
            action="warn",
            max_tool_calls=1,
            enable_exact=False,
            enable_semantic=False,
            enable_pingpong=False,
        )
    )
    assert not guard.observe(tool_event("a")).tripped
    decision = guard.observe(tool_event("b"))
    assert decision.tripped
    assert decision.detector == "budget"
