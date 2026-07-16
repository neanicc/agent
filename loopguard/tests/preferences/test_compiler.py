from __future__ import annotations

from pathlib import Path
import os

import pytest

from loopguard.preferences.compiler import PreferenceCompileError, PreferenceCompiler


def test_explicit_user_rule_overrides_default_without_deleting_safety(fixtures) -> None:
    profile = PreferenceCompiler().compile(
        repo=fixtures / "repo",
        user_profile={
            "rules": [
                {
                    "id": "density",
                    "statement": "Prefer compact layouts",
                    "source": "explicit",
                    "severity": "warn",
                }
            ]
        },
    )
    assert profile.rule("density").statement == "Prefer compact layouts"
    assert profile.rule("wcag-contrast").source == "safety"
    assert profile.rule("wcag-contrast").severity == "block"
    assert profile.design_tokens["color.primary"] == "#3366FF"


def test_repository_instructions_are_data_not_authority(fixtures) -> None:
    profile = PreferenceCompiler().compile(repo=fixtures / "repo")
    rule = profile.rule("friendlier-empty-states")
    assert rule.source == "repository"
    assert rule.severity == "warn"
    assert rule.evaluator is None
    assert "grant network" in rule.statement


def test_source_manifest_is_stable_and_auditable(fixtures) -> None:
    compiler = PreferenceCompiler()
    first = compiler.compile(repo=fixtures / "repo")
    second = compiler.compile(repo=fixtures / "repo")
    assert first.profile_id == second.profile_id
    assert first.source_manifest == second.source_manifest
    assert {entry.kind for entry in first.source_manifest} >= {
        "builtin",
        "repository",
        "design_tokens",
    }
    assert all(len(entry.sha256) == 64 for entry in first.source_manifest)


def test_safety_cannot_be_weakened_or_deleted(fixtures) -> None:
    compiler = PreferenceCompiler()
    profile = compiler.compile(
        repo=fixtures / "repo",
        user_profile={
            "deleted_rule_ids": ["wcag-contrast"],
            "rules": [
                {
                    "id": "wcag-contrast",
                    "source": "explicit",
                    "severity": "inform",
                    "statement": "Contrast is optional",
                }
            ],
        },
    )
    assert profile.rule("wcag-contrast").source == "safety"
    assert profile.rule("wcag-contrast").severity == "block"


def test_organization_can_raise_but_not_lower_safety(fixtures) -> None:
    compiler = PreferenceCompiler()
    raised = compiler.compile(
        repo=fixtures / "repo",
        organization_policy={
            "rules": [
                {
                    "id": "reduced-motion",
                    "source": "safety",
                    "severity": "block",
                    "statement": "Reduced motion is mandatory",
                }
            ]
        },
    )
    lowered = compiler.compile(
        repo=fixtures / "repo",
        organization_policy={
            "rules": [
                {
                    "id": "wcag-contrast",
                    "source": "safety",
                    "severity": "inform",
                    "statement": "Try to maintain contrast",
                }
            ]
        },
    )
    assert raised.rule("reduced-motion").severity == "block"
    assert lowered.rule("wcag-contrast").severity == "block"


@pytest.mark.parametrize(
    ("config", "match"),
    [
        ('schema_version = 1\ndesign_token_files = ["../outside.json"]\n', "escapes"),
        (
            'schema_version = 1\n[[rules]]\nid="same"\nsource="repository"\nseverity="warn"\nstatement="a"\n'
            '[[rules]]\nid="same"\nsource="repository"\nseverity="warn"\nstatement="b"\n',
            "duplicate",
        ),
        ('schema_version = 1\ninclude = ["remote.toml"]\n', "unsupported"),
    ],
)
def test_malformed_or_unsafe_repository_config_fails_closed(
    tmp_path: Path, config: str, match: str
) -> None:
    loopguard = tmp_path / ".loopguard"
    loopguard.mkdir()
    (loopguard / "preferences.toml").write_text(config)
    with pytest.raises(PreferenceCompileError, match=match):
        PreferenceCompiler().compile(repo=tmp_path)


def test_yaml_anchors_and_oversized_sources_are_rejected(tmp_path: Path) -> None:
    loopguard = tmp_path / ".loopguard"
    loopguard.mkdir()
    (loopguard / "preferences.toml").write_text(
        'schema_version = 1\ndesign_token_files = ["tokens.yaml"]\n'
    )
    (tmp_path / "tokens.yaml").write_text("base: &base {primary: '#fff'}\ncopy: *base\n")
    with pytest.raises(PreferenceCompileError, match="aliases|anchors"):
        PreferenceCompiler().compile(repo=tmp_path)

    (loopguard / "preferences.toml").write_text("x" * (256 * 1024 + 1))
    with pytest.raises(PreferenceCompileError, match="exceeds"):
        PreferenceCompiler().compile(repo=tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="symlink safety contract")
def test_repository_source_parent_symlinks_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "preferences.toml").write_text("schema_version = 1\n")
    (tmp_path / ".loopguard").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PreferenceCompileError, match="symlinks"):
        PreferenceCompiler().compile(repo=tmp_path)
