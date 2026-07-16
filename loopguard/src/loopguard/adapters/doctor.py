from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Sequence


class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    CONDITIONAL = "conditional"
    EXPERIMENTAL = "experimental"
    UNAVAILABLE = "unavailable"


CAPABILITY_NAMES = (
    "observe",
    "block_tool",
    "request_approval",
    "start_managed",
    "inject",
    "interrupt",
    "select_model",
    "select_effort",
)


@dataclass(frozen=True, slots=True)
class SurfaceReport:
    id: str
    vendor: str
    surface: str
    installed_version: str | None
    integration_source: str
    plugin_version: str | None
    plugin_checksum: str | None
    hook_locations: tuple[str, ...]
    trust: str
    coverage: tuple[str, ...]
    capabilities: dict[str, str]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IntegrationReport:
    schema_version: int
    daemon_reachable: bool
    bridge_health: dict[str, str]
    surfaces: tuple[SurfaceReport, ...]

    def surface(self, surface_id: str) -> SurfaceReport:
        for item in self.surfaces:
            if item.id == surface_id:
                return item
        raise KeyError(surface_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "daemon_reachable": self.daemon_reachable,
            "bridge_health": dict(self.bridge_health),
            "surfaces": [surface.to_dict() for surface in self.surfaces],
        }


@dataclass(frozen=True, slots=True)
class InstallationEvidence:
    source: str = "not_configured"
    version: str | None = None
    checksum: str | None = None
    hook_locations: tuple[str, ...] = ()
    trust: str = "unknown"
    verified: bool = False
    events: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> InstallationEvidence:
        if value is None:
            return cls()
        locations = _strings(value.get("hook_locations"))
        events = _strings(value.get("events"))
        return cls(
            source=_text(value.get("source"), "not_configured"),
            version=_optional_text(value.get("version")),
            checksum=_optional_text(value.get("checksum")),
            hook_locations=locations,
            trust=_text(value.get("trust"), "unknown"),
            verified=value.get("verified") is True,
            events=events,
        )


def build_report(
    *,
    codex_version: str | None,
    claude_version: str | None,
    installations: Mapping[str, Mapping[str, object]] | None = None,
    daemon_reachable: bool = False,
    bridge_health: Mapping[str, str] | None = None,
    cloud_observation_verified: Mapping[str, bool] | None = None,
) -> IntegrationReport:
    """Build an evidence-bound compatibility report without probing the network.

    Cloud control capabilities are deliberately unavailable: repository-local hooks are not
    evidence that a hosted execution surface runs them.
    """
    evidence = installations or {}
    bridges = {"codex": "not_checked", "claude": "not_checked"}
    bridges.update(bridge_health or {})
    cloud_smokes = cloud_observation_verified or {}
    surfaces: list[SurfaceReport] = []
    for vendor, installed_version in (("codex", codex_version), ("claude", claude_version)):
        installation = InstallationEvidence.from_mapping(evidence.get(vendor))
        attached_ready = installation.verified and daemon_reachable
        attached_configured = installation.source not in {"not_configured", "probe_failed"}
        tool_covered = any(
            event in installation.events for event in ("PreToolUse", "PermissionRequest")
        )
        attached = _unavailable_capabilities()
        attached["observe"] = (
            CapabilityState.SUPPORTED.value
            if attached_ready
            else CapabilityState.CONDITIONAL.value
            if attached_configured
            else CapabilityState.UNAVAILABLE.value
        )
        attached["block_tool"] = (
            CapabilityState.SUPPORTED.value
            if attached_ready and tool_covered
            else CapabilityState.CONDITIONAL.value
            if attached_configured and tool_covered
            else CapabilityState.UNAVAILABLE.value
        )
        approval_covered = "PermissionRequest" in installation.events
        attached["request_approval"] = (
            CapabilityState.SUPPORTED.value
            if attached_ready and approval_covered
            else CapabilityState.CONDITIONAL.value
            if attached_configured and approval_covered
            else CapabilityState.UNAVAILABLE.value
        )
        surfaces.append(
            SurfaceReport(
                id=f"{vendor}-attached-local",
                vendor=vendor,
                surface="attached_local",
                installed_version=installed_version,
                integration_source=installation.source,
                plugin_version=installation.version,
                plugin_checksum=installation.checksum,
                hook_locations=installation.hook_locations,
                trust=installation.trust,
                coverage=installation.events,
                capabilities=attached,
                notes=("Capabilities are limited to the exact verified event coverage.",),
            )
        )

        bridge_ready = bridges[vendor] == "healthy" and daemon_reachable
        managed = _unavailable_capabilities()
        if installed_version is not None:
            managed.update(
                {
                    name: (
                        CapabilityState.SUPPORTED.value
                        if bridge_ready
                        else CapabilityState.CONDITIONAL.value
                    )
                    for name in CAPABILITY_NAMES
                }
            )
        surfaces.append(
            SurfaceReport(
                id=f"{vendor}-managed-local",
                vendor=vendor,
                surface="managed_local",
                installed_version=installed_version,
                integration_source="managed_bridge",
                plugin_version=None,
                plugin_checksum=None,
                hook_locations=(),
                trust="not_applicable",
                coverage=(),
                capabilities=managed,
                notes=("Managed capabilities require a healthy local bridge and daemon.",),
            )
        )

        cloud = _unavailable_capabilities()
        cloud["observe"] = (
            CapabilityState.SUPPORTED.value
            if cloud_smokes.get(vendor) is True
            else CapabilityState.CONDITIONAL.value
        )
        surfaces.append(
            SurfaceReport(
                id=f"{vendor}-cloud",
                vendor=vendor,
                surface="cloud",
                installed_version=installed_version,
                integration_source=installation.source,
                plugin_version=installation.version,
                plugin_checksum=installation.checksum,
                hook_locations=installation.hook_locations,
                trust=installation.trust,
                coverage=installation.events,
                capabilities=cloud,
                notes=(
                    "Cloud observation is conditional until a real signed delivery smoke passes.",
                    "LoopGuard does not claim cloud model or effort control.",
                ),
            )
        )
    return IntegrationReport(1, daemon_reachable, bridges, tuple(surfaces))


def probe_report(
    *,
    daemon_reachable: bool,
    cwd: Path | None = None,
    home: Path | None = None,
) -> IntegrationReport:
    """Collect bounded, local-only evidence from installed CLIs and integration files."""
    working_directory = (cwd or Path.cwd()).resolve(strict=False)
    versions = {
        "codex": _installed_version("codex"),
        "claude": _installed_version("claude"),
    }
    installations = {
        vendor: _probe_installation(vendor, working_directory, version, home)
        for vendor, version in versions.items()
    }
    return build_report(
        codex_version=versions["codex"],
        claude_version=versions["claude"],
        installations=installations,
        daemon_reachable=daemon_reachable,
        bridge_health={
            vendor: "not_checked" if version is not None else "unavailable"
            for vendor, version in versions.items()
        },
    )


def diagnostic_bundle_preview() -> dict[str, object]:
    return {
        "files": ["doctor-report.json", "redaction-manifest.json"],
        "included": ["capability states", "version strings", "health and outcome codes"],
        "excluded": [
            "credentials",
            "prompts",
            "repository names",
            "session identifiers",
            "absolute hook paths",
        ],
        "confirmation_required": True,
    }


def build_diagnostic_payload(
    core_report: Mapping[str, object],
    integrations: IntegrationReport,
) -> dict[str, object]:
    """Allowlist support-safe core fields before archive serialization."""
    errors = core_report.get("errors")
    safe_errors = []
    if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
        for error in errors:
            if isinstance(error, Mapping):
                safe_errors.append(
                    {
                        "code": error.get("code"),
                        "retryable": error.get("retryable"),
                    }
                )
    return {
        "schema_version": 1,
        "healthy": core_report.get("healthy"),
        "daemon": core_report.get("daemon"),
        "protocol": core_report.get("protocol"),
        "store": core_report.get("store"),
        "state_permissions": core_report.get("state_permissions"),
        "integration": core_report.get("integration"),
        "errors": safe_errors,
        "safe_fixes": core_report.get("safe_fixes", 0),
        "agent_integrations": integrations.to_dict(),
    }


def write_diagnostic_bundle(
    path: Path,
    report: IntegrationReport | Mapping[str, object],
    *,
    confirmed: bool,
) -> Path:
    if not confirmed:
        raise ValueError("LGD-BUNDLE-CONFIRMATION: review the redaction preview and confirm")
    destination = path.expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = report.to_dict() if isinstance(report, IntegrationReport) else dict(report)
    sanitized = _redact_bundle_value(raw)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".loopguard-diagnostics-",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "doctor-report.json",
                json.dumps(sanitized, indent=2, sort_keys=True) + "\n",
            )
            archive.writestr(
                "redaction-manifest.json",
                json.dumps(diagnostic_bundle_preview(), indent=2, sort_keys=True) + "\n",
            )
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _redact_bundle_value(value: object, *, key: str = "") -> object:
    normalized = key.lower()
    if any(token in normalized for token in ("path", "location", "session_id", "repo_id")):
        if isinstance(value, list):
            return ["<redacted-local-value>"] * len(value)
        if value is not None:
            return "<redacted-local-value>"
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_bundle_value(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_bundle_value(child, key=key) for child in value]
    if isinstance(value, tuple):
        return [_redact_bundle_value(child, key=key) for child in value]
    return value


def _unavailable_capabilities() -> dict[str, str]:
    return {name: CapabilityState.UNAVAILABLE.value for name in CAPABILITY_NAMES}


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _text(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _installed_version(executable: str) -> str | None:
    resolved = shutil.which(executable)
    if resolved is None:
        return None
    try:
        result = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    version = (result.stdout or result.stderr).strip()
    return version or None


def _probe_installation(
    vendor: str,
    cwd: Path,
    version: str | None,
    home: Path | None,
) -> dict[str, object]:
    if version is None:
        return {}
    try:
        if vendor == "codex":
            return _probe_codex(cwd, home)
        return _probe_claude(cwd, home)
    except Exception:  # noqa: BLE001 - diagnostics must remain available when a vendor probe fails
        return {"source": "probe_failed", "trust": "unknown", "verified": False}


def _probe_codex(cwd: Path, home: Path | None) -> dict[str, object]:
    from .codex_hooks import CODEX_EVENTS, PLUGIN_VERSION, verify_codex_hooks, verify_codex_plugin

    plugin_probe_failed = False
    try:
        plugin = verify_codex_plugin(cwd=cwd, hook_report=[])
    except Exception:  # noqa: BLE001 - fallback inspection remains useful
        plugin = None
        plugin_probe_failed = True
    if plugin is not None and plugin.installed:
        try:
            plugin = verify_codex_plugin(cwd=cwd)
        except Exception:  # noqa: BLE001 - installed bytes are known; runtime trust is not
            plugin_probe_failed = True
        hook_locations = []
        if home is not None:
            hook_locations.append(
                str(home / "integrations/codex-marketplace/plugins/codex-plugin/hooks/hooks.json")
            )
        return {
            "source": "plugin",
            "version": PLUGIN_VERSION,
            "checksum": plugin.hook_checksum,
            "hook_locations": hook_locations,
            "trust": (
                "probe_failed"
                if plugin_probe_failed
                else "trusted"
                if plugin.healthy
                else plugin.status
            ),
            "verified": plugin.healthy and not plugin_probe_failed,
            "events": [event for event in CODEX_EVENTS if event not in plugin.missing_events],
        }
    candidates = (
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json",
        cwd / ".codex" / "hooks.json",
    )
    for path in candidates:
        fallback = verify_codex_hooks(path)
        if fallback.installed:
            return {
                "source": "fallback",
                "checksum": fallback.hook_checksum,
                "hook_locations": [str(path)],
                "trust": fallback.status,
                "verified": fallback.healthy,
                "events": [event for event in CODEX_EVENTS if event not in fallback.missing_events],
            }
    return {"source": "probe_failed"} if plugin_probe_failed else {}


def _probe_claude(cwd: Path, home: Path | None) -> dict[str, object]:
    from .claude_hooks import (
        CLAUDE_EVENTS,
        PLUGIN_VERSION,
        verify_claude_hooks,
        verify_claude_plugin,
    )

    plugin = verify_claude_plugin()
    if plugin.installed:
        hook_locations = []
        if home is not None:
            hook_locations.append(
                str(home / "integrations/claude-marketplace/plugins/loopguard/hooks/hooks.json")
            )
        return {
            "source": "plugin",
            "version": PLUGIN_VERSION,
            "checksum": plugin.checksum,
            "hook_locations": hook_locations,
            "trust": "trusted" if plugin.healthy else plugin.status,
            "verified": plugin.healthy,
            "events": list(CLAUDE_EVENTS) if plugin.healthy else [],
        }
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    candidates = (config / "settings.json", cwd / ".claude" / "settings.json")
    for scope, path in (("user", candidates[0]), ("project", candidates[1])):
        fallback = verify_claude_hooks(path, scope=scope, repository=cwd)
        if fallback.installed:
            return {
                "source": "fallback",
                "checksum": fallback.checksum,
                "hook_locations": [str(path)],
                "trust": fallback.status,
                "verified": fallback.healthy,
                "events": list(CLAUDE_EVENTS) if fallback.healthy else [],
            }
    return {}
