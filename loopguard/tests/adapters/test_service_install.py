from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import loopguard.adapters.service_install as service_install_module
from loopguard.adapters.service_install import (
    ServiceInstallError,
    install_user_service,
    render_user_service,
    safe_fix_user_service,
    service_status,
    uninstall_user_service,
    verify_synthetic_event,
)


class Commands:
    def __init__(self, *, status_returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.status_returncode = status_returncode

    def __call__(self, command, **_kwargs):
        normalized = [str(part) for part in command]
        self.calls.append(normalized)
        returncode = (
            self.status_returncode if "is-active" in normalized or "print" in normalized else 0
        )
        return subprocess.CompletedProcess(normalized, returncode, stdout="active\n", stderr="")


class FailingCommands(Commands):
    def __init__(self, *, fail_command: str) -> None:
        super().__init__()
        self.fail_command = fail_command

    def __call__(self, command, **kwargs):
        result = super().__call__(command, **kwargs)
        if self.fail_command in [str(part) for part in command]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")
        return result


def test_macos_service_runs_foreground_daemon_at_login(tmp_path: Path) -> None:
    rendered = render_user_service(
        platform="darwin", executable="/opt/loopguard/bin/loopguard", home=tmp_path
    )

    assert "RunAtLoad" in rendered.body
    assert "<string>daemon</string>" in rendered.body
    assert "<string>start</string>" in rendered.body
    assert "<string>--foreground</string>" in rendered.body
    assert "<string>/opt/loopguard/bin/loopguard</string>" in rendered.body
    assert str(tmp_path) in rendered.body
    assert "<key>Umask</key>" in rendered.body
    assert rendered.automatic_startup == "supported"


def test_linux_service_restarts_on_failure(tmp_path: Path) -> None:
    rendered = render_user_service(
        platform="linux", executable="/opt/loopguard/bin/loopguard", home=tmp_path
    )

    assert "Restart=on-failure" in rendered.body
    assert "WantedBy=default.target" in rendered.body
    assert (
        'ExecStart="/opt/loopguard/bin/loopguard" daemon start --foreground --home "'
        in rendered.body
    )
    assert rendered.automatic_startup == "supported"
    assert "NoNewPrivileges=true" in rendered.body
    assert "ProtectSystem=strict" in rendered.body
    assert "UMask=0077" in rendered.body


def test_renderer_rejects_relative_executable_and_control_characters(tmp_path: Path) -> None:
    with pytest.raises(ServiceInstallError, match="absolute"):
        render_user_service(platform="linux", executable="loopguard", home=tmp_path)
    with pytest.raises(ServiceInstallError, match="control"):
        render_user_service(
            platform="linux",
            executable="/opt/loopguard\n/bin/loopguard",
            home=tmp_path,
        )


def test_renderer_rejects_a_malformed_packaged_template(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(service_install_module, "_template", lambda _name: "[Service]\n")

    with pytest.raises(ServiceInstallError, match="malformed"):
        render_user_service(
            platform="linux",
            executable="/opt/loopguard",
            home=tmp_path,
        )


def test_macos_install_validates_staged_plist_then_bootstraps_and_kickstarts(
    tmp_path: Path,
) -> None:
    commands = Commands()
    destination = tmp_path / "com.loopguard.daemon.plist"

    result = install_user_service(
        executable=Path("/opt/loopguard"),
        home=tmp_path / "state",
        platform="darwin",
        destination=destination,
        command_runner=commands,
    )

    assert result.changed is True
    assert commands.calls[0][0:2] == ["plutil", "-lint"]
    assert commands.calls[1] == [
        "launchctl",
        "bootstrap",
        f"gui/{os.getuid()}",
        str(destination),
    ]
    assert commands.calls[2] == [
        "launchctl",
        "kickstart",
        "-k",
        f"gui/{os.getuid()}/com.loopguard.daemon",
    ]
    assert commands.calls[3] == [
        "launchctl",
        "print",
        f"gui/{os.getuid()}/com.loopguard.daemon",
    ]


def test_dry_run_previews_without_files_or_commands(tmp_path: Path) -> None:
    commands = Commands()
    destination = tmp_path / "loopguardd.service"

    result = install_user_service(
        executable=Path("/opt/loopguard"),
        home=tmp_path / "state",
        platform="linux",
        destination=destination,
        command_runner=commands,
        dry_run=True,
    )

    assert result.status == "preview"
    assert result.changed is True
    assert not destination.exists()
    assert commands.calls == []


def test_linux_install_is_idempotent_and_upgrade_restarts(tmp_path: Path) -> None:
    commands = Commands()
    destination = tmp_path / "systemd" / "loopguardd.service"

    first = install_user_service(
        executable=Path("/opt/loopguard/bin/loopguard"),
        home=tmp_path / "state",
        platform="linux",
        destination=destination,
        command_runner=commands,
    )
    first_calls = list(commands.calls)
    call_count = len(commands.calls)
    second = install_user_service(
        executable=Path("/opt/loopguard/bin/loopguard"),
        home=tmp_path / "state",
        platform="linux",
        destination=destination,
        command_runner=commands,
    )
    assert len(commands.calls) == call_count
    upgraded = install_user_service(
        executable=Path("/new/loopguard"),
        home=tmp_path / "state",
        platform="linux",
        destination=destination,
        command_runner=commands,
    )

    assert first.changed is True
    assert first_calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "loopguardd.service"],
        ["systemctl", "--user", "is-active", "loopguardd.service"],
    ]
    assert second.changed is False
    assert upgraded.changed is True
    assert commands.calls[-4:] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "loopguardd.service"],
        ["systemctl", "--user", "restart", "loopguardd.service"],
        ["systemctl", "--user", "is-active", "loopguardd.service"],
    ]
    if os.name == "posix":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_installer_refuses_to_overwrite_unrelated_service(tmp_path: Path) -> None:
    destination = tmp_path / "loopguardd.service"
    destination.write_text("[Service]\nExecStart=/friend/service\n")

    with pytest.raises(ServiceInstallError, match="not LoopGuard-owned"):
        install_user_service(
            executable=Path("/opt/loopguard"),
            home=tmp_path / "state",
            platform="linux",
            destination=destination,
            command_runner=Commands(),
        )

    assert "friend" in destination.read_text()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires optional Windows policy")
def test_installer_does_not_create_files_through_a_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ServiceInstallError, match="symlink"):
        install_user_service(
            executable=Path("/opt/loopguard"),
            home=tmp_path / "state",
            platform="linux",
            destination=link / "nested" / "loopguardd.service",
            command_runner=Commands(),
        )

    assert list(outside.iterdir()) == []


def test_failed_upgrade_restores_previous_definition(tmp_path: Path) -> None:
    destination = tmp_path / "loopguardd.service"
    install_user_service(
        executable=Path("/old/loopguard"),
        home=tmp_path / "state",
        platform="linux",
        destination=destination,
        command_runner=Commands(),
    )
    previous = destination.read_text()

    with pytest.raises(ServiceInstallError, match="service command failed"):
        install_user_service(
            executable=Path("/new/loopguard"),
            home=tmp_path / "state",
            platform="linux",
            destination=destination,
            command_runner=FailingCommands(fail_command="restart"),
        )

    assert destination.read_text() == previous


def test_uninstall_removes_only_owned_service_and_retains_data(tmp_path: Path) -> None:
    commands = Commands()
    destination = tmp_path / "loopguardd.service"
    home = tmp_path / "state"
    home.mkdir()
    data = home / "events.db"
    data.write_text("keep")
    install_user_service(
        executable=Path("/opt/loopguard"),
        home=home,
        platform="linux",
        destination=destination,
        command_runner=commands,
    )

    result = uninstall_user_service(
        home=home,
        platform="linux",
        destination=destination,
        command_runner=commands,
    )

    assert result.changed is True
    assert not destination.exists()
    assert data.read_text() == "keep"
    assert commands.calls[-2:] == [
        ["systemctl", "--user", "disable", "--now", "loopguardd.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_status_reports_runtime_evidence_not_just_a_file(tmp_path: Path) -> None:
    commands = Commands(status_returncode=3)
    destination = tmp_path / "loopguardd.service"
    destination.write_text(
        render_user_service(
            platform="linux",
            executable="/opt/loopguard",
            home=tmp_path,
        ).body
    )
    if os.name == "posix":
        os.chmod(destination, 0o600)
    result = service_status(
        home=tmp_path,
        platform="linux",
        destination=destination,
        command_runner=commands,
    )

    assert result.installed is True
    assert result.status == "stopped"
    assert result.running is False
    assert commands.calls == [["systemctl", "--user", "is-active", "loopguardd.service"]]


@pytest.mark.skipif(os.name != "posix", reason="owner-mode repair is POSIX-specific")
def test_safe_fix_repairs_readable_owned_definition_but_not_unrelated_state(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "loopguardd.service"
    destination.write_text(
        render_user_service(
            platform="linux",
            executable="/opt/loopguard",
            home=tmp_path,
        ).body
    )
    os.chmod(destination, 0o644)
    unrelated = tmp_path / "friend.service"
    unrelated.write_text("keep")

    fixed = safe_fix_user_service(
        home=tmp_path,
        platform="linux",
        destination=destination,
        command_runner=Commands(),
    )

    assert fixed == ("service_permissions",)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert unrelated.read_text() == "keep"


def test_synthetic_event_retries_bounded_transient_startup_failure(tmp_path: Path) -> None:
    from loopguard.adapters.hook_client import HookClientError

    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def send(self, event):
            self.calls += 1
            if self.calls < 3:
                raise HookClientError("daemon_unreachable", retryable=True)
            return SimpleNamespace(target=SimpleNamespace(target_id=event.session.session_id))

    client = FlakyClient()

    verify_synthetic_event(home=tmp_path, client=client, timeout_seconds=1)

    assert client.calls == 3
