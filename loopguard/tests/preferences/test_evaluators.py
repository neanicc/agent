from __future__ import annotations

import pytest

from loopguard.preferences.evaluators import (
    PreferenceArtifact,
    PreferenceEvaluationError,
    evaluate_artifacts,
)
from loopguard.preferences.models import PreferenceProfile, PreferenceRule


def test_unknown_color_literal_warns_when_tokens_are_required(profile, ui_diff) -> None:
    verdicts = evaluate_artifacts(profile, ui_diff.with_added("#12ABEF"))
    verdict = next(item for item in verdicts if item.rule_id == "use-design-tokens")
    assert verdict.status == "violation"
    assert verdict.severity == "warn"
    assert verdict.artifact_id == ui_diff.artifact_id


def test_declared_color_token_literal_passes(profile, ui_diff) -> None:
    verdicts = evaluate_artifacts(profile, ui_diff.with_added("#3366FF"))
    verdict = next(item for item in verdicts if item.rule_id == "use-design-tokens")
    assert verdict.status == "pass"


def test_missing_accessible_name_blocks(profile, axe_artifact) -> None:
    verdicts = evaluate_artifacts(profile, axe_artifact.with_violation("button-name"))
    verdict = next(item for item in verdicts if item.rule_id == "accessible-name")
    assert verdict.status == "violation"
    assert verdict.severity == "block"


def test_dynamic_type_and_reduced_motion_metadata_are_deterministic(profile) -> None:
    artifacts = [
        PreferenceArtifact(
            artifact_id="dynamic",
            kind="text_metadata",
            payload={"supports_dynamic_type": True, "clips_required_text": False},
        ),
        PreferenceArtifact(
            artifact_id="motion",
            kind="motion_metadata",
            payload={"respects_reduced_motion": False},
        ),
    ]
    verdicts = evaluate_artifacts(profile, artifacts)
    assert next(item for item in verdicts if item.rule_id == "dynamic-type").status == "pass"
    motion = next(item for item in verdicts if item.rule_id == "reduced-motion")
    assert motion.status == "violation"
    assert motion.severity == "warn"


def test_required_import_and_screenshot_reference_evaluators() -> None:
    profile = PreferenceProfile(
        profile_id="custom",
        rules=[
            PreferenceRule(
                id="use-button-component",
                source="explicit",
                severity="warn",
                statement="Use the shared Button component",
                evaluator="required-component",
                parameters={"required_imports": ["@acme/ui/button"]},
            ),
            PreferenceRule(
                id="visual-reference",
                source="explicit",
                severity="inform",
                statement="Capture the expected viewport",
                evaluator="screenshot-reference",
                parameters={"min_width": 320, "min_height": 568, "reference_required": True},
            ),
        ],
    )
    artifacts = [
        PreferenceArtifact(
            artifact_id="diff",
            kind="source_diff",
            payload={"added_text": "<button>Save</button>", "imports": []},
        ),
        PreferenceArtifact(
            artifact_id="shot",
            kind="screenshot_metadata",
            payload={"width": 1280, "height": 720, "current_present": True},
        ),
    ]
    verdicts = evaluate_artifacts(profile, artifacts)
    assert {item.rule_id: item.status for item in verdicts} == {
        "use-button-component": "violation",
        "visual-reference": "violation",
    }


def test_incomplete_accessibility_artifact_is_inconclusive(profile, axe_artifact) -> None:
    artifact = axe_artifact.model_copy(update={"payload": {"incomplete": ["color-contrast"]}})
    verdicts = evaluate_artifacts(profile, artifact)
    contrast = next(item for item in verdicts if item.rule_id == "wcag-contrast")
    assert contrast.status == "inconclusive"


def test_ios_accessible_name_snapshot_blocks_missing_labels(profile) -> None:
    artifact = PreferenceArtifact(
        artifact_id="ios-accessibility",
        kind="ios_accessibility",
        payload={
            "snapshot_complete": True,
            "elements": [{"role": "button", "identifier": "save", "label": ""}],
        },
    )
    verdict = next(
        item for item in evaluate_artifacts(profile, artifact) if item.rule_id == "accessible-name"
    )
    assert verdict.status == "violation"
    assert verdict.severity == "block"


def test_evaluation_is_replay_deterministic(profile, ui_diff) -> None:
    artifact = ui_diff.with_added("#12ABEF")
    assert evaluate_artifacts(profile, artifact) == evaluate_artifacts(profile, artifact)


def test_unknown_evaluator_fails_closed(ui_diff) -> None:
    profile = PreferenceProfile(
        profile_id="misconfigured",
        rules=[
            PreferenceRule(
                id="hard-rule",
                source="explicit",
                severity="block",
                statement="Must be evaluated",
                evaluator="missing-evaluator",
            )
        ],
    )
    with pytest.raises(PreferenceEvaluationError, match="no evaluator"):
        evaluate_artifacts(profile, ui_diff)
