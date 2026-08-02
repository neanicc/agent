from __future__ import annotations

from pathlib import Path

import pytest

from loopguard.preferences.compiler import PreferenceCompiler
from loopguard.preferences.evaluators import PreferenceArtifact


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "preferences"


@pytest.fixture
def profile(fixtures):
    return PreferenceCompiler().compile(repo=fixtures / "repo")


@pytest.fixture
def ui_diff() -> PreferenceArtifact:
    return PreferenceArtifact(
        artifact_id="sha256:" + "b" * 64,
        kind="source_diff",
        payload={"added_text": "const color = tokens.color.primary;", "imports": ["tokens"]},
    )


@pytest.fixture
def axe_artifact() -> PreferenceArtifact:
    return PreferenceArtifact(
        artifact_id="sha256:" + "c" * 64,
        kind="web_axe",
        payload={"violations": [], "incomplete": []},
    )
