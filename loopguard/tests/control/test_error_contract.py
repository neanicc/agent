from __future__ import annotations

import json
import os
import sqlite3
import tomllib
from pathlib import Path

import pytest

from loopguard.control.errors import ERROR_CATALOG, error_for, render_error
from loopguard.control.diagnostics import build_doctor_report
from loopguard.control.paths import ControlPaths


REQUIRED_FIELDS = {
    "code",
    "message",
    "cause",
    "suggested_commands",
    "doc_url",
    "request_id",
    "retryable",
    "details",
}


@pytest.mark.parametrize(
    "code",
    [
        "LGD-DAEMON-001",
        "LGD-STATE-002",
        "LGD-SCHEMA-003",
        "LGD-TRUST-004",
        "LGD-CAP-005",
        "LGD-DEPS-006",
    ],
)
def test_required_error_catalog_entries_have_complete_contract(code):
    payload = error_for(code).to_dict()

    assert set(payload) == REQUIRED_FIELDS
    assert payload["code"] == code
    assert payload["message"]
    assert payload["cause"]
    assert payload["suggested_commands"]
    assert payload["doc_url"].endswith(f"errors.md#{code.lower()}")


def test_error_details_are_recursive_redacted_and_json_safe():
    payload = error_for(
        "LGD-DAEMON-001",
        request_id="req-1",
        details={
            "Authorization": "Bearer must-not-leak",
            "nested": {"api_key": "sk-must-not-leak"},
        },
    ).to_dict()
    encoded = json.dumps(payload)

    assert payload["request_id"] == "req-1"
    assert payload["details"]["Authorization"] == "[REDACTED]"
    assert payload["details"]["nested"]["api_key"] == "[REDACTED]"
    assert "must-not-leak" not in encoded


def test_human_error_output_puts_fix_before_problem_and_never_prints_traceback():
    rendered = render_error(error_for("LGD-DAEMON-001"), as_json=False)

    assert rendered.index("Fix:") < rendered.index("Problem:")
    assert "LGD-DAEMON-001" in rendered
    assert "traceback" not in rendered.lower()


def test_error_catalog_codes_are_unique_and_documentable():
    assert len(ERROR_CATALOG) == len(set(ERROR_CATALOG))
    reference = (Path(__file__).resolve().parents[3] / "docs/reference/errors.md").read_text()
    assert all(f"## {code}" in reference for code in ERROR_CATALOG)


def test_all_dev_extra_contains_every_product_extra():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    extras = pyproject["project"]["optional-dependencies"]
    product_groups = {"control", "server", "cerebras", "litellm"}
    assert product_groups <= extras.keys()

    all_dev_names = {_requirement_name(requirement) for requirement in extras["all-dev"]}
    for group in product_groups:
        assert {_requirement_name(requirement) for requirement in extras[group]} <= all_dev_names
    assert "build" in {_requirement_name(requirement) for requirement in extras["dev"]}


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_doctor_names_unsafe_state_permissions(tmp_path):
    os.chmod(tmp_path, 0o755)

    report = build_doctor_report(ControlPaths.from_home(tmp_path))

    assert report["state_permissions"] == "unsafe"
    assert "LGD-STATE-002" in {error["code"] for error in report["errors"]}
    assert tmp_path.stat().st_mode & 0o777 == 0o755


def test_doctor_reports_schema_migration_without_mutating_database(tmp_path):
    path = tmp_path / "events.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 1")
    connection.close()
    path.chmod(0o600)
    before = path.read_bytes()

    report = build_doctor_report(ControlPaths.from_home(tmp_path))

    assert report["store"]["status"] == "migration_needed"
    assert "LGD-SCHEMA-003" in {error["code"] for error in report["errors"]}
    assert path.read_bytes() == before


def test_doctor_reports_explicit_integration_trust_pending(tmp_path):
    paths = ControlPaths.from_home(tmp_path)
    paths.integration_trust.write_text("pending")

    report = build_doctor_report(paths)

    assert report["integration"] == "trust_pending"
    assert "LGD-TRUST-004" in {error["code"] for error in report["errors"]}


def test_doctor_names_platform_capability_unavailable(tmp_path):
    report = build_doctor_report(ControlPaths.from_home(tmp_path), platform_name="nt")

    assert report["daemon"] == "capability_unavailable"
    assert "LGD-CAP-005" in {error["code"] for error in report["errors"]}


def _requirement_name(requirement: str) -> str:
    head = requirement.split(";", 1)[0].strip()
    for separator in ("[", "<", ">", "=", "!", "~"):
        head = head.split(separator, 1)[0]
    return head.strip().lower().replace("_", "-")
