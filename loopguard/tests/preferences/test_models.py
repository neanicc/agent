from __future__ import annotations

import pytest
from pydantic import ValidationError

from loopguard.preferences.models import (
    PreferenceEvidence,
    PreferenceProfile,
    PreferenceRule,
    PreferenceVerdict,
)


def rule(**updates) -> PreferenceRule:
    values = {
        "id": "dynamic-type",
        "source": "explicit",
        "severity": "block",
        "statement": "All iOS text supports Dynamic Type",
        "scopes": ["ios"],
        "evaluator": "dynamic-type",
    }
    values.update(updates)
    return PreferenceRule.model_validate(values)


def test_learned_rule_cannot_be_blocking() -> None:
    with pytest.raises(ValueError, match="learned preferences must be soft"):
        rule(
            id="dense-layout",
            source="learned",
            severity="block",
            statement="Prefer compact task cards",
        )


def test_explicit_rule_can_be_blocking() -> None:
    assert rule().severity == "block"


def test_profile_rejects_duplicate_rule_ids_and_supports_lookup() -> None:
    with pytest.raises(ValueError, match="rule IDs must be unique"):
        PreferenceProfile(
            profile_id="profile-1",
            rules=[rule(), rule(statement="Different")],
        )

    profile = PreferenceProfile(profile_id="profile-1", rules=[rule()])
    assert profile.rule("dynamic-type").statement == "All iOS text supports Dynamic Type"
    assert profile.rule("missing") is None


def test_evidence_and_verdict_require_auditable_citations() -> None:
    evidence = PreferenceEvidence(
        evidence_id="evidence-1",
        rule_id="dynamic-type",
        artifact_id="sha256:" + "a" * 64,
        evaluator="dynamic-type",
        evaluator_version="1.0.0",
        summary="Text-size metadata is absent.",
        confidence=1,
    )
    verdict = PreferenceVerdict(
        verdict_id="verdict-1",
        rule_id="dynamic-type",
        artifact_id=evidence.artifact_id,
        evaluator=evidence.evaluator,
        evaluator_version=evidence.evaluator_version,
        status="violation",
        severity="block",
        evidence_ids=[evidence.evidence_id],
        summary="Dynamic Type evidence is missing.",
        confidence=1,
    )
    assert verdict.evidence_ids == ["evidence-1"]

    with pytest.raises(ValidationError):
        PreferenceVerdict(
            verdict_id="verdict-2",
            rule_id="dynamic-type",
            artifact_id="",
            evaluator="dynamic-type",
            evaluator_version="1.0.0",
            status="violation",
            severity="block",
            summary="Missing citation",
        )


def test_contracts_are_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        rule(unknown="not accepted")
    with pytest.raises(ValidationError):
        rule(id="../escape")
    with pytest.raises(ValidationError):
        rule(parameters={"payload": "x" * 65_537})
