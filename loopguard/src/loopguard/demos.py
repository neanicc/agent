from __future__ import annotations

import tempfile
from pathlib import Path

from .config import LoopGuardConfig
from .event import LoopEvent
from .guard import LoopGuard
from .tools import read_file

# Real but empty workspace: these genuinely do not exist there.
MISSING_PATHS = ["package.json", "./package.json", "app/package.json"]


def run_broken_agent(use_guard: bool = True, action: str = "pause", root: str | None = None) -> None:
    workspace = Path(root) if root else Path(tempfile.mkdtemp(prefix="loopguard-demo-"))
    # A real file that genuinely exists, used to demonstrate real recovery.
    recovery = workspace / "notes.txt"
    recovery.write_text("This is the file you were actually looking for.")

    guard = LoopGuard(LoopGuardConfig(action=action, max_tool_calls=30)) if use_guard else None
    correction = None
    try:
        for i in range(10):
            path = "notes.txt" if correction else MISSING_PATHS[i % len(MISSING_PATHS)]
            result = read_file(path, root=workspace)  # REAL disk read
            is_err = result.startswith("Error:")
            shown = "not found" if is_err else result[:40]
            print(f'[{i + 1}] read_file("{path}") -> {shown} cost: $0.000')  # no LLM, zero cost
            if not is_err:
                return
            if guard is None:
                continue
            decision = guard.observe(
                LoopEvent(
                    run_id="demo",
                    agent="repo-agent",
                    kind="tool_call",
                    tool_name="read_file",
                    tool_args={"path": path},
                    error=result,
                    tokens=0,
                    cost_usd=0.0,
                )
            )
            if decision.developer_action == "inject":
                correction = decision.suggested_message or "Read notes.txt instead."
            elif decision.tripped and not decision.allowed:
                return
        print("[10] still trying package.json cost: $0.000")
    finally:
        if guard is not None:
            guard.export_jsonl("runs/last.jsonl")


def run_pingpong_demo(action: str = "pause") -> None:
    guard = LoopGuard(LoopGuardConfig(action=action, enable_semantic=False, enable_budget=False))
    print(
        "Ping-pong demo: two agents pass the same failure back and forth "
        "(A-B-A-B). Watch LoopGuard detect the oscillation."
    )
    messages = [
        ("agent-a", "tool failed: package.json not found"),
        ("agent-b", "retry with same approach"),
    ]
    try:
        for i in range(4):
            agent, msg = messages[i % 2]
            print(f"{agent}: {msg}")
            decision = guard.observe(
                LoopEvent(run_id="pingpong", agent=agent, kind="agent_message", output_text=msg)
            )
            if decision.tripped:
                if decision.allowed:
                    continue
                print("LoopGuard terminated the ping-pong loop.")
                break
        s = guard.summary()
        print(f"Total: {s['events']} events · {s['tokens']} tokens · ${s['cost_usd']:.4f}")
    finally:
        guard.export_jsonl("runs/last.jsonl")
