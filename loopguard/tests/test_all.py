import json

import pytest

from loopguard import LoopEvent, LoopGuard, LoopGuardConfig
from loopguard.integrations.cerebras_client import MISSING_KEY_MESSAGE, CerebrasLLMClient
from loopguard.integrations.cerebras_agent import tool_call_to_event
from loopguard.normalize import exact_signature, normalize_event


def ev(path="./package.json", **kw):
    return LoopEvent(
        run_id="r",
        agent="a",
        kind="tool_call",
        tool_name="read_file",
        tool_args={"path": path, **kw},
        error="package.json not found",
    )


def test_exact_repeated_calls_trip_after_3():
    g = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    assert not g.observe(ev()).tripped
    assert not g.observe(ev()).tripped
    assert g.observe(ev()).tripped


def test_semantic_near_duplicate_calls_trip_after_3():
    g = LoopGuard(
        LoopGuardConfig(
            action="warn", enable_exact=False, enable_budget=False, semantic_threshold=0.80
        )
    )
    for p in ["./package.json", "package.json"]:
        assert not g.observe(ev(p)).tripped
    assert g.observe(ev("/app/package.json")).tripped


def test_different_calls_do_not_trip():
    g = LoopGuard(LoopGuardConfig(action="warn", enable_budget=False))
    for p in ["a.txt", "b.txt", "c.txt"]:
        assert not g.observe(
            LoopEvent(
                run_id="r",
                agent="a",
                kind="tool_call",
                tool_name="read_file",
                tool_args={"path": p},
                error=f"{p} missing",
            )
        ).tripped


def test_pingpong_trips():
    g = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    vals = [("a", "failed"), ("b", "retry"), ("a", "failed"), ("b", "retry")]
    dec = None
    for agent, msg in vals:
        dec = g.observe(LoopEvent(run_id="r", agent=agent, kind="agent_message", output_text=msg))
    assert dec and dec.tripped and dec.detector == "pingpong"


def test_ignored_keys_do_not_affect_hashes():
    assert exact_signature(ev(timestamp="1")) == exact_signature(ev(timestamp="2"))


def test_secrets_are_redacted():
    text = normalize_event(ev(api_key="secret-value"))
    assert "secret-value" not in text and "<redacted>" in text


def test_budget_max_tool_calls_trips():
    g = LoopGuard(
        LoopGuardConfig(
            action="warn",
            max_tool_calls=1,
            enable_exact=False,
            enable_semantic=False,
            enable_pingpong=False,
        )
    )
    assert not g.observe(ev("a")).tripped
    assert g.observe(ev("b")).tripped


def test_export_jsonl_creates_valid_jsonl(tmp_path):
    g = LoopGuard(LoopGuardConfig(action="warn"))
    g.observe(ev())
    path = tmp_path / "run.jsonl"
    g.export_jsonl(path)
    assert json.loads(path.read_text().splitlines()[0])["tool_name"] == "read_file"


def test_cerebras_import_optional_and_missing_key(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match=MISSING_KEY_MESSAGE):
        CerebrasLLMClient()


def test_mocked_cerebras_tool_call_to_event():
    call = {"function": {"name": "read_file", "arguments": '{"path":"x"}'}}
    event = tool_call_to_event("r", "c", call)
    assert event.tool_name == "read_file" and event.kind == "tool_call"
