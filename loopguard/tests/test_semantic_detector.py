from loopguard import LoopEvent, LoopGuard, LoopGuardConfig

from conftest import tool_event


def test_semantic_near_duplicate_calls_trip_after_3():
    guard = LoopGuard(
        LoopGuardConfig(
            action="warn",
            enable_exact=False,
            enable_budget=False,
            semantic_threshold=0.80,
        )
    )
    for path in ["./package.json", "package.json"]:
        assert not guard.observe(tool_event(path)).tripped
    decision = guard.observe(tool_event("/app/package.json"))
    assert decision.tripped
    assert decision.detector == "semantic"


def test_different_calls_do_not_trip():
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_budget=False, semantic_threshold=0.90))
    events = [
        LoopEvent(
            run_id="r",
            agent="a",
            kind="tool_call",
            tool_name="read_file",
            tool_args={"path": "a.txt"},
            error="a missing",
        ),
        LoopEvent(
            run_id="r",
            agent="a",
            kind="tool_call",
            tool_name="search_web",
            tool_args={"query": "weather"},
            output_text="sunny",
        ),
        LoopEvent(
            run_id="r",
            agent="a",
            kind="tool_call",
            tool_name="run_tests",
            tool_args={"target": "unit"},
            output_text="passed",
        ),
    ]
    for event in events:
        assert not guard.observe(event).tripped
