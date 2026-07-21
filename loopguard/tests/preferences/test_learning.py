from __future__ import annotations

import os
import sqlite3

import pytest

from loopguard.preferences.learning import PreferenceLearner, PreferenceLearningError
from loopguard.preferences.store import PreferenceActor, PreferenceScope


def test_repeated_same_choice_creates_soft_rule(tmp_path) -> None:
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db", minimum_examples=3)
    for decision_id in ("d1", "d2", "d3"):
        learner.record(decision_id, choice="compact", context={"surface": "task-card"})
    rules = learner.derive_rules()
    assert len(rules) == 1
    assert rules[0].source == "learned"
    assert rules[0].severity == "inform"
    assert rules[0].parameters["example_count"] == 3
    assert rules[0].parameters["confidence"] == 1.0


def test_conflicting_choices_do_not_create_rule(tmp_path) -> None:
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db", minimum_examples=3)
    for index, choice in enumerate(("compact", "spacious", "compact")):
        learner.record(f"d{index}", choice=choice, context={"surface": "task-card"})
    assert learner.derive_rules() == []


def test_decisions_are_scoped_and_store_artifact_hashes(tmp_path) -> None:
    path = tmp_path / "preferences.db"
    first = PreferenceLearner.for_path(
        path,
        minimum_examples=2,
        scope=PreferenceScope(tenant_id="tenant-a", user_id="user", repository_id="repo"),
    )
    second = PreferenceLearner.for_path(
        path,
        minimum_examples=2,
        scope=PreferenceScope(tenant_id="tenant-b", user_id="user", repository_id="repo"),
    )
    first.record(
        "d1",
        choice="compact",
        context={"surface": "task-card"},
        artifact_hash="a" * 64,
    )
    second.record("d1", choice="spacious", context={"surface": "task-card"})
    first.record("d2", choice="compact", context={"surface": "task-card"})
    assert first.derive_rules()[0].parameters["choice"] == "compact"
    assert second.derive_rules() == []
    assert first.decision("d1").artifact_hash == "a" * 64


def test_decision_replay_is_idempotent_but_conflicts_fail_closed(tmp_path) -> None:
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db")
    learner.record("d1", choice="compact", context={"surface": "task-card"})
    learner.record("d1", choice="compact", context={"surface": "task-card"})
    with pytest.raises(PreferenceLearningError, match="different payload"):
        learner.record("d1", choice="spacious", context={"surface": "task-card"})


def test_revocation_and_deletion_stop_contributing_immediately(tmp_path) -> None:
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db", minimum_examples=3)
    for decision_id in ("d1", "d2", "d3"):
        learner.record(decision_id, choice="compact", context={"surface": "task-card"})
    rule_id = learner.derive_rules()[0].id

    learner.revoke("d1", reason="approval was recorded in error")
    assert learner.derive_rules() == []
    learner.record("d4", choice="compact", context={"surface": "task-card"})
    assert learner.derive_rules()[0].id == rule_id

    learner.delete_rule(rule_id, reason="do not learn this choice")
    assert learner.derive_rules() == []
    assert learner.negative_example_count(rule_id) == 1
    with pytest.raises(PreferenceLearningError, match="revoked decision"):
        learner.record("d2", choice="compact", context={"surface": "task-card"})
    for decision_id in ("d5", "d6"):
        learner.record(decision_id, choice="compact", context={"surface": "task-card"})
    assert learner.derive_rules() == []
    learner.record("d7", choice="compact", context={"surface": "task-card"})
    assert learner.derive_rules() == []
    learner.record("d8", choice="compact", context={"surface": "task-card"})
    assert learner.derive_rules()[0].id == rule_id
    assert learner.derive_rules()[0].parameters["confidence"] == 0.8


def test_explicit_promotion_can_create_blocking_rule(tmp_path) -> None:
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db", minimum_examples=2)
    for decision_id in ("d1", "d2"):
        learner.record(decision_id, choice="compact", context={"surface": "task-card"})
    learned = learner.derive_rules()[0]
    learner.promote(learned.id, "block", reason="operator-approved product requirement")
    promoted = learner.derive_rules()[0]
    assert promoted.source == "explicit"
    assert promoted.severity == "block"


def test_context_and_actor_contracts_are_bounded(tmp_path) -> None:
    learner = PreferenceLearner.for_path(tmp_path / "preferences.db")
    with pytest.raises(PreferenceLearningError, match="flat scalar"):
        learner.record("d1", choice="compact", context={"surface": {"nested": True}})
    with pytest.raises(ValueError):
        PreferenceActor(actor_id="unknown", authentication="none")


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner-only database contract")
def test_preference_store_is_owner_only_and_rejects_symlinks(tmp_path) -> None:
    path = tmp_path / "preferences.db"
    PreferenceLearner.for_path(path)
    assert path.stat().st_mode & 0o777 == 0o600

    target = tmp_path / "target.db"
    target.touch()
    link = tmp_path / "linked.db"
    link.symlink_to(target)
    with pytest.raises(PreferenceLearningError, match="symlink"):
        PreferenceLearner.for_path(link)


def test_preference_store_rejects_lookalike_schema(tmp_path) -> None:
    path = tmp_path / "preferences.db"
    connection = sqlite3.connect(path)
    for table in ("decisions", "revocations", "promotions", "negative_examples"):
        connection.execute(f"CREATE TABLE {table}(wrong TEXT)")
    connection.execute("PRAGMA user_version=1")
    connection.close()
    if os.name == "posix":
        path.chmod(0o600)

    with pytest.raises(PreferenceLearningError, match="schema"):
        PreferenceLearner.for_path(path)
