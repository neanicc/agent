from __future__ import annotations

import hashlib
import json
import re
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import CheckSpec


_VERIFICATION_BLOCK = re.compile(
    r"```verification[ \t]*\r?\n(?P<body>.*?)\r?\n```",
    re.DOTALL,
)
_SHELL_CONTROL = re.compile(r"(?:&&|\|\||[;|`]|\$\(|\r|\n)")
_DANGEROUS_SCRIPT = re.compile(
    r"(?:^|\s)(?:curl|wget|sudo|security|docker\s+run\s+--privileged|"
    r"git\s+(?:push|reset|rebase)|(?:npm|pnpm|yarn)\s+publish|twine\s+upload|"
    r"(?:vercel|netlify|flyctl|helm)\b|kubectl\s+(?:apply|delete)|"
    r"terraform\s+(?:apply|destroy)|npx\s+[^ ]*deploy)(?:\s|$)",
    re.IGNORECASE,
)


class DiscoveryError(RuntimeError):
    """Repository verification metadata could not be described safely."""


class DiscoverySource(StrEnum):
    VERIFICATION_CONFIG = "verification_config"
    AGENTS = "agents"
    PYPROJECT = "pyproject"
    PACKAGE_JSON = "package_json"


class RiskLevel(StrEnum):
    LOW = "low"
    REVIEW = "review"
    DENIED = "denied"


class RequestedCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: list[str] = Field(default_factory=list, max_length=256)
    network: bool = False


class DiscoveredCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    spec: CheckSpec
    repository: Path
    source_path: Path
    source_hash: str
    source: DiscoverySource
    requested_capabilities: RequestedCapabilities = Field(
        default_factory=RequestedCapabilities
    )
    risk: RiskLevel = RiskLevel.LOW
    risk_reasons: list[str] = Field(default_factory=list)
    source_command: str | None = None
    dependency_hashes: dict[str, str] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def command(self) -> list[str]:
        return list(self.spec.command)

    @classmethod
    def from_spec(
        cls,
        spec: CheckSpec,
        *,
        repository: Path,
        source_path: Path,
        source_bytes: bytes,
        source: DiscoverySource = DiscoverySource.PYPROJECT,
        requested_capabilities: RequestedCapabilities | None = None,
        source_command: str | None = None,
        dependency_hashes: dict[str, str] | None = None,
    ) -> DiscoveredCheck:
        risk, reasons = _classify_source_command(source_command)
        return cls(
            spec=spec,
            repository=repository.resolve(strict=True),
            source_path=source_path,
            source_hash=hashlib.sha256(source_bytes).hexdigest(),
            source=source,
            requested_capabilities=requested_capabilities or RequestedCapabilities(),
            risk=risk,
            risk_reasons=reasons,
            source_command=source_command,
            dependency_hashes=dependency_hashes or {},
        )


def discover_checks(repository: Path) -> list[DiscoveredCheck]:
    try:
        repo = repository.expanduser().resolve(strict=True)
    except OSError as exc:
        raise DiscoveryError("repository is unavailable") from exc
    if not repo.is_dir():
        raise DiscoveryError("repository must be a directory")

    explicit = repo / ".loopguard" / "verification.toml"
    if explicit.is_file():
        return _from_toml_file(repo, explicit, DiscoverySource.VERIFICATION_CONFIG)

    agents = _nearest_agents(repo)
    if agents is not None:
        try:
            raw = agents.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DiscoveryError("AGENTS.md verification config is unreadable") from exc
        match = _VERIFICATION_BLOCK.search(text)
        if match is not None:
            try:
                payload = tomllib.loads(match.group("body"))
            except tomllib.TOMLDecodeError as exc:
                raise DiscoveryError("AGENTS.md verification config is invalid") from exc
            return _from_check_payload(
                repo,
                agents,
                raw,
                payload,
                DiscoverySource.AGENTS,
            )

    checks: list[DiscoveredCheck] = []
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            raw = pyproject.read_bytes()
            payload = tomllib.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise DiscoveryError("pyproject.toml is invalid") from exc
        tool = payload.get("tool", {})
        if isinstance(tool, dict) and isinstance(tool.get("pytest"), dict):
            checks.append(
                DiscoveredCheck.from_spec(
                    CheckSpec(id="pytest", command=["python", "-m", "pytest", "-q"]),
                    repository=repo,
                    source_path=pyproject,
                    source_bytes=raw,
                    source=DiscoverySource.PYPROJECT,
                )
            )

    package = repo / "package.json"
    if package.is_file():
        checks.extend(_from_package_json(repo, package))
    return checks


def _from_toml_file(
    repo: Path,
    path: Path,
    source: DiscoverySource,
) -> list[DiscoveredCheck]:
    if _contains_symlink(repo, path):
        raise DiscoveryError("verification config symlink escape is not allowed")
    try:
        raw = path.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DiscoveryError("verification config is invalid") from exc
    return _from_check_payload(repo, path, raw, payload, source)


def _contains_symlink(repo: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(repo)
    except ValueError:
        return True
    current = repo
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _from_check_payload(
    repo: Path,
    path: Path,
    raw: bytes,
    payload: dict[str, Any],
    source: DiscoverySource,
) -> list[DiscoveredCheck]:
    values = payload.get("checks")
    if not isinstance(values, list) or not values:
        raise DiscoveryError("verification config requires at least one [[checks]] entry")
    checks: list[DiscoveredCheck] = []
    try:
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("check must be an object")
            environment = value.pop("environment", [])
            network = value.pop("network", False)
            if not isinstance(environment, list) or not all(
                isinstance(name, str) and name.strip() for name in environment
            ):
                raise ValueError("environment must be a list of names")
            spec = CheckSpec.model_validate(value)
            delegated_command, dependency_hashes = _delegated_package_script(repo, spec)
            checks.append(
                DiscoveredCheck.from_spec(
                    spec,
                    repository=repo,
                    source_path=path,
                    source_bytes=raw,
                    source=source,
                    requested_capabilities=RequestedCapabilities(
                        environment=environment,
                        network=network,
                    ),
                    source_command=delegated_command,
                    dependency_hashes=dependency_hashes,
                )
            )
    except (TypeError, ValueError) as exc:
        raise DiscoveryError("verification config contains an invalid check") from exc
    if len({check.id for check in checks}) != len(checks):
        raise DiscoveryError("verification config check IDs must be unique")
    return checks


def _from_package_json(repo: Path, path: Path) -> list[DiscoveredCheck]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError("package.json is invalid") from exc
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return []
    checks: list[DiscoveredCheck] = []
    test_script = scripts.get("test")
    if isinstance(test_script, str) and test_script.strip():
        checks.append(
            DiscoveredCheck.from_spec(
                CheckSpec(id="npm-test", command=["npm", "test", "--", "--runInBand"]),
                repository=repo,
                source_path=path,
                source_bytes=raw,
                source=DiscoverySource.PACKAGE_JSON,
                source_command=test_script,
            )
        )
    typecheck = scripts.get("typecheck")
    if isinstance(typecheck, str) and typecheck.strip():
        command = (
            ["npx", "tsc", "--noEmit"]
            if typecheck.strip() == "tsc --noEmit"
            else ["npm", "run", "typecheck", "--"]
        )
        checks.append(
            DiscoveredCheck.from_spec(
                CheckSpec(id="typecheck", command=command),
                repository=repo,
                source_path=path,
                source_bytes=raw,
                source=DiscoverySource.PACKAGE_JSON,
                source_command=typecheck,
            )
        )
    return checks


def _classify_source_command(command: str | None) -> tuple[RiskLevel, list[str]]:
    if command is None:
        return RiskLevel.LOW, []
    reasons: list[str] = []
    if _SHELL_CONTROL.search(command):
        reasons.append("shell_control_operator")
    if _DANGEROUS_SCRIPT.search(command):
        reasons.append("dangerous_script_command")
    if reasons:
        return RiskLevel.DENIED, reasons
    return RiskLevel.REVIEW, ["package_script_executes_through_package_manager"]


def _delegated_package_script(repo: Path, spec: CheckSpec) -> tuple[str | None, dict[str, str]]:
    command = list(spec.command)
    executable = Path(command[0]).name.lower()
    script_name: str | None = None
    if executable in {"npm", "pnpm"} and len(command) >= 2:
        if command[1] == "test":
            script_name = "test"
        elif command[1] == "run" and len(command) >= 3:
            script_name = command[2]
    elif executable == "yarn" and len(command) >= 2:
        script_name = command[1]
    if script_name is None:
        return None, {}
    package = repo / spec.cwd / "package.json"
    if not package.is_file() or package.is_symlink():
        return None, {}
    try:
        raw = package.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError("delegated package.json is invalid") from exc
    scripts = payload.get("scripts")
    source_command = scripts.get(script_name) if isinstance(scripts, dict) else None
    if not isinstance(source_command, str) or not source_command.strip():
        return None, {str(package.relative_to(repo)): hashlib.sha256(raw).hexdigest()}
    return source_command, {str(package.relative_to(repo)): hashlib.sha256(raw).hexdigest()}


def _nearest_agents(start: Path) -> Path | None:
    boundary = next((path for path in (start, *start.parents) if (path / ".git").exists()), start)
    for directory in (start, *start.parents):
        candidate = directory / "AGENTS.md"
        if candidate.is_file():
            return candidate
        if directory == boundary:
            break
    return None
