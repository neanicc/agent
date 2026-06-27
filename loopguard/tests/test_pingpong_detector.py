from loopguard import LoopEvent, LoopGuard, LoopGuardConfig


def test_pingpong_trips_on_a_b_a_b():
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    values = [("a", "failed"), ("b", "retry"), ("a", "failed"), ("b", "retry")]
    decision = None
    for agent, message in values:
        decision = guard.observe(
            LoopEvent(run_id="r", agent=agent, kind="agent_message", output_text=message)
        )
    assert decision is not None
    assert decision.tripped
    assert decision.detector == "pingpong"
