from loopguard import LoopEvent, LoopGuard

guard = LoopGuard()
guard.observe(
    LoopEvent(
        run_id="run", agent="agent", kind="tool_call", tool_name="search", tool_args={"q": "x"}
    )
)
