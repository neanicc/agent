from __future__ import annotations

import csv
import contextlib
import os
import plistlib
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Sequence
from xml.sax.saxutils import escape


MAC_LABEL = "com.loopguard.daemon"
LINUX_UNIT = "loopguardd.service"
WINDOWS_TASK = r"\LoopGuard\Daemon"
_LINUX_MARKER = "# Managed by LoopGuard service installer."
_MAC_MARKER = "<string>com.loopguard.daemon</string>"
_WINDOWS_MARKER = r"<URI>\LoopGuard\Daemon</URI>"
MAX_SERVICE_DEFINITION_BYTES = 1_048_576


class ServiceInstallError(RuntimeError):
    """A user service could not be changed without violating its ownership contract."""


@dataclass(frozen=True, slots=True)
class RenderedService:
    platform: str
    body: str
    automatic_startup: str


@dataclass(frozen=True, slots=True)
class ServiceResult:
    changed: bool
    status: str
    platform: str
    destination: Path
    automatic_startup: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["destination"] = str(self.destination)
        return payload


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    installed: bool
    running: bool
    status: str
    platform: str
    destination: Path
    automatic_startup: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["destination"] = str(self.destination)
        return payload


CommandRunner = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class _ExistingDefinition:
    body: str
    permissions_safe: bool


def render_user_service(
    *,
    platform: str,
    executable: str | Path,
    home: str | Path,
    user_sid: str | None = None,
) -> RenderedService:
    active = _normalize_platform(platform)
    executable_text = os.fspath(executable)
    home_text = os.fspath(home)
    _validate_value(executable_text, "executable")
    _validate_value(home_text, "home")
    if not _is_absolute(executable_text, active):
        raise ServiceInstallError("service executable must be absolute")
    if not Path(home_text).expanduser().is_absolute() and active != "win32":
        raise ServiceInstallError("service home must be absolute")

    if active == "darwin":
        log_path = str(Path(home_text).expanduser() / "daemon.log")
        body = _template("com.loopguard.daemon.plist")
        body = body.replace("{{EXECUTABLE}}", escape(executable_text))
        body = body.replace("{{HOME}}", escape(str(Path(home_text).expanduser())))
        body = body.replace("{{LOG_PATH}}", escape(log_path))
        _validate_rendered(active, body)
        return RenderedService(active, body, "supported")
    if active == "linux":
        arguments = (
            _systemd_quote(executable_text),
            "daemon",
            "start",
            "--foreground",
            "--home",
            _systemd_quote(str(Path(home_text).expanduser())),
        )
        body = _template("loopguardd.service")
        body = body.replace("{{EXEC_START}}", " ".join(arguments))
        body = body.replace(
            "{{STATE_HOME}}",
            _systemd_quote(str(Path(home_text).expanduser())),
        )
        _validate_rendered(active, body)
        return RenderedService(active, body, "supported")

    sid = user_sid or "CURRENT_USER"
    _validate_sid(sid)
    arguments = subprocess.list2cmdline(["daemon", "start", "--foreground", "--home", home_text])
    body = _template("loopguardd-windows.xml")
    body = body.replace("{{USER_SID}}", escape(sid))
    body = body.replace("{{EXECUTABLE}}", escape(executable_text))
    body = body.replace("{{ARGUMENTS}}", escape(arguments))
    _validate_rendered(active, body)
    return RenderedService(active, body, "experimental")


def install_user_service(
    *,
    executable: Path,
    home: Path,
    platform: str | None = None,
    destination: Path | None = None,
    command_runner: CommandRunner = subprocess.run,
    user_sid: str | None = None,
    dry_run: bool = False,
) -> ServiceResult:
    active = _normalize_platform(platform or sys.platform)
    sid = user_sid
    if active == "win32" and sid is None:
        sid = _current_user_sid(command_runner)
    rendered = render_user_service(
        platform=active,
        executable=executable,
        home=home.expanduser().absolute(),
        user_sid=sid,
    )
    target = (destination or default_service_path(active, home)).expanduser().absolute()
    _validate_existing_anchor(target.parent)
    existing = _existing_definition(target, active)
    changed = existing is None or existing.body != rendered.body or not existing.permissions_safe
    if dry_run:
        return ServiceResult(changed, "preview", active, target, rendered.automatic_startup)
    if not changed:
        return ServiceResult(False, "unchanged", active, target, rendered.automatic_startup)

    existed = existing is not None
    validator: Callable[[Path], None] | None = None
    if active == "darwin":

        def validate_plist(path: Path) -> None:
            _run(command_runner, ["plutil", "-lint", str(path)])

        validator = validate_plist
    _write_owned_definition(target, rendered.body, active, validator=validator)
    try:
        _activate_changed_service(
            active,
            target,
            existed=existed,
            command_runner=command_runner,
        )
        runtime = service_status(
            home=home,
            platform=active,
            destination=target,
            command_runner=command_runner,
        )
        if active != "win32" and not runtime.running:
            raise ServiceInstallError("installed user service did not become active")
    except Exception:
        _rollback_activation(
            active,
            target,
            previous=existing.body if existing is not None else None,
            validator=validator,
            command_runner=command_runner,
        )
        raise
    status = "installed_experimental" if active == "win32" else "active"
    return ServiceResult(True, status, active, target, rendered.automatic_startup)


def service_status(
    *,
    home: Path,
    platform: str | None = None,
    destination: Path | None = None,
    command_runner: CommandRunner = subprocess.run,
) -> ServiceStatus:
    active = _normalize_platform(platform or sys.platform)
    target = (destination or default_service_path(active, home)).expanduser().absolute()
    _validate_parent_if_present(target.parent)
    definition = _existing_definition(target, active)
    automatic = "experimental" if active == "win32" else "supported"
    if definition is None:
        return ServiceStatus(False, False, "not_installed", active, target, automatic)
    if not definition.permissions_safe:
        return ServiceStatus(True, False, "unsafe_permissions", active, target, automatic)
    command = _status_command(active)
    result = _run(command_runner, command, allow_failure=True)
    running = result.returncode == 0
    if active == "win32":
        running = running and "running" in str(result.stdout).lower()
        status = "running" if running else "installed_experimental"
    else:
        status = "running" if running else "stopped"
    return ServiceStatus(True, running, status, active, target, automatic)


def uninstall_user_service(
    *,
    home: Path,
    platform: str | None = None,
    destination: Path | None = None,
    command_runner: CommandRunner = subprocess.run,
    dry_run: bool = False,
) -> ServiceResult:
    active = _normalize_platform(platform or sys.platform)
    target = (destination or default_service_path(active, home)).expanduser().absolute()
    _validate_parent_if_present(target.parent)
    existing = _existing_definition(target, active)
    automatic = "experimental" if active == "win32" else "supported"
    if existing is None:
        return ServiceResult(False, "not_installed", active, target, automatic)
    if dry_run:
        return ServiceResult(True, "preview", active, target, automatic)
    if not existing.permissions_safe:
        raise ServiceInstallError("service definition permissions are not owner-only")
    if active == "darwin":
        _bootout_macos(command_runner)
    elif active == "linux":
        _run(command_runner, ["systemctl", "--user", "disable", "--now", LINUX_UNIT])
    else:
        _run(command_runner, ["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"])
    target.unlink()
    if active == "linux":
        _run(command_runner, ["systemctl", "--user", "daemon-reload"])
    return ServiceResult(True, "removed", active, target, automatic)


def start_user_service(
    *,
    home: Path,
    platform: str | None = None,
    command_runner: CommandRunner = subprocess.run,
) -> None:
    active = _normalize_platform(platform or sys.platform)
    if active == "darwin":
        command = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{MAC_LABEL}"]
    elif active == "linux":
        command = ["systemctl", "--user", "start", LINUX_UNIT]
    else:
        command = ["schtasks", "/Run", "/TN", WINDOWS_TASK]
    _run(command_runner, command)


def safe_fix_user_service(
    *,
    home: Path,
    platform: str | None = None,
    destination: Path | None = None,
    command_runner: CommandRunner = subprocess.run,
) -> tuple[str, ...]:
    """Repair only a non-writable owned definition and restart its stopped service."""
    active = _normalize_platform(platform or sys.platform)
    target = (destination or default_service_path(active, home)).expanduser().absolute()
    _validate_parent_if_present(target.parent)
    definition = _existing_definition(target, active)
    if definition is None:
        return ()
    fixes: list[str] = []
    if not definition.permissions_safe:
        if os.name == "posix":
            os.chmod(target, 0o600)
        fixes.append("service_permissions")
    status = service_status(
        home=home,
        platform=active,
        destination=target,
        command_runner=command_runner,
    )
    if not status.running:
        start_user_service(home=home, platform=active, command_runner=command_runner)
        fixes.append("service_restarted")
    return tuple(fixes)


def verify_synthetic_event(
    *,
    home: Path,
    client: Any | None = None,
    timeout_seconds: float = 5.0,
) -> None:
    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("synthetic-event timeout must be between zero and 30 seconds")
    from loopguard.control.events import ControlEvent, EventKind, SessionRef

    from .hook_client import HookClient, HookClientError

    event = ControlEvent(
        event_id=f"setup-{secrets.token_hex(16)}",
        kind=EventKind.SESSION_STARTED,
        source="loopguard-setup",
        session=SessionRef(
            host_id="setup-local",
            repo_id="setup-synthetic",
            session_id="setup-synthetic",
        ),
        payload={"synthetic": True},
        created_at=datetime.now(timezone.utc),
    )
    active_client = client or HookClient(home=home, timeout_seconds=0.5)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            decision = active_client.send(event)
            break
        except HookClientError as exc:
            if not exc.retryable or time.monotonic() >= deadline:
                raise ServiceInstallError(f"synthetic event failed: {exc.code}") from exc
            time.sleep(0.05)
    if decision.target.target_id != event.session.session_id:
        raise ServiceInstallError("synthetic event returned a mismatched policy target")


def default_service_path(platform: str, home: Path) -> Path:
    active = _normalize_platform(platform)
    if active == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.loopguard.daemon.plist"
    if active == "linux":
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return config / "systemd" / "user" / LINUX_UNIT
    return home.expanduser().absolute() / "service" / "loopguardd-windows.xml"


def _activate_changed_service(
    platform: str,
    destination: Path,
    *,
    existed: bool,
    command_runner: CommandRunner,
) -> None:
    if platform == "darwin":
        domain = f"gui/{os.getuid()}"
        if existed:
            _bootout_macos(command_runner)
        _run(command_runner, ["launchctl", "bootstrap", domain, str(destination)])
        _run(command_runner, ["launchctl", "kickstart", "-k", f"{domain}/{MAC_LABEL}"])
        return
    if platform == "linux":
        _run(command_runner, ["systemctl", "--user", "daemon-reload"])
        if existed:
            _run(command_runner, ["systemctl", "--user", "enable", LINUX_UNIT])
            _run(command_runner, ["systemctl", "--user", "restart", LINUX_UNIT])
        else:
            _run(command_runner, ["systemctl", "--user", "enable", "--now", LINUX_UNIT])
        return
    _run(
        command_runner,
        ["schtasks", "/Create", "/TN", WINDOWS_TASK, "/XML", str(destination), "/F"],
    )


def _status_command(platform: str) -> list[str]:
    if platform == "darwin":
        return ["launchctl", "print", f"gui/{os.getuid()}/{MAC_LABEL}"]
    if platform == "linux":
        return ["systemctl", "--user", "is-active", LINUX_UNIT]
    return ["schtasks", "/Query", "/TN", WINDOWS_TASK, "/FO", "CSV", "/NH"]


def _existing_definition(path: Path, platform: str) -> _ExistingDefinition | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ServiceInstallError("service definition must be a real regular file")
    if os.name == "posix" and metadata.st_uid != os.getuid():
        raise ServiceInstallError("service definition is owned by another user")
    if metadata.st_size > MAX_SERVICE_DEFINITION_BYTES:
        raise ServiceInstallError("service definition exceeds the bounded size limit")
    try:
        body = path.read_text()
    except (OSError, UnicodeError) as exc:
        raise ServiceInstallError("service definition could not be read") from exc
    if _marker(platform) not in body:
        raise ServiceInstallError("existing service definition is not LoopGuard-owned")
    permissions_safe = os.name != "posix" or stat.S_IMODE(metadata.st_mode) == 0o600
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ServiceInstallError("service definition is writable by another user")
    return _ExistingDefinition(body, permissions_safe)


def _write_owned_definition(
    destination: Path,
    body: str,
    platform: str,
    *,
    validator: Callable[[Path], None] | None,
) -> None:
    _ensure_safe_parent(destination.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
        if validator is not None:
            validator(temporary)
        if _marker(platform) not in temporary.read_text():
            raise ServiceInstallError("rendered service definition lost its ownership marker")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _rollback_activation(
    platform: str,
    destination: Path,
    *,
    previous: str | None,
    validator: Callable[[Path], None] | None,
    command_runner: CommandRunner,
) -> None:
    with contextlib.suppress(Exception):
        if previous is not None:
            _write_owned_definition(destination, previous, platform, validator=validator)
            _activate_changed_service(
                platform,
                destination,
                existed=platform == "linux",
                command_runner=command_runner,
            )
            return
        destination.unlink(missing_ok=True)
        if platform == "darwin":
            _run(
                command_runner,
                ["launchctl", "bootout", f"gui/{os.getuid()}/{MAC_LABEL}"],
                allow_failure=True,
            )
        elif platform == "linux":
            _run(
                command_runner,
                ["systemctl", "--user", "disable", "--now", LINUX_UNIT],
                allow_failure=True,
            )
            _run(command_runner, ["systemctl", "--user", "daemon-reload"], allow_failure=True)
        else:
            _run(
                command_runner,
                ["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"],
                allow_failure=True,
            )


def _validate_parent(path: Path) -> None:
    current = path
    while True:
        metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode):
            raise ServiceInstallError("service definition parent may not contain symlinks")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ServiceInstallError("service definition parent must be a directory")
        if os.name == "posix":
            if metadata.st_uid != os.getuid():
                return
            if stat.S_IMODE(metadata.st_mode) & 0o022:
                raise ServiceInstallError("service definition parent is not owner-controlled")
        if current == current.parent:
            return
        current = current.parent


def _validate_parent_if_present(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    _validate_parent(path)


def _ensure_safe_parent(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while True:
        try:
            os.lstat(current)
            break
        except FileNotFoundError:
            missing.append(current)
            if current == current.parent:
                raise ServiceInstallError("service definition parent has no existing anchor")
            current = current.parent
    _validate_parent(current)
    for candidate in reversed(missing):
        try:
            os.mkdir(candidate, 0o700)
        except FileExistsError:
            pass
        _validate_parent(candidate)


def _validate_existing_anchor(path: Path) -> None:
    current = path
    while True:
        try:
            os.lstat(current)
            _validate_parent(current)
            return
        except FileNotFoundError:
            if current == current.parent:
                raise ServiceInstallError("service definition parent has no existing anchor")
            current = current.parent


def _run(
    command_runner: CommandRunner,
    command: Sequence[str],
    *,
    allow_failure: bool = False,
) -> Any:
    try:
        result = command_runner(
            list(command),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceInstallError(f"service command unavailable: {' '.join(command[:2])}") from exc
    if not allow_failure and result.returncode != 0:
        raise ServiceInstallError(f"service command failed: {' '.join(command[:2])}")
    return result


def _bootout_macos(command_runner: CommandRunner) -> None:
    command = ["launchctl", "bootout", f"gui/{os.getuid()}/{MAC_LABEL}"]
    result = _run(command_runner, command, allow_failure=True)
    if result.returncode == 0:
        return
    message = f"{result.stdout}\n{result.stderr}".lower()
    if not any(token in message for token in ("not found", "could not find", "no such process")):
        raise ServiceInstallError("service command failed: launchctl bootout")


def _current_user_sid(command_runner: CommandRunner) -> str:
    result = _run(command_runner, ["whoami", "/user", "/fo", "csv", "/nh"])
    try:
        row = next(csv.reader([result.stdout.strip()]))
    except (csv.Error, StopIteration) as exc:
        raise ServiceInstallError("could not parse the current Windows user SID") from exc
    sid = next((value for value in reversed(row) if value.startswith("S-1-")), "")
    _validate_sid(sid)
    return sid


def _validate_sid(sid: str) -> None:
    if sid == "CURRENT_USER":
        return
    parts = sid.split("-")
    if len(parts) < 4 or parts[0] != "S" or not all(part.isdigit() for part in parts[1:]):
        raise ServiceInstallError("invalid current-user SID")


def _template(name: str) -> str:
    return files("loopguard.adapters").joinpath("templates", name).read_text(encoding="utf-8")


def _validate_rendered(platform: str, body: str) -> None:
    if "{{" in body or _marker(platform) not in body:
        raise ServiceInstallError("service template is malformed or incomplete")
    try:
        if platform == "darwin":
            parsed = plistlib.loads(body.encode())
            if parsed.get("Label") != MAC_LABEL or parsed.get("RunAtLoad") is not True:
                raise ServiceInstallError("macOS service template has invalid policy")
        elif platform == "win32":
            ET.fromstring(body)
        elif "ExecStart=" not in body or "Restart=on-failure" not in body:
            raise ServiceInstallError("Linux service template has invalid policy")
    except (ET.ParseError, plistlib.InvalidFileException, ValueError) as exc:
        raise ServiceInstallError("service template is malformed") from exc


def _marker(platform: str) -> str:
    return {
        "darwin": _MAC_MARKER,
        "linux": _LINUX_MARKER,
        "win32": _WINDOWS_MARKER,
    }[platform]


def _normalize_platform(value: str) -> str:
    lowered = value.lower()
    if lowered in {"darwin", "macos"}:
        return "darwin"
    if lowered.startswith("linux"):
        return "linux"
    if lowered in {"win32", "windows", "nt"}:
        return "win32"
    raise ServiceInstallError(f"unsupported user-service platform: {value}")


def _validate_value(value: str, label: str) -> None:
    if not value or any(character in value for character in ("\x00", "\r", "\n")):
        raise ServiceInstallError(f"service {label} contains an invalid control character")


def _is_absolute(value: str, platform: str) -> bool:
    if platform == "win32":
        return PureWindowsPath(value).is_absolute()
    return PurePosixPath(value).is_absolute()


def _systemd_quote(value: str) -> str:
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
