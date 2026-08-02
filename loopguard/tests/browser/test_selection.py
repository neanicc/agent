from __future__ import annotations

import pytest

from loopguard.browser.manifest import PlaywrightManifest, PlaywrightTestFile
from loopguard.browser.selection import PlaywrightSelector


@pytest.fixture
def playwright_manifest() -> PlaywrightManifest:
    return PlaywrightManifest(
        generated_by="playwright-1.61.1",
        smoke_projects=["smoke"],
        shared_paths=["src/theme.ts", "src/design/**"],
        config_paths=["playwright.config.ts", "e2e/global.setup.ts"],
        ignored_paths=["docs/**", "**/*.md"],
        tests=[
            PlaywrightTestFile(
                file="e2e/auth.setup.ts",
                projects=["setup"],
                tags=["setup"],
            ),
            PlaywrightTestFile(
                file="e2e/login.spec.ts",
                projects=["chromium", "webkit"],
                tags=["auth"],
                dependencies=["e2e/auth.setup.ts"],
                routes=["/login"],
                imports=["app/login/page.tsx"],
            ),
            PlaywrightTestFile(
                file="e2e/card.spec.ts",
                projects=["chromium"],
                components=["components/Card.tsx"],
            ),
        ],
    )


def test_route_change_selects_route_and_shared_setup(
    playwright_manifest: PlaywrightManifest,
) -> None:
    selector = PlaywrightSelector(playwright_manifest)
    result = selector.select(changed_paths=["app/login/page.tsx"])
    assert result.test_files == ["e2e/auth.setup.ts", "e2e/login.spec.ts"]
    assert result.explanations["e2e/login.spec.ts"] == ["covers-route:/login"]
    assert result.explanations["e2e/auth.setup.ts"] == ["dependency-of:e2e/login.spec.ts"]
    assert result.projects == ["chromium", "setup", "webkit"]
    assert result.confidence == "exact"


def test_unknown_shared_change_falls_back_to_smoke(
    playwright_manifest: PlaywrightManifest,
) -> None:
    result = PlaywrightSelector(playwright_manifest).select(["src/theme.ts"])
    assert result.projects == ["smoke"]
    assert result.confidence == "fallback"
    assert result.explanations == {"project:smoke": ["shared-change:src/theme.ts"]}


def test_component_and_test_changes_are_selected_with_explanations(
    playwright_manifest: PlaywrightManifest,
) -> None:
    selector = PlaywrightSelector(playwright_manifest)
    component = selector.select(["components/Card.tsx"])
    assert component.test_files == ["e2e/card.spec.ts"]
    assert component.explanations["e2e/card.spec.ts"] == [
        "covers-component:components/Card.tsx"
    ]
    direct = selector.select(["e2e/login.spec.ts"])
    assert direct.test_files == ["e2e/auth.setup.ts", "e2e/login.spec.ts"]
    assert "changed-test:e2e/login.spec.ts" in direct.explanations["e2e/login.spec.ts"]


def test_import_graph_edge_selects_consumer_and_docs_are_ignored(
    playwright_manifest: PlaywrightManifest,
) -> None:
    selector = PlaywrightSelector(
        playwright_manifest,
        dependency_edges={"src/session.ts": ["e2e/login.spec.ts"]},
    )
    selected = selector.select(["src/session.ts"])
    assert selected.test_files == ["e2e/auth.setup.ts", "e2e/login.spec.ts"]
    assert selected.explanations["e2e/login.spec.ts"] == [
        "dependency-graph:src/session.ts"
    ]
    ignored = selector.select(["docs/browser.md"])
    assert ignored.test_files == []
    assert ignored.projects == []
    assert ignored.confidence == "exact"


def test_unknown_source_and_configuration_changes_never_select_zero(
    playwright_manifest: PlaywrightManifest,
) -> None:
    selector = PlaywrightSelector(playwright_manifest)
    assert selector.select(["src/unknown.ts"]).projects == ["smoke"]
    assert selector.select(["playwright.config.ts"]).projects == ["smoke"]


def test_manifest_rejects_duplicate_files_and_unsafe_paths() -> None:
    with pytest.raises(ValueError, match="unique"):
        PlaywrightManifest(
            generated_by="test",
            smoke_projects=["smoke"],
            tests=[
                PlaywrightTestFile(file="e2e/a.spec.ts", projects=["chromium"]),
                PlaywrightTestFile(file="e2e/a.spec.ts", projects=["webkit"]),
            ],
        )
    with pytest.raises(ValueError):
        PlaywrightTestFile(file="../escape.spec.ts", projects=["chromium"])
