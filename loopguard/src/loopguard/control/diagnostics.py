from __future__ import annotations

import os
import socket
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from .errors import ErrorEnvelope, error_for
from .migrations import CURRENT_SCHEMA_VERSION
from .paths import ControlPaths
from .protocol import PROTOCOL_VERSION


def build_doctor_report(
    paths: ControlPaths,
    *,
    platform_name: str | None = None,
    repository: str | Path | None = None,
) -> dict[str, object]:
    from loopguard.browser.execution import playwright_adapter_status

    active_platform = platform_name or os.name
    errors: list[ErrorEnvelope] = []
    state_permissions = _state_permissions(paths, errors)
    daemon_status = _daemon_status(paths, active_platform)
    if daemon_status == "capability_unavailable":
        errors.append(error_for("LGD-CAP-005", details={"capability": "local_transport"}))
    elif daemon_status != "reachable":
        errors.append(error_for("LGD-DAEMON-001", details={"socket": str(paths.socket)}))
    store = _store_status(paths, errors)
    integration = _integration_status(paths, errors)

    report: dict[str, object] = {
        "schema_version": 1,
        "healthy": not errors,
        "daemon": daemon_status,
        "protocol": {
            "version": PROTOCOL_VERSION,
            "peer_identity": "enforced" if active_platform == "posix" else "unavailable",
        },
        "store": store,
        "state_permissions": state_permissions,
        "integration": integration,
        "browser_acceleration": playwright_adapter_status(repository or Path.cwd()),
        "errors": [error.to_dict() for error in errors],
    }
    return report


def _state_permissions(paths: ControlPaths, errors: list[ErrorEnvelope]) -> str:
    if not paths.home.exists():
        return "missing"
    candidates = ((paths.home, 0o700, "directory"), (paths.socket, 0o600, "socket"))
    unsafe: list[str] = []
    for path, expected_mode, kind in candidates:
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            continue
        valid_kind = {
            "directory": stat.S_ISDIR,
            "socket": stat.S_ISSOCK,
        }[kind](status.st_mode)
        if not valid_kind or stat.S_ISLNK(status.st_mode):
            unsafe.append(str(path))
            continue
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != expected_mode
        ):
            unsafe.append(str(path))
    for path in (
        paths.events_db,
        paths.events_db.with_name(f"{paths.events_db.name}-wal"),
        paths.events_db.with_name(f"{paths.events_db.name}-shm"),
        paths.pid,
    ):
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(status.st_mode)
            or stat.S_ISLNK(status.st_mode)
            or (
                os.name == "posix"
                and (status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600)
            )
        ):
            unsafe.append(str(path))
    if unsafe:
        errors.append(error_for("LGD-STATE-002", details={"paths": unsafe}))
        return "unsafe"
    return "owner_only"


def _daemon_status(paths: ControlPaths, platform_name: str) -> str:
    if platform_name != "posix":
        return "capability_unavailable"
    try:
        status = os.lstat(paths.socket)
    except FileNotFoundError:
        return "unreachable"
    if (
        not stat.S_ISSOCK(status.st_mode)
        or status.st_uid != os.getuid()
        or stat.S_IMODE(status.st_mode) != 0o600
    ):
        return "unreachable"
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.2)
    try:
        client.connect(str(paths.socket))
    except OSError:
        return "unreachable"
    finally:
        client.close()
    return "reachable"


def _store_status(paths: ControlPaths, errors: list[ErrorEnvelope]) -> dict[str, object]:
    if not paths.events_db.exists():
        return {
            "status": "missing",
            "schema_version": None,
            "supported_schema_version": CURRENT_SCHEMA_VERSION,
            "encryption_key": "not_checked",
            "quick_check": "not_run",
            "dispatch_lag": 0,
            "handler_failures": 0,
            "positions": {"local": 0, "repo": 0, "session": 0},
        }
    connection: sqlite3.Connection | None = None
    try:
        uri = f"file:{quote(str(paths.events_db), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        quick_rows = connection.execute("PRAGMA quick_check").fetchall()
        quick_status = "ok" if quick_rows == [("ok",)] else "failed"
        if version != CURRENT_SCHEMA_VERSION:
            errors.append(
                error_for(
                    "LGD-SCHEMA-003",
                    details={"found": version, "supported": CURRENT_SCHEMA_VERSION},
                )
            )
            return {
                "status": "migration_needed",
                "schema_version": version,
                "supported_schema_version": CURRENT_SCHEMA_VERSION,
                "encryption_key": "not_checked",
                "quick_check": quick_status,
                "dispatch_lag": None,
                "handler_failures": None,
                "positions": {"local": None, "repo": None, "session": None},
            }
        if quick_status != "ok":
            errors.append(error_for("LGD-STORE-008"))
        positions = connection.execute(
            """
            SELECT COALESCE(MAX(local_log_seq), 0), COALESCE(MAX(repo_seq), 0),
                   COALESCE(MAX(session_seq), 0)
            FROM events
            """
        ).fetchone()
        lag = int(
            connection.execute(
                "SELECT COUNT(*) FROM handler_dispatch WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
        )
        failures = int(
            connection.execute(
                "SELECT COUNT(*) FROM handler_dispatch WHERE status = 'failed'"
            ).fetchone()[0]
        )
        key_row = connection.execute(
            "SELECT key_id FROM store_metadata WHERE singleton = 1"
        ).fetchone()
        key_status = _key_status(str(key_row[0])) if key_row is not None else "missing"
        if key_status == "dependency_unavailable":
            errors.append(error_for("LGD-DEPS-006", details={"dependency": "control"}))
        elif key_status != "available":
            errors.append(error_for("LGD-STORE-008", details={"encryption_key": key_status}))
        return {
            "status": "ok" if quick_status == "ok" else "integrity_failed",
            "schema_version": version,
            "supported_schema_version": CURRENT_SCHEMA_VERSION,
            "encryption_key": key_status,
            "quick_check": quick_status,
            "dispatch_lag": lag,
            "handler_failures": failures,
            "positions": {
                "local": int(positions[0]),
                "repo": int(positions[1]),
                "session": int(positions[2]),
            },
        }
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
        errors.append(error_for("LGD-STORE-008"))
        return {
            "status": "integrity_failed",
            "schema_version": None,
            "supported_schema_version": CURRENT_SCHEMA_VERSION,
            "encryption_key": "not_checked",
            "quick_check": "failed",
            "dispatch_lag": None,
            "handler_failures": None,
            "positions": {"local": None, "repo": None, "session": None},
        }
    finally:
        if connection is not None:
            connection.close()


def _key_status(key_id: str) -> str:
    try:
        from .crypto import KeyStoreError, PlatformKeyStore

        PlatformKeyStore().get(key_id)
    except ImportError:
        return "dependency_unavailable"
    except KeyStoreError:
        return "unavailable"
    return "available"


def _integration_status(paths: ControlPaths, errors: list[ErrorEnvelope]) -> str:
    if not paths.integration_trust.exists():
        return "not_configured"
    try:
        marker = paths.integration_trust.read_text().strip()
    except OSError:
        marker = ""
    if marker != "trusted":
        errors.append(error_for("LGD-TRUST-004"))
        return "trust_pending"
    return "trusted"
