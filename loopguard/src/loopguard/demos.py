from __future__ import annotations

from .config import LoopGuardConfig
from .event import LoopEvent
from .guard import LoopGuard


PACKAGE_PATHS = ["./package.json", "package.json", "/app/package.json"]


def demo_read_file(path: str) -> str:
    if path == "pyproject.toml":
        return "Success: project metadata found."
    raise FileNotFoundError("package.json not found")


def run_broken_agent(use_guard: bool = True, action: str = "pause") -> None:
    guard = LoopGuard(LoopGuardConfig(action=action, max_tool_calls=30)) if use_guard else None
    correction = None
    for i in range(10):
        path = "pyproject.toml" if correction else PACKAGE_PATHS[i % len(PACKAGE_PATHS)]
        cost = min(0.004 + i * 0.005, 0.047)
        try:
            result = demo_read_file(path)
            print(f'[{i + 1}] read_file("{path}") -> {result} cost: ${cost:.3f}')
            return
        except FileNotFoundError as exc:
            print(f'[{i + 1}] read_file("{path}") -> not found cost: ${cost:.3f}')
            if guard is None:
                continue
            decision = guard.observe(
                LoopEvent(
                    run_id="demo",
                    agent="repo-agent",
                    kind="tool_call",
                    tool_name="read_file",
                    tool_args={"path": path},
                    error=str(exc),
                    tokens=120,
                    cost_usd=cost,
                )
            )
            if decision.developer_action == "inject" and decision.suggested_message:
                correction = decision.suggested_message
            elif decision.tripped and not decision.allowed:
                return
    print("[10] still trying package.json cost: $0.047")


def run_pingpong_demo(action: str = "pause") -> None:
    guard = LoopGuard(LoopGuardConfig(action=action, enable_semantic=False, enable_budget=False))
    messages = [
        ("agent-a", "tool failed: package.json not found"),
        ("agent-b", "retry with same approach"),
    ]
    for i in range(4):
        agent, msg = messages[i % 2]
        print(f"{agent}: {msg}")
        decision = guard.observe(
            LoopEvent(run_id="pingpong", agent=agent, kind="agent_message", output_text=msg)
        )
        if decision.tripped:
            break
