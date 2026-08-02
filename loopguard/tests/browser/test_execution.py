from __future__ import annotations

import pytest

from loopguard.browser.execution import BrowserExecutionPolicy, playwright_adapter_status
from loopguard.browser.manifest import PlaywrightManifest, PlaywrightTestFile
from loopguard.verify.plugins import ChangeRecord, PlaywrightImpactPlugin


@pytest.fixture
def browser_policy() -> BrowserExecutionPolicy:
    return BrowserExecutionPolicy(
        primary_project="chromium",
        completion_projects=["chromium", "webkit"],
        command=["npx", "playwright", "test"],
    )


def test_iteration_runs_impacted_chromium_without_trace(
    browser_policy: BrowserExecutionPolicy,
) -> None:
    plan = browser_policy.plan(
        phase="impacted",
        failures=0,
        test_files=["e2e/login.spec.ts"],
    )
    assert plan.projects == ["chromium"]
    assert plan.trace == "off"
    assert plan.argv == [
        "npx",
        "playwright",
        "test",
        "e2e/login.spec.ts",
        "--project=chromium",
        "--trace=off",
    ]


def test_first_retry_records_trace(browser_policy: BrowserExecutionPolicy) -> None:
    plan = browser_policy.plan(phase="impacted", failures=1)
    assert plan.trace == "on-first-retry"


def test_completion_runs_impacted_across_required_projects(
    browser_policy: BrowserExecutionPolicy,
) -> None:
    plan = browser_policy.plan(
        phase="completion", failures=0, test_files=["e2e/login.spec.ts"]
    )
    assert plan.selection == "impacted"
    assert plan.projects == ["chromium", "webkit"]
    assert plan.trace == "retain-on-failure"


def test_pr_gate_runs_configured_full_suite_and_allows_valid_shard(
    browser_policy: BrowserExecutionPolicy,
) -> None:
    plan = browser_policy.plan(phase="pr", failures=0, shard="2/4")
    assert plan.selection == "full"
    assert plan.test_files == []
    assert "--shard=2/4" in plan.argv
    with pytest.raises(ValueError):
        browser_policy.plan(phase="completion", failures=0, shard="2/4")


def test_always_on_trace_requires_explicit_diagnostic_mode(
    browser_policy: BrowserExecutionPolicy,
) -> None:
    with pytest.raises(ValueError, match="diagnostic"):
        browser_policy.plan(phase="impacted", failures=0, trace="on")
    assert (
        browser_policy.plan(
            phase="impacted", failures=0, trace="on", diagnostic=True
        ).trace
        == "on"
    )


def test_playwright_impact_plugin_emits_offline_coverage_edges(tmp_path) -> None:
    manifest = PlaywrightManifest(
        generated_by="test",
        smoke_projects=["smoke"],
        tests=[
            PlaywrightTestFile(
                file="e2e/login.spec.ts",
                projects=["chromium"],
                routes=["/login"],
                imports=["app/login/page.tsx"],
            )
        ],
    )
    contribution = PlaywrightImpactPlugin(manifest).analyze(
        tmp_path,
        [ChangeRecord(path="app/login/page.tsx")],
    )
    assert contribution.candidates == {
        "e2e/login.spec.ts": ["covers-route:/login"]
    }
    assert contribution.edges[0].source_path == "e2e/login.spec.ts"
    assert contribution.edges[0].target_path == "app/login/page.tsx"


def test_doctor_detection_requires_config_and_fixture_imports(tmp_path) -> None:
    (tmp_path / "playwright.config.ts").write_text(
        'import { defineLoopGuardConfig } from "@loopguard/playwright/config";\n'
    )
    tests = tmp_path / "e2e"
    tests.mkdir()
    (tests / "login.spec.ts").write_text(
        'import { test, expect } from "@loopguard/playwright";\n'
    )

    assert playwright_adapter_status(tmp_path) == {
        "status": "enabled",
        "config_found": True,
        "config_adapter_imported": True,
        "fixture_imports": 1,
        "enabled": True,
    }
