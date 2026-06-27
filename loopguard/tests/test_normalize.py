from conftest import tool_event
from loopguard.normalize import exact_signature, normalize_event


def test_ignored_keys_do_not_affect_hashes():
    assert exact_signature(tool_event(timestamp="1")) == exact_signature(tool_event(timestamp="2"))


def test_secrets_are_redacted():
    text = normalize_event(tool_event(api_key="secret-value"))
    assert "secret-value" not in text
    assert "<redacted>" in text
