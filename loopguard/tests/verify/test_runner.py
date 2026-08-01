from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from loopguard.verify.isolation import (
    IsolationPlan,
    SandboxExecIsolationProvider,
    UnavailableIsolationProvider,
)
from loopguard.verify.models import CheckSpec, CheckStatus, IsolationLevel
from loopguard.verify.runner import CommandRunner, ExecutionAuthorization


class _Sandboxed:
    def prepare(self, worktree: Path, *, network_allowed: bool) -> IsolationPlan:
        return IsolationPlan(
            level=IsolationLevel.SANDBOXED,
            enforces_memory_limit=True,
            enforces_process_limit=True,
        )


class _SandboxedWithoutResourceLimits:
    def prepare(self, worktree: Path, *, network_allowed: bool) -> IsolationPlan:
        return IsolationPlan(level=IsolationLevel.SANDBOXED)


def _run(
    tmp_path: Path,
    command: list[str],
    *,
    runner: CommandRunner | None = None,
    timeout: int = 5,
    authorization: ExecutionAuthorization | None = None,
):
    active = runner or CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=_Sandboxed(),
    )
    return asyncio.run(
        active.run(
            CheckSpec(id="check", command=command, timeout_seconds=timeout),
            authorization=authorization or ExecutionAuthorization(approved=True),
        )
    )


def test_runner_uses_exec_form_and_logical_python_resolves_to_pinned_interpreter(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"
    execution = _run(
        tmp_path,
        ["python", "-c", "import sys; print(sys.executable)", ";", "touch", str(marker)],
    )

    assert execution.result.status is CheckStatus.PASSED
    assert str(Path(sys.executable).absolute()) in execution.stdout.decode()
    assert not marker.exists()


def test_runner_starts_from_allowlisted_environment_without_ambient_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-inherit")
    execution = _run(
        tmp_path,
        [
            "python",
            "-c",
            "import os; print(os.getenv('CI')); print(os.getenv('AWS_SECRET_ACCESS_KEY'))",
        ],
        authorization=ExecutionAuthorization(
            approved=True,
            environment={"CI": "true"},
        ),
    )

    assert execution.stdout.decode().splitlines() == ["true", "None"]
    assert b"must-not-inherit" not in execution.stdout


def test_unsandboxed_run_requires_separate_per_command_approval(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=UnavailableIsolationProvider(),
    )

    execution = _run(
        tmp_path,
        ["python", "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('x')"],
        runner=runner,
        authorization=ExecutionAuthorization(approved=True, allow_unsandboxed=False),
    )

    assert execution.result.status is CheckStatus.INCONCLUSIVE
    assert execution.result.isolation is IsolationLevel.UNSANDBOXED
    assert execution.reason == "unsandboxed_approval_required"
    assert not marker.exists()


def test_runner_rejects_parent_and_symlink_cwd_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "runner-outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=_Sandboxed(),
    )

    parent = asyncio.run(
        runner.run(
            CheckSpec(id="parent", command=["python", "-c", "print('x')"], cwd=".."),
            authorization=ExecutionAuthorization(approved=True),
        )
    )
    symlink = asyncio.run(
        runner.run(
            CheckSpec(id="symlink", command=["python", "-c", "print('x')"], cwd="linked"),
            authorization=ExecutionAuthorization(approved=True),
        )
    )

    assert parent.reason == "cwd_escape"
    assert symlink.reason == "cwd_escape"
    assert parent.result.status is symlink.result.status is CheckStatus.INCONCLUSIVE


def test_runner_enforces_timeout_and_output_limits(tmp_path: Path) -> None:
    runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=_Sandboxed(),
        max_output_bytes=128,
    )
    timed_out = _run(
        tmp_path,
        ["python", "-c", "import time; time.sleep(30)"],
        runner=runner,
        timeout=1,
    )
    bounded = _run(
        tmp_path,
        ["python", "-c", "print('x' * 1000)"],
        runner=runner,
    )

    assert timed_out.result.status is CheckStatus.TIMED_OUT
    assert timed_out.result.exit_code is None
    assert bounded.result.status is CheckStatus.PASSED
    assert bounded.output_truncated is True
    assert len(bounded.stdout) == 128


@pytest.mark.skipif(os.name != "posix", reason="POSIX resource limits and process groups")
def test_runner_enforces_file_and_process_group_limits(tmp_path: Path) -> None:
    output = tmp_path / "bounded.bin"
    file_runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=_Sandboxed(),
        file_limit_bytes=1024,
    )
    file_limited = _run(
        tmp_path,
        [
            "python",
            "-c",
            f"from pathlib import Path; Path({str(output)!r}).write_bytes(b'x' * 1000000)",
        ],
        runner=file_runner,
    )
    process_runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=_SandboxedWithoutResourceLimits(),
        process_limit=1,
        resource_probe=lambda _pid: (2, 0),
    )
    process_limited = _run(
        tmp_path,
        [
            "python",
            "-c",
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; "
            "time.sleep(30)']); time.sleep(30)",
        ],
        runner=process_runner,
    )

    assert file_limited.result.status is CheckStatus.FAILED
    assert output.stat().st_size <= 1024
    assert process_limited.result.failure_ids == ["resource_limit:processes"], (
        process_limited.result.status,
        process_limited.result.exit_code,
        process_limited.stderr.decode(errors="replace"),
    )


def test_missing_resource_monitor_is_inconclusive_before_repository_code_runs(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"
    runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=_SandboxedWithoutResourceLimits(),
        resource_probe=lambda _pid: None,
    )

    execution = _run(
        tmp_path,
        ["python", "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        runner=runner,
    )

    assert execution.result.status is CheckStatus.INCONCLUSIVE
    assert execution.reason == "resource_monitor_unavailable"
    assert not marker.exists()


def test_memory_limit_violation_kills_the_check_and_is_reported(tmp_path: Path) -> None:
    runner = CommandRunner(
        worktree=tmp_path,
        python_executable=Path(sys.executable),
        isolation_provider=_SandboxedWithoutResourceLimits(),
        memory_limit_bytes=1024,
        resource_probe=lambda _pid: (1, 2048),
    )

    execution = _run(
        tmp_path,
        ["python", "-c", "import time; time.sleep(30)"],
        runner=runner,
    )

    assert execution.result.status is CheckStatus.FAILED
    assert execution.result.failure_ids == ["resource_limit:memory"]


def test_deleted_bound_worktree_is_inconclusive_without_launching(tmp_path: Path) -> None:
    worktree = tmp_path / "repository"
    worktree.mkdir()
    runner = CommandRunner(
        worktree=worktree,
        python_executable=Path(sys.executable),
        isolation_provider=_Sandboxed(),
    )
    worktree.rmdir()

    execution = asyncio.run(
        runner.run(
            CheckSpec(id="check", command=["python", "-c", "print('unsafe')"]),
            authorization=ExecutionAuthorization(approved=True),
        )
    )

    assert execution.result.status is CheckStatus.INCONCLUSIVE
    assert execution.reason == "worktree_snapshot_failed"


def test_runner_parses_pytest_failure_ids_without_storing_unbounded_output(tmp_path: Path) -> None:
    execution = _run(
        tmp_path,
        [
            "python",
            "-c",
            "import sys; print('FAILED tests/test_old.py::test_old - AssertionError'); sys.exit(1)",
        ],
    )

    assert execution.result.status is CheckStatus.FAILED
    assert execution.result.failure_ids == ["tests/test_old.py::test_old"]
    assert execution.result.artifact_ids == [execution.output_artifact_id]


def test_runner_parses_jest_json_and_bounded_junit_xml(tmp_path: Path) -> None:
    jest_payload = (
        '{"testResults":[{"assertionResults":['
        '{"status":"failed","fullName":"auth rejects invalid login"}]}]}'
    )
    jest = _run(
        tmp_path,
        ["python", "-c", f"import sys; print({jest_payload!r}); sys.exit(1)"],
    )
    junit_script = (
        "from pathlib import Path; import sys; "
        "Path('report.xml').write_text('<testsuite><testcase classname=\"auth\" "
        "name=\"rejects\"><failure/></testcase></testsuite>'); sys.exit(1)"
    )
    junit = _run(
        tmp_path,
        ["python", "-c", junit_script, "--junitxml=report.xml"],
    )

    assert jest.result.failure_ids == ["auth rejects invalid login"]
    assert junit.result.failure_ids == ["auth::rejects"]


def test_junit_parser_rejects_document_type_declarations(tmp_path: Path) -> None:
    junit_script = (
        "from pathlib import Path; import sys; "
        "Path('report.xml').write_text('<!DOCTYPE testsuite [<!ENTITY x \"boom\">]>'"
        "'<testsuite><testcase name=\"&x;\"><failure/></testcase></testsuite>'); "
        "sys.exit(1)"
    )

    execution = _run(
        tmp_path,
        ["python", "-c", junit_script, "--junitxml=report.xml"],
    )

    assert execution.result.status is CheckStatus.FAILED
    assert execution.result.failure_ids == []


def test_macos_isolation_profile_denies_network_and_credential_reads_by_default(
    tmp_path: Path,
) -> None:
    provider = SandboxExecIsolationProvider()
    plan = provider.prepare(tmp_path, network_allowed=False)
    try:
        if plan.level is IsolationLevel.UNSANDBOXED:
            return
        profile = Path(plan.command_prefix[2]).read_text()
        assert "(allow network*)" not in profile
        assert str(tmp_path) in profile
        assert str(Path.home() / ".ssh") in profile
        assert "deny file-read" in profile
    finally:
        plan.cleanup()


if os.name == "posix":

    def test_timeout_kills_spawned_process_group(tmp_path: Path) -> None:
        marker = tmp_path / "child-finished"
        script = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',\"import time; from pathlib import Path; "
            f"time.sleep(3); Path({str(marker)!r}).write_text('x')\"]); "
            "time.sleep(30)"
        )

        execution = _run(tmp_path, ["python", "-c", script], timeout=1)
        assert execution.result.status is CheckStatus.TIMED_OUT
        asyncio.run(asyncio.sleep(3.2))
        assert not marker.exists()
