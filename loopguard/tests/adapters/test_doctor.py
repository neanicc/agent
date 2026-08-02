from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from loopguard.adapters.doctor import (
    CAPABILITY_NAMES,
    CapabilityState,
    build_diagnostic_payload,
    build_report,
    diagnostic_bundle_preview,
    write_diagnostic_bundle,
)


def test_report_never_claims_cloud_model_control() -> None:
    report = build_report(codex_version="test", claude_version="test")

    cloud = report.surface("codex-cloud")

    assert cloud.capabilities["observe"] in {"supported", "conditional"}
    assert cloud.capabilities["select_model"] == "unavailable"


def test_every_surface_reports_every_capability_with_a_stable_state() -> None:
    report = build_report(codex_version="0.144.0", claude_version="2.1.210")

    for surface in report.surfaces:
        assert set(surface.capabilities) == set(CAPABILITY_NAMES)
        assert set(surface.capabilities.values()) <= {state.value for state in CapabilityState}


def test_verified_attached_integration_reports_exact_coverage() -> None:
    report = build_report(
        codex_version="0.144.0",
        claude_version=None,
        installations={
            "codex": {
                "source": "plugin",
                "version": "0.1.0",
                "checksum": "sha256:abc",
                "hook_locations": ["~/.codex/plugins/loopguard"],
                "trust": "trusted",
                "verified": True,
                "events": ["SessionStart", "PreToolUse", "PostToolUse"],
            }
        },
        daemon_reachable=True,
    )

    attached = report.surface("codex-attached-local")

    assert attached.capabilities["observe"] == "supported"
    assert attached.capabilities["block_tool"] == "supported"
    assert attached.coverage == ("SessionStart", "PreToolUse", "PostToolUse")
    assert attached.integration_source == "plugin"
    assert attached.plugin_checksum == "sha256:abc"


def test_report_dict_contains_runtime_evidence_without_claiming_secrets() -> None:
    payload = build_report(
        codex_version=None,
        claude_version=None,
        daemon_reachable=False,
        bridge_health={"codex": "unreachable", "claude": "not_configured"},
    ).to_dict()

    assert payload["schema_version"] == 1
    assert payload["daemon_reachable"] is False
    assert payload["bridge_health"] == {
        "codex": "unreachable",
        "claude": "not_configured",
    }


def test_diagnostic_bundle_requires_preview_confirmation_and_redacts_paths(tmp_path: Path) -> None:
    report = build_report(
        codex_version="test",
        claude_version=None,
        installations={"codex": {"hook_locations": ["/secret/repository/.codex/hooks.json"]}},
    )
    destination = tmp_path / "diagnostics.zip"

    assert diagnostic_bundle_preview()["confirmation_required"] is True
    with pytest.raises(ValueError, match="LGD-BUNDLE-CONFIRMATION"):
        write_diagnostic_bundle(destination, report, confirmed=False)
    write_diagnostic_bundle(destination, report, confirmed=True)

    with zipfile.ZipFile(destination) as archive:
        content = archive.read("doctor-report.json").decode()
    assert "/secret/repository" not in content
    assert "<redacted-local-value>" in content


def test_diagnostic_payload_includes_core_health_but_drops_error_details() -> None:
    integrations = build_report(codex_version=None, claude_version=None)

    payload = build_diagnostic_payload(
        {
            "healthy": False,
            "daemon": "unreachable",
            "protocol": {"version": 1},
            "store": {"status": "missing"},
            "state_permissions": "missing",
            "integration": "not_configured",
            "errors": [
                {
                    "code": "LGD-DAEMON-001",
                    "retryable": True,
                    "details": {"socket": "/secret/socket"},
                }
            ],
        },
        integrations,
    )

    assert payload["daemon"] == "unreachable"
    assert payload["errors"] == [{"code": "LGD-DAEMON-001", "retryable": True}]
    assert "details" not in str(payload)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires optional Windows policy")
def test_diagnostic_bundle_replaces_symlink_without_overwriting_its_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    destination = tmp_path / "diagnostics.zip"
    destination.symlink_to(outside)

    write_diagnostic_bundle(
        destination,
        build_report(codex_version=None, claude_version=None),
        confirmed=True,
    )

    assert outside.read_text() == "keep"
    assert destination.is_file()
    assert not destination.is_symlink()
