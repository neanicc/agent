from __future__ import annotations

import os
import json
import shutil
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Mapping, Protocol, Sequence


SETUP_STEPS = (
    "install_detected",
    "daemon_extra",
    "service",
    "integration",
    "daemon",
    "synthetic_event",
)
SUPPORTED_AGENTS = frozenset({"codex", "claude"})
MAX_DX_METRICS_BYTES = 1_048_576


class SetupError(RuntimeError):
    """A setup request violated a stable safety or determinism contract."""


class SetupActions(Protocol):
    def execute(self, step: str, *, dry_run: bool) -> str: ...


@dataclass(frozen=True, slots=True)
class SetupRequest:
    agents: tuple[str, ...]
    scope: str | None
    dry_run: bool = False
    non_interactive: bool = False
    resume_from: str | None = None

    def __post_init__(self) -> None:
        if self.scope is not None and self.scope not in {"user", "project"}:
            raise SetupError("LGD-SETUP-SCOPE: scope must be user or project")
        if not self.agents:
            raise SetupError("LGD-SETUP-AGENT: at least one agent is required")
        if self.non_interactive and (self.scope is None or "auto" in self.agents):
            raise SetupError("LGD-SETUP-NONINTERACTIVE: explicit --scope and --agent are required")
        unknown = set(self.agents) - SUPPORTED_AGENTS - {"auto"}
        if unknown:
            raise SetupError(f"LGD-SETUP-AGENT: unsupported agents: {','.join(sorted(unknown))}")
        if "auto" in self.agents and len(self.agents) != 1:
            raise SetupError("LGD-SETUP-AGENT: auto cannot be combined with explicit agents")
        if self.resume_from is not None and self.resume_from not in SETUP_STEPS:
            raise SetupError("LGD-SETUP-RESUME: unknown setup step")


@dataclass(frozen=True, slots=True)
class SetupStepResult:
    name: str
    status: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupResult:
    status: str
    outcome_code: str
    agents: tuple[str, ...]
    scope: str
    changed: bool
    trust_status: str
    steps: tuple[SetupStepResult, ...]
    resume_command: str | None = None
    error_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UninstallResult:
    status: str
    changed: bool
    data_retained: bool
    agents: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_setup(
    request: SetupRequest,
    *,
    actions: SetupActions,
    metric_recorder: Callable[[str, float, str], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SetupResult:
    """Run a resumable setup sequence while keeping vendor trust human-owned."""
    agents = request.agents
    scope = request.scope or "user"
    first = SETUP_STEPS.index(request.resume_from) if request.resume_from else 0
    results: list[SetupStepResult] = []
    changed = False
    for step in SETUP_STEPS[first:]:
        started = clock()
        try:
            status = actions.execute(step, dry_run=request.dry_run)
        except Exception as exc:  # noqa: BLE001 - normalized into a stable setup outcome
            _record_metric_safely(
                metric_recorder,
                step,
                max(0.0, clock() - started),
                "failed",
            )
            return SetupResult(
                status="failed",
                outcome_code="LGD-SETUP-STEP-FAILED",
                agents=agents,
                scope=scope,
                changed=changed,
                trust_status="human_review_required",
                steps=tuple(
                    results
                    + [SetupStepResult(step, "failed", _step_details_or_empty(actions, step))]
                ),
                resume_command=_resume_command(agents, scope, step),
                error_type=type(exc).__name__,
            )
        if status not in {"changed", "unchanged", "preview", "supported"}:
            raise SetupError(f"LGD-SETUP-ACTION: invalid status for {step}")
        _record_metric_safely(metric_recorder, step, max(0.0, clock() - started), status)
        changed = changed or status == "changed"
        results.append(SetupStepResult(step, status, _step_details_or_empty(actions, step)))
    return SetupResult(
        status="preview" if request.dry_run else "attention_required",
        outcome_code="LGD-SETUP-TRUST-REQUIRED",
        agents=agents,
        scope=scope,
        changed=changed,
        trust_status="human_review_required",
        steps=tuple(results),
    )


def uninstall_owned_integrations(
    *,
    agents: Sequence[str],
    removers: Mapping[str, object],
    dry_run: bool,
) -> UninstallResult:
    changed = False
    normalized = tuple(dict.fromkeys(agents))
    for agent in normalized:
        remover = removers.get(agent)
        if not callable(remover):
            raise SetupError(f"LGD-UNINSTALL-AGENT: no remover for {agent}")
        status = remover(dry_run)
        if status not in {"removed", "not_installed", "preview"}:
            raise SetupError(f"LGD-UNINSTALL-RESULT: invalid status for {agent}")
        changed = changed or status in {"removed", "preview"}
    return UninstallResult(
        status="preview" if dry_run else "removed" if changed else "not_installed",
        changed=changed,
        data_retained=True,
        agents=normalized,
    )


def apply_safe_fixes(home: Path, candidates: Sequence[Path]) -> tuple[Path, ...]:
    """Repair permissions only for real, owner-controlled paths under LoopGuard home."""
    root = home.expanduser().absolute()
    fixed: list[Path] = []
    for candidate in dict.fromkeys((root, *candidates)):
        path = candidate.expanduser().absolute()
        if path != root and root not in path.parents:
            continue
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            continue
        if os.name == "posix" and metadata.st_uid != os.getuid():
            continue
        expected = 0o700 if stat.S_ISDIR(metadata.st_mode) else 0o600
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            continue
        changed = os.name == "posix" and stat.S_IMODE(metadata.st_mode) != expected
        if changed:
            os.chmod(path, expected)
            fixed.append(path)
    return tuple(fixed)


def purge_data(home: Path, *, confirmed: bool) -> bool:
    if not confirmed:
        raise SetupError("LGD-DATA-CONFIRMATION: explicit confirmation is required")
    root = home.expanduser().absolute()
    if root in {Path(root.anchor), Path.home().absolute()}:
        raise SetupError("LGD-DATA-PATH: refusing to purge a filesystem or user home root")
    try:
        metadata = os.lstat(root)
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SetupError("LGD-DATA-PATH: LoopGuard home must be a real directory")
    if os.name == "posix" and metadata.st_uid != os.getuid():
        raise SetupError("LGD-DATA-PATH: LoopGuard home is owned by another user")
    shutil.rmtree(root)
    return True


def record_dx_metric(
    home: Path,
    *,
    event: str,
    duration_seconds: float,
    outcome_code: str,
    timestamp: int | None = None,
) -> None:
    if event not in {*SETUP_STEPS, "quickstart", "protected_session"}:
        raise SetupError("LGD-DX-EVENT: unsupported metric event")
    if outcome_code not in {"changed", "unchanged", "preview", "supported", "failed", "ok"}:
        raise SetupError("LGD-DX-OUTCOME: unsupported metric outcome")
    if duration_seconds < 0 or duration_seconds > 31_536_000:
        raise SetupError("LGD-DX-DURATION: invalid metric duration")
    root = home.expanduser().absolute()
    try:
        metadata = os.lstat(root)
    except FileNotFoundError:
        root.mkdir(mode=0o700, parents=True)
        metadata = os.lstat(root)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SetupError("LGD-DX-PATH: LoopGuard home must be a real directory")
    if os.name == "posix":
        if metadata.st_uid != os.getuid():
            raise SetupError("LGD-DX-PATH: LoopGuard home is owned by another user")
        os.chmod(root, 0o700)
    path = root / "dx-metrics.jsonl"
    payload = {
        "schema_version": 1,
        "event": event,
        "duration_ms": round(duration_seconds * 1_000),
        "outcome_code": outcome_code,
        "timestamp": timestamp if timestamp is not None else int(time.time()),
    }
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, (json.dumps(payload, separators=(",", ":")) + "\n").encode())
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def build_dx_report(home: Path) -> dict[str, object]:
    path = home.expanduser().absolute() / "dx-metrics.jsonl"
    rows = _read_dx_rows(path)
    return {
        "schema_version": 1,
        "scope": "local",
        "upload_enabled": False,
        "time_to_quickstart": _metric_summary(rows, "quickstart"),
        "time_to_protected_session": _metric_summary(rows, "protected_session"),
        "setup_steps": {
            event: _metric_summary(rows, event)
            for event in SETUP_STEPS
            if any(row.get("event") == event for row in rows)
        },
    }


def record_protected_session_metric(home: Path, *, now: int | None = None) -> bool:
    """Record the first protected-session milestone after the latest setup, without identifiers."""
    path = home.expanduser().absolute() / "dx-metrics.jsonl"
    rows = _read_dx_rows(path)
    if not rows:
        return False
    setup_times = [
        int(row["timestamp"])
        for row in rows
        if row.get("event") == "install_detected" and isinstance(row.get("timestamp"), int)
    ]
    if not setup_times:
        return False
    latest_setup = setup_times[-1]
    if any(
        row.get("event") == "protected_session"
        and isinstance(row.get("timestamp"), int)
        and int(row["timestamp"]) >= latest_setup
        for row in rows
    ):
        return False
    observed_at = int(time.time()) if now is None else now
    record_dx_metric(
        home,
        event="protected_session",
        duration_seconds=max(0, observed_at - latest_setup),
        outcome_code="ok",
        timestamp=observed_at,
    )
    return True


def _resume_command(agents: Sequence[str], scope: str, step: str) -> str:
    return f"loopguard setup --agent {','.join(agents)} --scope {scope} --resume-from {step}"


def _metric_summary(rows: Sequence[Mapping[str, object]], event: str) -> dict[str, object]:
    matching = [row for row in rows if row.get("event") == event]
    durations = [
        int(row["duration_ms"]) for row in matching if isinstance(row.get("duration_ms"), int)
    ]
    if not durations:
        return {"samples": 0, "median_ms": None, "last_ms": None, "last_outcome": None}
    return {
        "samples": len(durations),
        "median_ms": round(median(durations)),
        "last_ms": durations[-1],
        "last_outcome": matching[-1].get("outcome_code"),
    }


def _step_details(actions: SetupActions, step: str) -> tuple[str, ...]:
    describe = getattr(actions, "describe", None)
    if not callable(describe):
        return ()
    details = describe(step)
    if not isinstance(details, tuple) or not all(isinstance(item, str) for item in details):
        raise SetupError(f"LGD-SETUP-ACTION: invalid details for {step}")
    return details


def _step_details_or_empty(actions: SetupActions, step: str) -> tuple[str, ...]:
    try:
        return _step_details(actions, step)
    except Exception:  # noqa: BLE001 - never mask the original resumable setup failure
        return ()


def _record_metric_safely(
    recorder: Callable[[str, float, str], None] | None,
    event: str,
    duration: float,
    outcome: str,
) -> None:
    if recorder is None:
        return
    try:
        recorder(event, duration, outcome)
    except Exception:  # noqa: BLE001 - local measurement must never block product setup
        return


def _read_dx_rows(path: Path) -> list[dict[str, object]]:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SetupError("LGD-DX-PATH: metrics must be a real regular file")
    if metadata.st_size > MAX_DX_METRICS_BYTES:
        raise SetupError("LGD-DX-SIZE: local metrics exceed the bounded report size")
    if os.name == "posix" and (
        metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SetupError("LGD-DX-PATH: local metrics must be owner-only")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        raw = os.read(descriptor, MAX_DX_METRICS_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_DX_METRICS_BYTES:
        raise SetupError("LGD-DX-SIZE: local metrics exceed the bounded report size")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise SetupError("LGD-DX-FORMAT: local metrics are not UTF-8") from exc
    rows: list[dict[str, object]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema_version") == 1:
            rows.append(row)
    return rows
