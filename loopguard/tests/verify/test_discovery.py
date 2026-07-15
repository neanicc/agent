from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopguard.verify.discovery import DiscoveryError, DiscoverySource, discover_checks


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "verify"


def test_discovers_python_and_package_script_checks_without_executing_them() -> None:
    python_checks = discover_checks(FIXTURES / "python_project")
    typescript_checks = discover_checks(FIXTURES / "typescript_project")

    assert [check.command for check in python_checks] == [["python", "-m", "pytest", "-q"]]
    assert ["npm", "test", "--", "--runInBand"] in [
        check.command for check in typescript_checks
    ]
    assert ["npx", "tsc", "--noEmit"] in [check.command for check in typescript_checks]
    assert all(check.source_hash for check in [*python_checks, *typescript_checks])


def test_verification_toml_has_precedence_and_discovery_has_no_side_effects(tmp_path: Path) -> None:
    config = tmp_path / ".loopguard" / "verification.toml"
    config.parent.mkdir()
    config.write_text(
        """
[[checks]]
id = "focused"
command = ["python", "-m", "pytest", "tests/test_auth.py"]
timeout_seconds = 45
environment = ["CI"]
network = false
""".strip()
    )
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    sentinel = tmp_path / "must-not-exist"

    checks = discover_checks(tmp_path)

    assert len(checks) == 1
    assert checks[0].source is DiscoverySource.VERIFICATION_CONFIG
    assert checks[0].requested_capabilities.environment == ["CI"]
    assert not sentinel.exists()


def test_nearest_agents_verification_block_precedes_tool_config(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        """
# Agent instructions

```verification
[[checks]]
id = "agent-check"
command = ["python", "-m", "pytest", "-q", "tests/unit"]
```
""".strip()
    )
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

    checks = discover_checks(tmp_path)

    assert [check.id for check in checks] == ["agent-check"]
    assert checks[0].source is DiscoverySource.AGENTS


def test_nested_discovery_uses_nearest_agents_file_within_git_boundary(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text(
        '```verification\n[[checks]]\nid="root"\ncommand=["pytest"]\n```\n'
    )
    nested = tmp_path / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text(
        '```verification\n[[checks]]\nid="nearest"\ncommand=["pytest","api"]\n```\n'
    )

    checks = discover_checks(nested)

    assert [check.id for check in checks] == ["nearest"]


def test_invalid_explicit_config_fails_closed_instead_of_falling_back(tmp_path: Path) -> None:
    config = tmp_path / ".loopguard" / "verification.toml"
    config.parent.mkdir()
    config.write_text("[[checks]]\ncommand = 'pytest -q'\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

    with pytest.raises(DiscoveryError, match="verification config"):
        discover_checks(tmp_path)


def test_symlinked_explicit_config_cannot_escape_repository(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-verification.toml"
    outside.write_text('[[checks]]\nid="outside"\ncommand=["pytest"]\n')
    config = tmp_path / ".loopguard" / "verification.toml"
    config.parent.mkdir()
    config.symlink_to(outside)

    with pytest.raises(DiscoveryError, match="symlink|escape"):
        discover_checks(tmp_path)


def test_malicious_package_script_is_reported_but_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "owned"
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": f"pytest -q; touch {marker}",
                }
            }
        )
    )

    checks = discover_checks(tmp_path)

    assert checks[0].risk == "denied"
    assert "shell_control_operator" in checks[0].risk_reasons
    assert not marker.exists()
