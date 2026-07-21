from __future__ import annotations

import re
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .manifest import normalize_repo_path


BrowserPhase = Literal["impacted", "completion", "pr"]
TraceMode = Literal["off", "on-first-retry", "retain-on-failure", "on"]
SelectionMode = Literal["impacted", "full"]
_SHARD = re.compile(r"^(?P<index>[1-9][0-9]*)/(?P<total>[1-9][0-9]*)$")
_PLAYWRIGHT_CONFIG_NAMES = {
    f"playwright.config.{suffix}" for suffix in ("ts", "js", "mts", "mjs", "cts", "cjs")
}
_TEST_SUFFIXES = tuple(
    f".{kind}.{suffix}"
    for kind in ("spec", "test")
    for suffix in ("ts", "js", "mts", "mjs", "cts", "cjs", "tsx", "jsx")
)
_IGNORED_DIRECTORIES = {".git", ".loopguard", "node_modules", "dist", "build"}


class BrowserExecutionPlan(BaseModel):
    """A validated Playwright invocation assembled without a shell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: BrowserPhase
    selection: SelectionMode
    projects: list[str] = Field(min_length=1, max_length=128)
    test_files: list[str] = Field(default_factory=list, max_length=100_000)
    trace: TraceMode
    shard: str | None = None
    argv: list[str] = Field(min_length=1, max_length=100_512)


class BrowserExecutionPolicy:
    """Turns verification phases into conservative Playwright command plans."""

    def __init__(
        self,
        *,
        primary_project: str,
        completion_projects: Sequence[str],
        command: Sequence[str],
    ) -> None:
        self.primary_project = _name(primary_project, "primary project")
        self.completion_projects = _unique_names(
            completion_projects, "completion projects"
        )
        if not self.completion_projects:
            raise ValueError("completion projects must not be empty")
        self.command = _argv(command)

    def plan(
        self,
        *,
        phase: BrowserPhase,
        failures: int,
        test_files: Sequence[str] = (),
        trace: TraceMode | None = None,
        diagnostic: bool = False,
        shard: str | None = None,
    ) -> BrowserExecutionPlan:
        if phase not in {"impacted", "completion", "pr"}:
            raise ValueError("browser phase is invalid")
        if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
            raise ValueError("browser failure count must be non-negative")
        if shard is not None and phase != "pr":
            raise ValueError("browser sharding is only allowed for the PR full gate")
        normalized_shard = _shard(shard) if shard is not None else None
        if isinstance(test_files, (str, bytes)):
            raise ValueError("browser test files must be a path sequence")
        normalized_tests = list(dict.fromkeys(normalize_repo_path(path) for path in test_files))
        if any(path.startswith("-") for path in normalized_tests):
            raise ValueError("browser test paths cannot be command options")

        if phase == "impacted":
            selection: SelectionMode = "impacted"
            projects = [self.primary_project]
            default_trace: TraceMode = "off" if failures == 0 else "on-first-retry"
        elif phase == "completion":
            selection = "impacted"
            projects = list(self.completion_projects)
            default_trace = "retain-on-failure"
        else:
            selection = "full"
            projects = list(self.completion_projects)
            normalized_tests = []
            default_trace = "retain-on-failure"

        selected_trace = trace or default_trace
        if selected_trace not in {"off", "on-first-retry", "retain-on-failure", "on"}:
            raise ValueError("Playwright trace mode is invalid")
        if selected_trace == "on" and not diagnostic:
            raise ValueError("always-on tracing requires explicit diagnostic mode")

        argv = [*self.command, *normalized_tests]
        argv.extend(f"--project={project}" for project in projects)
        argv.append(f"--trace={selected_trace}")
        if normalized_shard is not None:
            argv.append(f"--shard={normalized_shard}")
        return BrowserExecutionPlan(
            phase=phase,
            selection=selection,
            projects=projects,
            test_files=normalized_tests,
            trace=selected_trace,
            shard=normalized_shard,
            argv=argv,
        )


def playwright_adapter_status(repository: str | Path) -> dict[str, object]:
    """Inspect bounded project source and report whether the opt-in adapter is wired."""

    try:
        root = Path(repository).expanduser().resolve(strict=True)
    except OSError:
        return _adapter_status("repository_unavailable")
    if not root.is_dir():
        return _adapter_status("repository_unavailable")
    try:
        configs = sorted(path for path in root.iterdir() if path.name in _PLAYWRIGHT_CONFIG_NAMES)
    except OSError:
        return _adapter_status("repository_unavailable")
    if not configs:
        return _adapter_status("not_applicable")
    config_imported = any(_imports_adapter(path) for path in configs)
    fixture_imports = 0
    scanned = 0
    for directory, child_directories, names in os.walk(root, followlinks=False):
        child_directories[:] = sorted(
            name
            for name in child_directories
            if name not in _IGNORED_DIRECTORIES
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(names):
            if scanned >= 20_000:
                break
            if not name.endswith(_TEST_SUFFIXES):
                continue
            scanned += 1
            fixture_imports += int(_imports_adapter(Path(directory) / name))
        if scanned >= 20_000:
            break
    enabled = config_imported and fixture_imports > 0
    return {
        "status": "enabled" if enabled else "not_enabled",
        "config_found": True,
        "config_adapter_imported": config_imported,
        "fixture_imports": fixture_imports,
        "enabled": enabled,
    }


def _name(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or "\x00" in normalized
        or ("project" in label and re.fullmatch(r"[A-Za-z0-9._:-]+", normalized) is None)
    ):
        raise ValueError(f"{label} must be bounded and non-empty")
    return normalized


def _unique_names(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized = tuple(_name(value, label) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique")
    return normalized


def _argv(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(_name(value, "browser command argument") for value in values)
    if not normalized:
        raise ValueError("browser command must be a non-empty argv array")
    return normalized


def _shard(value: str) -> str:
    match = _SHARD.fullmatch(value)
    if match is None:
        raise ValueError("browser shard must use index/total syntax")
    index = int(match.group("index"))
    total = int(match.group("total"))
    if index > total or total > 1_024:
        raise ValueError("browser shard is outside the supported range")
    return f"{index}/{total}"


def _imports_adapter(path: Path) -> bool:
    try:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            return False
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return bool(
        re.search(
            r"(?:from\s*|import\s*\(\s*)['\"]@loopguard/playwright(?:/config)?['\"]",
            source,
        )
    )


def _adapter_status(status: str) -> dict[str, object]:
    return {
        "status": status,
        "config_found": False,
        "config_adapter_imported": False,
        "fixture_imports": 0,
        "enabled": False,
    }
