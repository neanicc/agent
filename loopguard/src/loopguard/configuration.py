from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import LoopGuardConfig
from .control.redaction import redact
from .control.paths import ControlPaths


class ConfigurationError(Exception):
    """A configuration source could not be parsed or validated safely."""


class DaemonSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_frame_bytes: int = Field(default=1_048_576, gt=0, le=16_777_216)
    max_concurrent_clients: int = Field(default=32, gt=0, le=1_024)
    idle_timeout_seconds: float = Field(default=30.0, gt=0, le=86_400)
    write_timeout_seconds: float = Field(default=5.0, gt=0, le=300)
    handler_queue_size: int = Field(default=128, gt=0, le=65_536)
    handler_max_attempts: int = Field(default=3, gt=0, le=100)
    handler_retry_delay_seconds: float = Field(default=0.05, ge=0, le=300)


class TelemetrySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class ControlConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    daemon: DaemonSettings = Field(default_factory=DaemonSettings)
    guard: LoopGuardConfig = Field(default_factory=LoopGuardConfig)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)


@dataclass(frozen=True, slots=True)
class LoadedConfiguration:
    configuration: ControlConfiguration
    sources: dict[str, str]
    path: Path

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "config": redact(self.configuration.model_dump(mode="json")),
            "sources": dict(sorted(self.sources.items())),
            "precedence": ["defaults", "file", "environment", "cli"],
            "path": str(self.path),
        }


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("expected a boolean")


_ENVIRONMENT = {
    "LOOPGUARD_MAX_FRAME_BYTES": ("daemon.max_frame_bytes", int),
    "LOOPGUARD_MAX_CONCURRENT_CLIENTS": ("daemon.max_concurrent_clients", int),
    "LOOPGUARD_IDLE_TIMEOUT_SECONDS": ("daemon.idle_timeout_seconds", float),
    "LOOPGUARD_WRITE_TIMEOUT_SECONDS": ("daemon.write_timeout_seconds", float),
    "LOOPGUARD_HANDLER_QUEUE_SIZE": ("daemon.handler_queue_size", int),
    "LOOPGUARD_HANDLER_MAX_ATTEMPTS": ("daemon.handler_max_attempts", int),
    "LOOPGUARD_HANDLER_RETRY_DELAY_SECONDS": (
        "daemon.handler_retry_delay_seconds",
        float,
    ),
    "LOOPGUARD_TRIP_COUNT": ("guard.trip_count", int),
    "LOOPGUARD_ACTION": ("guard.action", str),
    "LOOPGUARD_TELEMETRY_ENABLED": ("telemetry.enabled", _parse_bool),
}


def load_configuration(
    *,
    path: str | Path | None = None,
    home: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    environment: dict[str, str] | None = None,
) -> LoadedConfiguration:
    resolved_path = Path(path) if path is not None else ControlPaths.from_home(home).config
    defaults = ControlConfiguration().model_dump(mode="python")
    sources = {key: "defaults" for key in _flatten(defaults)}
    merged = defaults

    if resolved_path.exists():
        try:
            file_values = json.loads(resolved_path.read_text())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConfigurationError("configuration file is not valid UTF-8 JSON") from exc
        if not isinstance(file_values, dict):
            raise ConfigurationError("configuration root must be a JSON object")
        merged = _merge_dicts(merged, file_values)
        for key in _flatten(file_values):
            sources[key] = "file"

    active_environment = environment if environment is not None else os.environ
    for variable, (key, converter) in _ENVIRONMENT.items():
        if variable not in active_environment:
            continue
        try:
            converted = converter(active_environment[variable])
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"environment variable {variable} is invalid") from exc
        _set_dotted(merged, key, converted)
        sources[key] = "environment"

    for key, value in (cli_overrides or {}).items():
        _set_dotted(merged, key, value)
        sources[key] = "cli"
    try:
        configuration = ControlConfiguration.model_validate(merged)
    except ValidationError as exc:
        raise ConfigurationError("configuration values failed validation") from exc
    return LoadedConfiguration(configuration=configuration, sources=sources, path=resolved_path)


def _merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, child in value.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            flattened.update(_flatten(child, dotted))
        else:
            flattened[dotted] = child
    return flattened


def _set_dotted(target: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cursor = target
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            raise ConfigurationError(f"configuration path {key} conflicts with a scalar")
        cursor = child
    cursor[parts[-1]] = value
