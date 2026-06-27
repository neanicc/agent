from __future__ import annotations

from loopguard import LoopEvent, LoopGuard, LoopGuardConfig


def read_file(path: str) -> str:
    if path == "pyproject.toml":
        return "Success: project metadata found."
    raise FileNotFoundError("package.json not found")


def run(use_guard: bool = True, action: str = "pause") -> None:
    guard = LoopGuard(LoopGuardConfig(action=action, max_tool_calls=30)) if use_guard else None
    paths = ["./package.json", "package.json", "/app/package.json"]
    correction = None
    for i in range(10):
        path = "pyproject.toml" if correction else paths[i % 3]
        cost = 0.004 + i * 0.005
        try:
            result = read_file(path)
            print(f'[{i + 1}] read_file("{path}") -> {result} cost: ${cost:.3f}')
            return
        except FileNotFoundError as exc:
            print(f'[{i + 1}] read_file("{path}") -> not found cost: ${cost:.3f}')
            if guard:
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
    print(f"[10] still trying package.json cost: ${0.047:.3f}")


if __name__ == "__main__":
    run()
