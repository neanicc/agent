from loopguard import LoopEvent, LoopGuard, LoopGuardConfig


def run(action: str = "pause"):
    guard = LoopGuard(LoopGuardConfig(action=action, enable_semantic=False))
    msgs = [
        ("agent-a", "tool failed: package.json not found"),
        ("agent-b", "retry with same approach"),
    ]
    for i in range(4):
        agent, msg = msgs[i % 2]
        print(f"{agent}: {msg}")
        decision = guard.observe(
            LoopEvent(run_id="pingpong", agent=agent, kind="agent_message", output_text=msg)
        )
        if decision.tripped:
            break


if __name__ == "__main__":
    run()
