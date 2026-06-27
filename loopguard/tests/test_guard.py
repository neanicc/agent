import json

from loopguard import LoopGuard, LoopGuardConfig

from conftest import tool_event


def test_export_jsonl_creates_valid_jsonl(tmp_path):
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    guard.observe(tool_event())
    path = tmp_path / "run.jsonl"
    guard.export_jsonl(path)
    assert json.loads(path.read_text().splitlines()[0])["tool_name"] == "read_file"


def test_summary_counts_tokens_and_cost():
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    event = tool_event()
    event.tokens = 12
    event.cost_usd = 0.01
    guard.observe(event)
    assert guard.summary() == {
        "events": 1,
        "tokens": 12,
        "cost_usd": 0.01,
        "judge_cost_usd": 0.0,
    }
