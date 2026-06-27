import pytest

from loopguard.integrations.cerebras_agent import tool_call_to_event
from loopguard.integrations.cerebras_client import MISSING_KEY_MESSAGE, CerebrasLLMClient


def test_cerebras_import_optional_and_missing_key(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match=MISSING_KEY_MESSAGE):
        CerebrasLLMClient()


def test_mocked_cerebras_tool_call_to_event():
    call = {"function": {"name": "read_file", "arguments": '{"path":"x"}'}}
    event = tool_call_to_event("r", "c", call)
    assert event.tool_name == "read_file"
    assert event.kind == "tool_call"
