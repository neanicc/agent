from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from loopguard.adapters.service_install import (
    install_user_service,
    render_user_service,
    uninstall_user_service,
)


class WindowsCommands:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        normalized = [str(part) for part in command]
        self.calls.append(normalized)
        return subprocess.CompletedProcess(normalized, 0, stdout="SUCCESS\n", stderr="")


def test_windows_task_binds_current_user_sid_quotes_paths_and_restarts(tmp_path: Path) -> None:
    rendered = render_user_service(
        platform="win32",
        executable=r"C:\Program Files\LoopGuard\loopguard.exe",
        home=tmp_path / "state with spaces",
        user_sid="S-1-5-21-1000",
    )

    assert "S-1-5-21-1000" in rendered.body
    assert "InteractiveToken" in rendered.body
    assert r"C:\Program Files\LoopGuard\loopguard.exe" in rendered.body
    assert "--foreground" in rendered.body
    assert "RestartOnFailure" in rendered.body
    assert rendered.automatic_startup == "experimental"
    ET.fromstring(rendered.body)


def test_windows_install_and_uninstall_use_exact_current_user_task(tmp_path: Path) -> None:
    commands = WindowsCommands()
    destination = tmp_path / "loopguardd-windows.xml"

    installed = install_user_service(
        executable=Path(r"C:\LoopGuard\loopguard.exe"),
        home=tmp_path / "state",
        platform="win32",
        destination=destination,
        command_runner=commands,
        user_sid="S-1-5-21-1000",
    )
    removed = uninstall_user_service(
        home=tmp_path / "state",
        platform="win32",
        destination=destination,
        command_runner=commands,
    )

    assert installed.changed is True
    assert commands.calls[0][:4] == ["schtasks", "/Create", "/TN", r"\LoopGuard\Daemon"]
    assert "/XML" in commands.calls[0]
    assert removed.changed is True
    assert commands.calls[-1] == [
        "schtasks",
        "/Delete",
        "/TN",
        r"\LoopGuard\Daemon",
        "/F",
    ]
