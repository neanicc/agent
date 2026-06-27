from conftest import tool_event
from loopguard.normalize import exact_signature, normalize_event
from loopguard.event import LoopEvent


def test_ignored_keys_do_not_affect_hashes():
    assert exact_signature(tool_event(timestamp="1")) == exact_signature(tool_event(timestamp="2"))


def test_secrets_are_redacted():
    text = normalize_event(tool_event(api_key="secret-value"))
    assert "secret-value" not in text
    assert "<redacted>" in text


def test_api_keys_in_free_text_are_redacted():
    # Secrets embedded in error/output prose must not reach the judge or the run log.
    event = LoopEvent(
        run_id="r",
        agent="a",
        kind="tool_call",
        tool_name="call_api",
        error="auth failed using key sk-abc123def456ghi789 and Bearer tok_supersecret9",
    )
    text = normalize_event(event)
    assert "sk-abc123def456ghi789" not in text
    assert "tok_supersecret9" not in text
    assert "<redacted>" in text
