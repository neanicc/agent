from __future__ import annotations

import json

import pytest

from loopguard.context.digest import DigestBudget, DigestBuilder
from loopguard.context.models import ChangeRecord


def _record(
    seq: int,
    path: str,
    *,
    actor: str = "codex:session",
    symbols: list[str] | None = None,
    verification_ids: list[str] | None = None,
    patch: str | None = None,
) -> ChangeRecord:
    return ChangeRecord(
        record_id=f"record-{seq}-{path}",
        content_fingerprint=f"{seq:064x}",
        repo_id="repo",
        repo_seq=seq,
        worktree_id="worktree",
        path=path,
        actor=actor,
        before_hash="old",
        after_hash="new",
        patch=patch,
        symbols=symbols or [],
        verification_ids=verification_ids or [],
    )


def test_digest_is_deterministic_bounded_and_excludes_patch_reasoning() -> None:
    changes = [
        _record(
            seq,
            f"src/module_{seq}.py",
            symbols=[f"symbol_{seq}"],
            patch="hidden reasoning and scratchpad must never be replayed",
        )
        for seq in range(1, 9)
    ]
    builder = DigestBuilder()

    first = builder.build(changes, since=4, budget=DigestBudget(max_chars=420))
    second = builder.build(reversed(changes), since=4, budget=DigestBudget(max_chars=420))

    assert first.text == second.text
    assert len(first.text) <= 420
    assert first.next_repo_seq == 8
    assert first.included_count + first.omitted_count == 4
    assert "hidden reasoning" not in first.text.lower()
    assert "scratchpad" not in first.text.lower()


def test_digest_prioritizes_collision_failure_dependency_then_recency() -> None:
    changes = [
        _record(9, "src/recent.py"),
        _record(6, "src/dependency.py"),
        _record(7, "src/failing.py", verification_ids=["verify-failed"]),
        _record(5, "src/collision.py"),
    ]

    digest = DigestBuilder().build(
        changes,
        since=0,
        budget=DigestBudget(max_chars=2_000),
        collision_paths={"src/collision.py"},
        verification_statuses={"verify-failed": "regression"},
        direct_dependency_paths={"src/dependency.py"},
    )

    positions = [
        digest.text.index(f'"path":"{path}"')
        for path in [
            "src/collision.py",
            "src/failing.py",
            "src/dependency.py",
            "src/recent.py",
        ]
    ]
    assert positions == sorted(positions)
    assert '"status":"regression"' in digest.text


def test_digest_omits_whole_entries_and_advances_cursor() -> None:
    changes = [_record(1, "src/" + "a" * 300 + ".py"), _record(2, "src/ok.py")]

    digest = DigestBuilder().build(
        changes,
        since=0,
        budget=DigestBudget(max_chars=220),
    )

    assert len(digest.text) <= 220
    assert digest.next_repo_seq == 2
    assert digest.omitted_count >= 1
    for line in digest.text.splitlines():
        if line.startswith("- "):
            json.loads(line[2:])


def test_digest_rejects_cross_repository_input() -> None:
    foreign = _record(2, "src/foreign.py").model_copy(update={"repo_id": "other"})
    with pytest.raises(ValueError, match="one repository"):
        DigestBuilder().build(
            [_record(1, "src/local.py"), foreign],
            since=0,
            budget=DigestBudget(max_chars=500),
        )


def test_digest_cursor_never_moves_backwards_when_no_new_records_exist() -> None:
    digest = DigestBuilder().build(
        [_record(3, "src/old.py")],
        since=10,
        budget=DigestBudget(max_chars=500),
    )

    assert digest.next_repo_seq == 10
    assert digest.included_count == digest.omitted_count == 0


def test_digest_enforces_token_estimate_independently_of_character_limit() -> None:
    changes = [_record(seq, f"src/module_{seq}.py") for seq in range(1, 10)]

    digest = DigestBuilder().build(
        changes,
        since=0,
        budget=DigestBudget(max_chars=2_000, max_tokens_estimate=50),
    )

    assert len(digest.text) < 2_000
    assert digest.tokens_estimate <= 50
    assert digest.truncated is True
