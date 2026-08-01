from __future__ import annotations

import os

import loopguard.context.git_runtime as git_runtime_module
from loopguard.context.git_runtime import resolve_git_runtime


def test_windows_git_runtime_uses_runner_path_and_required_process_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PATH", r"C:\Program Files\Git\bin;C:\Windows\System32")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    observed: dict[str, str] = {}

    def fake_which(command: str, *, path: str) -> str:
        observed[command] = path
        return r"C:\Program Files\Git\bin\git.exe"

    monkeypatch.setattr(git_runtime_module.shutil, "which", fake_which)

    runtime = resolve_git_runtime(platform="nt")

    assert runtime is not None
    assert observed == {"git": r"C:\Program Files\Git\bin;C:\Windows\System32"}
    assert runtime.environment["SYSTEMROOT"] == r"C:\Windows"
    assert runtime.environment["COMSPEC"] == r"C:\Windows\System32\cmd.exe"


def test_posix_git_runtime_ignores_user_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/tmp/untrusted")
    observed: dict[str, str] = {}

    def fake_which(command: str, *, path: str) -> str:
        observed[command] = path
        return "/usr/bin/git"

    monkeypatch.setattr(git_runtime_module.shutil, "which", fake_which)

    runtime = resolve_git_runtime(platform="posix")

    assert runtime is not None
    assert observed == {"git": os.defpath}
    assert runtime.environment == {"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"}
