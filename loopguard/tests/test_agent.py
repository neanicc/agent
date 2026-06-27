from loopguard import LoopGuard, LoopGuardConfig
from loopguard.agent import run_agent
from loopguard.providers.base import LLMResult
from loopguard.tools import READ_FILE_SCHEMA, TOOLS


class _ToolCall:
    def __init__(self, cid, name, args_json):
        self.id = cid
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": args_json})()


class _LoopingProvider:
    """Always calls read_file on a missing file -> deterministic loop."""

    model = "fake/model"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        self.calls += 1
        return LLMResult(
            text="",
            tool_calls=[_ToolCall(f"c{self.calls}", "read_file", '{"path": "package.json"}')],
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            cost_usd=0.001,
        )


def test_agent_loops_and_guard_stops_it(tmp_path):
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_budget=False))
    provider = _LoopingProvider()
    result = run_agent(
        provider,
        system="find package.json",
        task="find package.json",
        tools_schema=[READ_FILE_SCHEMA],
        tool_impls={"read_file": lambda path: TOOLS["read_file"](path, root=tmp_path)},
        guard=guard,
        max_steps=10,
    )
    # warn mode never blocks, so it runs to max_steps; events were recorded
    assert provider.calls == 10
    assert guard.summary()["events"] == 10


def test_agent_records_real_tokens_and_cost(tmp_path):
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    provider = _LoopingProvider()
    run_agent(
        provider,
        system="s",
        task="t",
        tools_schema=[READ_FILE_SCHEMA],
        tool_impls={"read_file": lambda path: TOOLS["read_file"](path, root=tmp_path)},
        guard=guard,
        max_steps=3,
    )
    s = guard.summary()
    assert s["tokens"] == 330  # 3 turns * 110
    assert round(s["cost_usd"], 4) == 0.003
