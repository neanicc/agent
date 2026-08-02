from __future__ import annotations

from copy import deepcopy

import pytest

from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.redaction import REDACTED, redact
from loopguard.control.store import EventStore


def _event(payload: dict[str, object]) -> ControlEvent:
    return ControlEvent(
        event_id="evt_secret",
        kind=EventKind.TOOL_CALL,
        source="test",
        session=SessionRef(host_id="host", repo_id="repo", session_id="session"),
        payload=payload,
    )


def test_redact_masks_keys_and_inline_tokens():
    value = {
        "Authorization": "Bearer abcdefghijklmnop",
        "cmd": "curl -H 'x-api-key: sk-test-secret-value' https://example.test",
        "nested": {"password": "hunter2"},
    }

    result = redact(value)

    assert "abcdefghijklmnop" not in str(result)
    assert "sk-test-secret-value" not in str(result)
    assert "hunter2" not in str(result)
    assert result["nested"]["password"] == REDACTED


@pytest.mark.parametrize(
    "key",
    [
        "authorization",
        "Proxy-Authorization",
        "password",
        "passwd",
        "client_secret",
        "github-token",
        "refreshToken",
        "api.key",
        "private_key",
        "AWS_SECRET_ACCESS_KEY",
        "cookie",
        "set-cookie",
        "credentials",
    ],
)
def test_sensitive_key_names_are_normalized_and_mask_the_entire_value(key: str):
    assert redact({key: {"nested": "must-not-survive"}}) == {key: REDACTED}


@pytest.mark.parametrize(
    ("secret", "prefix"),
    [
        ("Bearer abcdefghijklmnop", "Bearer "),
        ("Basic YWxhZGRpbjpvcGVuc2VzYW1l", "Basic "),
        ("x-api-key: sk-test-secret-value", "x-api-key: "),
        ("OPENAI_API_KEY=sk-test-secret-value", "OPENAI_API_KEY="),
        ("token: github_pat_abcdefghijklmnopqrstuvwxyz", "token: "),
        ("ghp_abcdefghijklmnopqrstuvwxyz123456", ""),
        ("AKIAIOSFODNN7EXAMPLE", ""),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue", ""),
    ],
)
def test_inline_credentials_are_masked_without_erasing_safe_prefixes(secret: str, prefix: str):
    result = redact(f"before {secret} after")

    assert secret not in result
    assert REDACTED in result
    if prefix:
        assert prefix in result


def test_private_key_blocks_and_url_userinfo_are_masked():
    value = {
        "pem": "-----BEGIN PRIVATE KEY-----\nsecretmaterial\n-----END PRIVATE KEY-----",
        "url": "https://alice:swordfish@example.test/path",
    }

    result = redact(value)

    assert "secretmaterial" not in result["pem"]
    assert "alice" not in result["url"]
    assert "swordfish" not in result["url"]
    assert result["url"] == f"https://{REDACTED}@example.test/path"


def test_redaction_returns_an_independent_structure_and_preserves_safe_values():
    value = {
        "safe": "ordinary text",
        "items": [{"password": "secret"}, "token_count=42"],
        "tuple": ("Bearer abcdefghijklmnop", 7),
    }
    before = deepcopy(value)

    result = redact(value)
    result["items"].append("new")

    assert value == before
    assert result["safe"] == "ordinary text"
    assert result["items"][1] == "token_count=42"
    assert result["tuple"] == (f"Bearer {REDACTED}", 7)


def test_store_persists_only_redacted_payload_and_does_not_mutate_caller(tmp_path):
    path = tmp_path / "events.db"
    secret = "sk-super-secret-storage-value"
    event = _event(
        {
            "Authorization": f"Bearer {secret}",
            "arguments": {"cmd": f"curl -H 'x-api-key: {secret}' example.test"},
        }
    )
    original = event.model_copy(deep=True)

    with EventStore.for_test(path) as store:
        position = store.append(event)
        restored = store.read_local_after(0, 1)[0].event

    persisted = b"".join(
        candidate.read_bytes()
        for candidate in (path, path.with_name("events.db-wal"))
        if candidate.exists()
    )
    assert event == original
    assert restored.payload["Authorization"] == REDACTED
    assert secret not in str(restored.payload)
    assert secret.encode() not in persisted
    assert position.local_log_seq == 1


def test_duplicate_idempotency_uses_redacted_semantics(tmp_path):
    path = tmp_path / "events.db"
    first = _event({"password": "first-secret", "safe": "same"})
    retry = _event({"password": "second-secret", "safe": "same"})

    with EventStore.for_test(path) as store:
        first_position = store.append(first)
        retry_position = store.append(retry)
        restored = store.read_local_after(0, 1)[0].event

    assert retry_position == first_position
    assert restored.payload == {"password": REDACTED, "safe": "same"}
