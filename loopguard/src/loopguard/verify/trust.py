from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .discovery import DiscoveredCheck, RiskLevel


_SECRET_MARKERS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "SSH_",
    "SIGNING_",
)
_SHELLS = {"bash", "sh", "zsh", "fish", "cmd", "powershell", "pwsh"}
_CREDENTIAL_TOOLS = {"security", "keychain", "pass", "op"}
_DEPLOY_TOOLS = {"vercel", "netlify", "flyctl", "helm"}


class TrustState(StrEnum):
    UNTRUSTED = "untrusted"
    APPROVED = "approved"
    INVALIDATED = "invalidated"
    REVOKED = "revoked"
    DENIED = "denied"


class ApprovalCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    network: bool = False
    environment: list[str] = Field(default_factory=list, max_length=256)
    secrets: list[str] = Field(default_factory=list, max_length=256)


class CommandPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executable: str
    arguments: list[str]
    cwd: str
    environment: list[str]
    network: bool
    secrets: list[str]
    timeout_seconds: int
    source: str
    source_hash: str
    source_command: str | None = None


class CommandTrustRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str
    command_hash: str
    config_hash: str
    actor: str
    capabilities: ApprovalCapabilities
    approved_at: AwareDatetime
    revoked_at: AwareDatetime | None = None


class TrustDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    state: TrustState
    reasons: list[str]
    preview: CommandPreview


class CommandTrustService:
    def evaluate(
        self,
        repository: Path,
        check: DiscoveredCheck,
        *,
        record: CommandTrustRecord | None = None,
    ) -> TrustDecision:
        repo = _canonical_repository(repository)
        preview = _preview(repo, check)
        denied = _categorical_reasons(repo, check)
        if denied:
            return TrustDecision(
                allowed=False,
                state=TrustState.DENIED,
                reasons=denied,
                preview=preview,
            )
        if record is None:
            return TrustDecision(
                allowed=False,
                state=TrustState.UNTRUSTED,
                reasons=["approval_required"],
                preview=preview,
            )
        if record.revoked_at is not None:
            return TrustDecision(
                allowed=False,
                state=TrustState.REVOKED,
                reasons=["approval_revoked"],
                preview=preview,
            )
        expected_repo = _repository_id(repo)
        expected_command = _command_hash(check)
        if (
            record.repository_id != expected_repo
            or record.command_hash != expected_command
            or record.config_hash != check.source_hash
            or not _capabilities_cover(check, record.capabilities)
        ):
            return TrustDecision(
                allowed=False,
                state=TrustState.INVALIDATED,
                reasons=["repository_command_or_capability_hash_changed"],
                preview=preview,
            )
        return TrustDecision(
            allowed=True,
            state=TrustState.APPROVED,
            reasons=[],
            preview=preview,
        )

    def approve(
        self,
        repository: Path,
        check: DiscoveredCheck,
        *,
        actor: str,
        capabilities: ApprovalCapabilities | None = None,
    ) -> CommandTrustRecord:
        repo = _canonical_repository(repository)
        if _categorical_reasons(repo, check):
            raise ValueError("categorically denied commands require a separate organization policy")
        granted = capabilities or ApprovalCapabilities()
        if not _capabilities_cover(check, granted):
            raise ValueError("approval does not cover all requested capabilities")
        if not actor.strip():
            raise ValueError("approval actor must not be empty")
        return CommandTrustRecord(
            repository_id=_repository_id(repo),
            command_hash=_command_hash(check),
            config_hash=check.source_hash,
            actor=actor.strip(),
            capabilities=granted,
            approved_at=datetime.now(timezone.utc),
        )

    def revoke(self, record: CommandTrustRecord) -> CommandTrustRecord:
        return record.model_copy(update={"revoked_at": datetime.now(timezone.utc)})


def _canonical_repository(repository: Path) -> Path:
    try:
        repo = repository.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("repository is unavailable") from exc
    if not repo.is_dir():
        raise ValueError("repository must be a directory")
    return repo


def _repository_id(repository: Path) -> str:
    git_marker = repository / ".git"
    identity_path = _git_common_directory(git_marker) if git_marker.exists() else repository
    return hashlib.sha256(f"loopguard-repository-v1\0{identity_path}".encode()).hexdigest()


def _git_common_directory(marker: Path) -> Path:
    if marker.is_dir():
        git_directory = marker.resolve(strict=True)
    elif marker.is_file():
        try:
            prefix, separator, raw_path = marker.read_text().strip().partition(":")
        except OSError as exc:
            raise ValueError("Git worktree metadata is unreadable") from exc
        if prefix != "gitdir" or not separator or not raw_path.strip():
            raise ValueError("Git worktree metadata is invalid")
        candidate = Path(raw_path.strip())
        git_directory = (
            candidate if candidate.is_absolute() else marker.parent / candidate
        ).resolve(strict=True)
    else:
        raise ValueError("Git metadata type is unsupported")
    common_marker = git_directory / "commondir"
    if not common_marker.is_file():
        return git_directory
    try:
        common = Path(common_marker.read_text().strip())
    except OSError as exc:
        raise ValueError("Git common-directory metadata is unreadable") from exc
    if not str(common):
        raise ValueError("Git common-directory metadata is invalid")
    return (common if common.is_absolute() else git_directory / common).resolve(strict=True)


def _command_hash(check: DiscoveredCheck) -> str:
    payload = {
        "spec": check.spec.model_dump(mode="json"),
        "requested_capabilities": check.requested_capabilities.model_dump(mode="json"),
        "source_hash": check.source_hash,
        "source_command": check.source_command,
        "dependency_hashes": check.dependency_hashes,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _preview(repository: Path, check: DiscoveredCheck) -> CommandPreview:
    requested = list(check.requested_capabilities.environment)
    return CommandPreview(
        executable=check.spec.command[0],
        arguments=list(check.spec.command[1:]),
        cwd=str(repository / check.spec.cwd),
        environment=requested,
        network=check.requested_capabilities.network,
        secrets=[name for name in requested if _is_secret_name(name)],
        timeout_seconds=check.spec.timeout_seconds,
        source=str(check.source_path),
        source_hash=check.source_hash,
        source_command=check.source_command,
    )


def _categorical_reasons(repository: Path, check: DiscoveredCheck) -> list[str]:
    reasons = list(check.risk_reasons) if check.risk is RiskLevel.DENIED else []
    command = list(check.spec.command)
    executable = Path(command[0]).name.lower()
    arguments = [argument.lower() for argument in command[1:]]
    try:
        cwd = (repository / check.spec.cwd).resolve(strict=True)
        cwd.relative_to(repository)
    except (OSError, ValueError):
        reasons.append("cwd_escape")
    if executable in _SHELLS:
        reasons.append("interactive_shell")
    if executable in _CREDENTIAL_TOOLS or (
        executable in {"gh", "aws", "gcloud"}
        and arguments
        and arguments[0] in {"auth", "configure"}
    ):
        reasons.append("credential_store_access")
    if executable in _DEPLOY_TOOLS or (
        executable in {"npm", "pnpm", "yarn", "twine"}
        and any(argument in {"publish", "upload"} for argument in arguments)
    ):
        reasons.append("publish_or_deploy")
    if executable == "git" and arguments and arguments[0] in {
        "push",
        "reset",
        "rebase",
        "commit",
        "tag",
    }:
        reasons.append("git_history_mutation")
    if executable in {"docker", "podman"} and any(
        argument == "--privileged" or argument.startswith("--pid=host")
        for argument in arguments
    ):
        reasons.append("privileged_container")
    return list(dict.fromkeys(reasons))


def _capabilities_cover(
    check: DiscoveredCheck,
    capabilities: ApprovalCapabilities,
) -> bool:
    requested = set(check.requested_capabilities.environment)
    if check.requested_capabilities.network and not capabilities.network:
        return False
    if not requested.issubset(capabilities.environment):
        return False
    requested_secrets = {name for name in requested if _is_secret_name(name)}
    return requested_secrets.issubset(capabilities.secrets)


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)
