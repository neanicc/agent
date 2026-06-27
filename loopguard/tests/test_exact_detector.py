from loopguard import LoopGuard, LoopGuardConfig

from conftest import tool_event


def test_exact_repeated_calls_trip_after_3():
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    assert not guard.observe(tool_event()).tripped
    assert not guard.observe(tool_event()).tripped
    decision = guard.observe(tool_event())
    assert decision.tripped
    assert decision.detector == "exact"
    assert decision.similarity == 1.0
