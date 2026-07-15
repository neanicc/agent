from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping

from .baseline import BaselineCaptureError, capture_repository_snapshot
from .isolation import AutoIsolationProvider, IsolationProvider
from .models import CheckResult, CheckSpec, CheckStatus, IsolationLevel

if TYPE_CHECKING:
    from .trust import TrustDecision


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PYTEST_FAILURE = re.compile(r"(?:^|\s)FAILED\s+([^\s]+?::[^\s]+?)(?:\s|$)")
_MAX_JUNIT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    approved: bool
    allow_unsandboxed: bool = False
    network: bool = False
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))

    @classmethod
    def from_trust(
        cls,
        decision: TrustDecision,
        *,
        environment: Mapping[str, str] | None = None,
        allow_unsandboxed: bool = False,
    ) -> ExecutionAuthorization:
        if not decision.allowed:
            return cls(approved=False)
        values = dict(environment or {})
        if set(values) != set(decision.preview.environment):
            raise ValueError("execution environment does not match the approved preview")
        return cls(
            approved=True,
            allow_unsandboxed=allow_unsandboxed,
            network=decision.preview.network,
            environment=values,
        )


@dataclass(frozen=True, slots=True)
class CheckExecution:
    result: CheckResult
    stdout: bytes
    stderr: bytes
    output_artifact_id: str
    output_truncated: bool = False
    reason: str | None = None


class CommandRunner:
    def __init__(
        self,
        *,
        worktree: Path,
        python_executable: Path,
        isolation_provider: IsolationProvider | None = None,
        executable_path: str | None = None,
        max_output_bytes: int = 2 * 1024 * 1024,
        memory_limit_bytes: int = 2 * 1024 * 1024 * 1024,
        file_limit_bytes: int = 512 * 1024 * 1024,
        process_limit: int = 128,
        resource_probe: Callable[[int], tuple[int, int] | None] | None = None,
    ) -> None:
        self.worktree = worktree.expanduser().resolve(strict=True)
        self.python_executable = python_executable.expanduser().absolute()
        if not self.python_executable.is_file() or not os.access(self.python_executable, os.X_OK):
            raise ValueError("configured Python interpreter is unavailable")
        if max_output_bytes <= 0:
            raise ValueError("output limit must be positive")
        self.isolation_provider = isolation_provider or AutoIsolationProvider()
        self.executable_path = executable_path or os.pathsep.join(
            dict.fromkeys(
                (
                    str(self.python_executable.parent),
                    *os.defpath.split(os.pathsep),
                )
            )
        )
        self.max_output_bytes = max_output_bytes
        self.memory_limit_bytes = memory_limit_bytes
        self.file_limit_bytes = file_limit_bytes
        self.process_limit = process_limit
        self.resource_probe = resource_probe or _process_tree_usage

    async def run(
        self,
        spec: CheckSpec,
        *,
        authorization: ExecutionAuthorization,
    ) -> CheckExecution:
        started_at = datetime.now(timezone.utc)
        try:
            worktree_hash = _hash_worktree(self.worktree)
        except (OSError, ValueError, BaselineCaptureError):
            unavailable_hash = hashlib.sha256(
                f"worktree-unavailable\0{self.worktree}".encode()
            ).hexdigest()
            return self._inconclusive(
                spec,
                started_at,
                unavailable_hash,
                IsolationLevel.UNSANDBOXED,
                "worktree_snapshot_failed",
            )
        if not authorization.approved:
            return self._inconclusive(
                spec,
                started_at,
                worktree_hash,
                IsolationLevel.UNSANDBOXED,
                "command_approval_required",
            )
        try:
            cwd = _resolve_cwd(self.worktree, spec.cwd)
            command = self._resolve_command(spec.command)
            environment = self._environment(authorization.environment)
        except ValueError as exc:
            return self._inconclusive(
                spec,
                started_at,
                worktree_hash,
                IsolationLevel.UNSANDBOXED,
                str(exc),
            )
        plan = self.isolation_provider.prepare(
            self.worktree,
            network_allowed=authorization.network,
        )
        if plan.level is IsolationLevel.UNSANDBOXED and not authorization.allow_unsandboxed:
            plan.cleanup()
            return self._inconclusive(
                spec,
                started_at,
                worktree_hash,
                IsolationLevel.UNSANDBOXED,
                "unsandboxed_approval_required",
            )
        monitor_memory = not plan.enforces_memory_limit
        monitor_processes = not plan.enforces_process_limit
        if (monitor_memory or monitor_processes) and self.resource_probe(os.getpid()) is None:
            plan.cleanup()
            return self._inconclusive(
                spec,
                started_at,
                worktree_hash,
                plan.level,
                "resource_monitor_unavailable",
            )
        environment.update(plan.environment)
        stdout_task: asyncio.Task[tuple[bytes, bool]] | None = None
        stderr_task: asyncio.Task[tuple[bytes, bool]] | None = None
        process: asyncio.subprocess.Process | None = None
        timed_out = False
        resource_exceeded: str | None = None
        resource_task: asyncio.Task[str | None] | None = None
        try:
            launch_command = self._resource_wrapped_command(
                spec.timeout_seconds,
                [*plan.command_prefix, *command],
            )
            process = await asyncio.create_subprocess_exec(
                *launch_command,
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            assert process.stdout is not None
            assert process.stderr is not None
            stdout_task = asyncio.create_task(
                _read_bounded(process.stdout, self.max_output_bytes)
            )
            stderr_task = asyncio.create_task(
                _read_bounded(process.stderr, self.max_output_bytes)
            )
            if os.name == "posix" and (monitor_memory or monitor_processes):
                resource_task = asyncio.create_task(
                    _monitor_process_group(
                        process,
                        memory_limit_bytes=self.memory_limit_bytes,
                        process_limit=self.process_limit,
                        monitor_memory=monitor_memory,
                        monitor_processes=monitor_processes,
                        resource_probe=self.resource_probe,
                    )
                )
            try:
                await asyncio.wait_for(process.wait(), timeout=spec.timeout_seconds)
            except TimeoutError:
                timed_out = True
                await _kill_process_tree(process)
            if resource_task is not None:
                resource_exceeded = await resource_task
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
        except asyncio.CancelledError:
            if process is not None:
                await _kill_process_tree(process)
            pending = [
                task
                for task in (stdout_task, stderr_task, resource_task)
                if task is not None
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise
        except (OSError, subprocess.SubprocessError):
            stdout = b""
            stderr = b""
            stdout_truncated = False
            stderr_truncated = False
            return self._inconclusive(
                spec,
                started_at,
                worktree_hash,
                plan.level,
                "process_start_failed",
            )
        finally:
            plan.cleanup()
        completed_at = datetime.now(timezone.utc)
        artifact_id = _artifact_id(stdout, stderr)
        if timed_out:
            status = CheckStatus.TIMED_OUT
            exit_code = None
            failure_ids: list[str] = []
        elif resource_exceeded is not None:
            status = (
                CheckStatus.INCONCLUSIVE
                if resource_exceeded == "monitor_unavailable"
                else CheckStatus.FAILED
            )
            exit_code = process.returncode if process is not None else None
            failure_ids = (
                []
                if resource_exceeded == "monitor_unavailable"
                else [f"resource_limit:{resource_exceeded}"]
            )
        else:
            assert process is not None
            exit_code = process.returncode
            status = CheckStatus.PASSED if exit_code == 0 else CheckStatus.FAILED
            failure_ids = _parse_failure_ids(spec, cwd, stdout, stderr)
        result = CheckResult(
            check_id=spec.id,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            exit_code=exit_code,
            failure_ids=failure_ids,
            artifact_ids=[artifact_id],
            isolation=plan.level,
            worktree_hash=worktree_hash,
        )
        return CheckExecution(
            result=result,
            stdout=stdout,
            stderr=stderr,
            output_artifact_id=artifact_id,
            output_truncated=stdout_truncated or stderr_truncated,
            reason=(
                "resource_monitor_unavailable"
                if resource_exceeded == "monitor_unavailable"
                else None
            ),
        )

    def _resolve_command(self, command: list[str]) -> list[str]:
        if command[0] == "python":
            return [str(self.python_executable), *command[1:]]
        executable = Path(command[0])
        if executable.is_absolute():
            try:
                resolved = executable.resolve(strict=True)
            except OSError as exc:
                raise ValueError("executable_unavailable") from exc
        else:
            found = shutil.which(command[0], path=self.executable_path)
            if found is None:
                raise ValueError("executable_unavailable")
            resolved = Path(found).resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise ValueError("executable_unavailable")
        return [str(resolved), *command[1:]]

    def _environment(self, requested: Mapping[str, str]) -> dict[str, str]:
        environment = {"PATH": self.executable_path, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
        for name, value in requested.items():
            if not _ENVIRONMENT_NAME.fullmatch(name) or "\x00" in value:
                raise ValueError("invalid_environment")
            environment[name] = value
        return environment

    def _resource_wrapped_command(
        self,
        timeout_seconds: int,
        command: list[str],
    ) -> list[str]:
        return [
            str(self.python_executable),
            "-m",
            "loopguard.verify.exec_wrapper",
            "--cpu",
            str(timeout_seconds),
            "--memory",
            str(self.memory_limit_bytes),
            "--file",
            str(self.file_limit_bytes),
            "--",
            *command,
        ]

    def _inconclusive(
        self,
        spec: CheckSpec,
        started_at: datetime,
        worktree_hash: str,
        isolation: IsolationLevel,
        reason: str,
    ) -> CheckExecution:
        completed_at = datetime.now(timezone.utc)
        artifact_id = _artifact_id(b"", b"")
        return CheckExecution(
            result=CheckResult(
                check_id=spec.id,
                status=CheckStatus.INCONCLUSIVE,
                started_at=started_at,
                completed_at=completed_at,
                exit_code=None,
                artifact_ids=[],
                isolation=isolation,
                parser_error=reason,
                worktree_hash=worktree_hash,
            ),
            stdout=b"",
            stderr=b"",
            output_artifact_id=artifact_id,
            reason=reason,
        )


async def _read_bounded(
    stream: asyncio.StreamReader,
    limit: int,
) -> tuple[bytes, bool]:
    output = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            break
        remaining = limit - len(output)
        if remaining > 0:
            output.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(output), truncated


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    observed = await asyncio.to_thread(_process_tree_stats, process.pid)
    descendants = observed[0] if observed is not None else {process.pid}
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    for pid in sorted(descendants - {process.pid}, reverse=True):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


async def _monitor_process_group(
    process: asyncio.subprocess.Process,
    *,
    memory_limit_bytes: int,
    process_limit: int,
    monitor_memory: bool,
    monitor_processes: bool,
    resource_probe: Callable[[int], tuple[int, int] | None],
) -> str | None:
    while process.returncode is None:
        usage = await asyncio.to_thread(resource_probe, process.pid)
        if usage is None:
            await _kill_process_tree(process)
            return "monitor_unavailable"
        count, rss_bytes = usage
        reason = (
            "memory"
            if monitor_memory and rss_bytes > memory_limit_bytes
            else "processes"
            if monitor_processes and count > process_limit
            else None
        )
        if reason is not None:
            await _kill_process_tree(process)
            return reason
        await asyncio.sleep(0.1)
    return None


def _process_tree_stats(root_pid: int) -> tuple[set[int], int] | None:
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,rss="],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=1,
            check=False,
            env={"PATH": os.defpath, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    if len(result.stdout) > 2 * 1024 * 1024:
        return None
    entries: list[tuple[int, int, int]] = []
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) != 3 or not all(column.isdigit() for column in columns):
            continue
        entries.append((int(columns[0]), int(columns[1]), int(columns[2])))
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid, _rss in entries:
            if parent_pid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    rss_bytes = sum(rss * 1024 for pid, _parent, rss in entries if pid in selected)
    return selected, rss_bytes


def _process_tree_usage(root_pid: int) -> tuple[int, int] | None:
    result = _process_tree_stats(root_pid)
    if result is None:
        return None
    processes, rss_bytes = result
    return len(processes), rss_bytes


def _resolve_cwd(worktree: Path, raw: str) -> Path:
    candidate = worktree / raw
    try:
        relative = candidate.relative_to(worktree)
    except ValueError as exc:
        raise ValueError("cwd_escape") from exc
    current = worktree
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("cwd_escape")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(worktree)
    except (OSError, ValueError) as exc:
        raise ValueError("cwd_escape") from exc
    if not resolved.is_dir():
        raise ValueError("cwd_unavailable")
    return resolved


def _artifact_id(stdout: bytes, stderr: bytes) -> str:
    digest = hashlib.sha256(b"loopguard-check-output-v1\0" + stdout + b"\0" + stderr).hexdigest()
    return f"sha256:{digest}"


def _parse_failure_ids(
    spec: CheckSpec,
    cwd: Path,
    stdout: bytes,
    stderr: bytes,
) -> list[str]:
    text = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
    failures = {match.group(1) for match in _PYTEST_FAILURE.finditer(text)}
    failures.update(_parse_jest_json(stdout))
    junit = _junit_path(spec, cwd)
    if junit is not None:
        failures.update(_parse_junit(junit, cwd))
    return sorted(failures)


def _parse_jest_json(raw: bytes) -> set[str]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    failures: set[str] = set()
    results = payload.get("testResults", [])
    if not isinstance(results, list):
        return failures
    for suite in results:
        if not isinstance(suite, dict):
            continue
        assertions = suite.get("assertionResults", [])
        if not isinstance(assertions, list):
            continue
        for assertion in assertions:
            if (
                isinstance(assertion, dict)
                and assertion.get("status") == "failed"
                and isinstance(assertion.get("fullName"), str)
            ):
                failures.add(assertion["fullName"])
    return failures


def _junit_path(spec: CheckSpec, cwd: Path) -> Path | None:
    for index, argument in enumerate(spec.command):
        raw: str | None = None
        if argument.startswith("--junitxml="):
            raw = argument.partition("=")[2]
        elif argument == "--junitxml" and index + 1 < len(spec.command):
            raw = spec.command[index + 1]
        if raw:
            return cwd / raw
    return None


def _parse_junit(path: Path, cwd: Path) -> set[str]:
    try:
        if path.is_symlink():
            return set()
        resolved = path.resolve(strict=True)
        resolved.relative_to(cwd)
        if resolved.stat().st_size > _MAX_JUNIT_BYTES:
            return set()
        raw = resolved.read_bytes()
        upper = raw.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            return set()
        root = ET.fromstring(raw)
    except (OSError, ValueError, ET.ParseError):
        return set()
    failures: set[str] = set()
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "unknown")
        failures.add(f"{classname}::{name}" if classname else name)
    return failures


def _hash_worktree(worktree: Path) -> str:
    if not worktree.is_dir():
        raise ValueError("worktree_unavailable")
    if (worktree / ".git").exists():
        return capture_repository_snapshot(worktree).worktree_hash
    digest = hashlib.sha256(b"loopguard-worktree-v1\0")
    excluded = {".git", ".venv", "node_modules", "__pycache__"}
    count = 0
    for path in sorted(worktree.rglob("*")):
        if any(part in excluded for part in path.relative_to(worktree).parts):
            continue
        if path.is_symlink():
            digest.update(str(path.relative_to(worktree)).encode())
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            count += 1
            if count > 100_000:
                raise ValueError("worktree_too_large")
            digest.update(str(path.relative_to(worktree)).encode())
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()
