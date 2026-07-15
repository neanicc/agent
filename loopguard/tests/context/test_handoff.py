from __future__ import annotations

from pathlib import Path

import pytest

from loopguard.context.handoff import Handoff, HandoffStore


def _handoff() -> Handoff:
    return Handoff(
        goal="Finish the authentication repair",
        accepted_decisions=["Keep tokens server-side"],
        changed_paths=["src/auth.py", "tests/test_auth.py"],
        verification_ids=["verify-1"],
        unresolved=["Confirm the legacy migration window"],
        risks=["Older tokens may need rotation"],
        repo_seq=42,
        commit_sha="a" * 40,
    )


@pytest.mark.parametrize("forbidden", ["reasoning", "chain_of_thought", "scratchpad"])
def test_handoff_rejects_hidden_reasoning_fields(forbidden: str) -> None:
    payload = _handoff().model_dump()
    payload[forbidden] = "private internal trace"

    with pytest.raises(ValueError, match="hidden reasoning"):
        Handoff.model_validate(payload)


def test_handoff_normalizes_paths_and_rejects_duplicates() -> None:
    payload = _handoff().model_dump()
    payload["changed_paths"] = ["tests/test_auth.py", "src/auth.py"]
    assert Handoff.model_validate(payload).changed_paths == [
        "src/auth.py",
        "tests/test_auth.py",
    ]

    payload = _handoff().model_dump()
    payload["verification_ids"] = ["verify-1", "verify-1"]
    with pytest.raises(ValueError, match="unique"):
        Handoff.model_validate(payload)


def test_handoff_store_is_repo_bound_and_restart_safe(tmp_path: Path) -> None:
    database = tmp_path / "handoffs.db"
    with HandoffStore(database) as store:
        artifact = store.create("repo-a", "session-a", _handoff())
        assert store.read("repo-a", artifact.handoff_id).handoff == _handoff()
        with pytest.raises(KeyError, match="not found"):
            store.read("repo-b", artifact.handoff_id)

    with HandoffStore(database) as reopened:
        restored = reopened.read("repo-a", artifact.handoff_id)
        assert restored.owner_session_id == "session-a"
        assert restored.handoff.repo_seq == 42
