"""Repair-specific worktree, runtime, and artifact containment contracts."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from loopguard.heal.models import RepairModel

_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_ENVIRONMENT = {"LANG", "LC_ALL", "PYTHONHASHSEED", "TZ"}
_SHELLS = {"bash", "cmd", "dash", "fish", "powershell", "pwsh", "sh", "zsh"}


class ArtifactPolicyViolation(ValueError):
    pass


class SandboxRuntimeError(RuntimeError):
    pass


class SandboxLimits(RepairModel):
    nano_cpus: int = Field(default=1_000_000_000, ge=100_000_000, le=4_000_000_000)
    memory_bytes: int = Field(default=512 * 1024 * 1024, ge=64 * 1024 * 1024, le=8 * 1024**3)
    pids: int = Field(default=64, ge=8, le=512)
    timeout_seconds: int = Field(default=300, ge=1, le=3_600)
    output_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    artifact_bytes: int = Field(default=64 * 1024 * 1024, ge=1_024, le=512 * 1024 * 1024)
    artifact_files: int = Field(default=128, ge=1, le=2_048)
    tmpfs_bytes: int = Field(default=64 * 1024 * 1024, ge=1_048_576, le=512 * 1024 * 1024)


class SandboxSpec(RepairModel):
    repair_id: str = Field(min_length=1, max_length=128)
    repository_id: str = Field(min_length=1, max_length=256)
    repository_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    image: str = Field(pattern=r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
    command: tuple[str, ...] = Field(min_length=1, max_length=64)
    source_path: Path
    fixture_path: Path
    artifact_path: Path
    environment: dict[str, str] = Field(default_factory=dict, max_length=16)
    limits: SandboxLimits = Field(default_factory=SandboxLimits)

    @field_validator("repair_id")
    @classmethod
    def valid_repair_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("repair identity is invalid")
        return value

    def validate_policy(self) -> Self:
        if not _SHA.fullmatch(self.repository_sha) or not _IMAGE.fullmatch(self.image):
            raise ValueError("sandbox immutable identity is invalid")
        if not self.repository_id or len(self.repository_id) > 256 or "\x00" in self.repository_id:
            raise ValueError("sandbox repository identity is invalid")
        executable = Path(self.command[0]).name.lower()
        if executable in _SHELLS:
            raise ValueError("shell command execution is forbidden")
        if any(
            not value or len(value) > 4_096 or "\x00" in value or "\n" in value or "\r" in value
            for value in self.command
        ):
            raise ValueError("sandbox command is invalid")
        if not set(self.environment) <= _SAFE_ENVIRONMENT or any(
            len(value) > 1_024 or "\x00" in value for value in self.environment.values()
        ):
            raise ValueError("sandbox environment contains a forbidden value")

        resolved: list[Path] = []
        for label, candidate in (
            ("source", self.source_path),
            ("fixture", self.fixture_path),
            ("artifact", self.artifact_path),
        ):
            if candidate.is_symlink():
                raise ValueError(f"sandbox {label} path cannot be a symlink")
            try:
                path = candidate.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"sandbox {label} path is unavailable") from exc
            if not path.is_dir():
                raise ValueError(f"sandbox {label} path must be a directory")
            if any(part.is_symlink() for part in _parents_until_root(candidate.absolute())):
                raise ValueError(f"sandbox {label} path cannot contain a symlink")
            resolved.append(path)
        for index, path in enumerate(resolved):
            for other in resolved[index + 1 :]:
                if path == other or path.is_relative_to(other) or other.is_relative_to(path):
                    raise ValueError("sandbox mount paths must not overlap")
        return self


class HostedIsolationEvidence(RepairModel):
    runtime_class: Literal["gvisor", "kata", "firecracker"]
    image: str = Field(pattern=r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
    image_signature_verified: bool
    provenance_verified: bool
    rootless: bool
    read_only_root: bool
    network_disabled: bool
    seccomp_enforced: bool
    apparmor_enforced: bool
    capabilities_dropped: bool
    privilege_escalation_disabled: bool
    host_namespaces_disabled: bool
    host_paths_disabled: bool
    host_socket_disabled: bool
    service_account_token_disabled: bool
    cloud_metadata_disabled: bool
    deadline_enforced: bool
    nano_cpus: int = Field(gt=0)
    memory_bytes: int = Field(gt=0)
    pids: int = Field(gt=0)
    ephemeral_storage_bytes: int = Field(gt=0)

    def validate_for(self, spec: SandboxSpec) -> Self:
        controls = (
            self.image_signature_verified,
            self.provenance_verified,
            self.rootless,
            self.read_only_root,
            self.network_disabled,
            self.seccomp_enforced,
            self.apparmor_enforced,
            self.capabilities_dropped,
            self.privilege_escalation_disabled,
            self.host_namespaces_disabled,
            self.host_paths_disabled,
            self.host_socket_disabled,
            self.service_account_token_disabled,
            self.cloud_metadata_disabled,
            self.deadline_enforced,
        )
        if self.image != spec.image or not all(controls):
            raise ValueError("hosted isolation evidence is incomplete")
        if (
            self.nano_cpus > spec.limits.nano_cpus
            or self.memory_bytes > spec.limits.memory_bytes
            or self.pids > spec.limits.pids
            or self.ephemeral_storage_bytes > spec.limits.artifact_bytes
        ):
            raise ValueError("hosted isolation exceeds the repair resource policy")
        return self


class SandboxExecution(RepairModel):
    exit_code: int = Field(ge=-1, le=255)
    stdout: bytes = Field(max_length=16_777_216)
    stderr: bytes = Field(max_length=16_777_216)
    timed_out: bool = False
    observed_source: Literal["airflow", "openlineage", "github_actions", "webhook"] | None = None
    observed_payload: dict[str, Any] | None = None
    artifact_paths: tuple[str, ...] = Field(default=(), max_length=2_048)
    assurance: Literal["test", "local_docker", "hosted_isolated"]
    hosted_evidence: HostedIsolationEvidence | None = None

    @model_validator(mode="after")
    def hosted_execution_has_evidence(self) -> Self:
        if self.assurance == "hosted_isolated" and self.hosted_evidence is None:
            raise ValueError("hosted execution requires isolation evidence")
        return self


class WorktreeMaterialization(RepairModel):
    path: Path
    revision: str = Field(pattern=r"^[0-9a-f]{40,64}$")


class WorktreeCheckout:
    """Materialize one detached exact-revision worktree under an owned private root."""

    def __init__(
        self,
        repository: Path,
        *,
        revision: str,
        root: Path,
        repair_id: str,
    ) -> None:
        if not _SHA.fullmatch(revision) or not _IDENTIFIER.fullmatch(repair_id):
            raise ValueError("worktree identity is invalid")
        self.repository = repository.expanduser().resolve(strict=True)
        if self.repository.is_symlink() or not (self.repository / ".git").exists():
            raise ValueError("repair repository is not a Git worktree")
        self.root = root.expanduser().absolute()
        if self.root.is_symlink():
            raise ValueError("repair worktree root cannot be a symlink")
        root_existed = self.root.exists()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("repair worktree root cannot contain a symlink")
        if os.name == "posix":
            details = self.root.stat()
            if details.st_uid != os.getuid():
                raise ValueError("repair worktree root must be user-owned")
            if root_existed and stat.S_IMODE(details.st_mode) != 0o700:
                raise ValueError("existing repair worktree root must use mode 0700")
            self.root.chmod(0o700)
        if self.root.is_relative_to(self.repository) or self.repository.is_relative_to(self.root):
            raise ValueError("repair worktree root and repository must not overlap")
        self.path = self.root / repair_id
        if self.path.exists() or self.path.is_symlink():
            raise ValueError("repair worktree destination already exists")
        self.revision = revision
        self._active = False

    def __enter__(self) -> WorktreeMaterialization:
        resolved = _git(
            self.repository,
            ("rev-parse", "--verify", f"{self.revision}^{{commit}}"),
        ).strip()
        if resolved != self.revision:
            raise ValueError("repair revision must be a full immutable commit")
        _git(
            self.repository,
            ("worktree", "add", "--detach", str(self.path), self.revision),
        )
        observed = _git(self.path, ("rev-parse", "HEAD")).strip()
        if observed != self.revision:
            self._remove()
            raise SandboxRuntimeError("materialized repair revision does not match")
        self._active = True
        return WorktreeMaterialization(path=self.path, revision=observed)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc, traceback
        self._remove()
        return False

    def _remove(self) -> None:
        if self.path.exists() or self._active:
            _git(self.repository, ("worktree", "remove", "--force", str(self.path)))
            _git(self.repository, ("worktree", "prune"))
        self._active = False


def validate_artifacts(
    root: Path,
    *,
    maximum_bytes: int,
    maximum_files: int,
) -> tuple[str, ...]:
    if maximum_bytes < 1 or maximum_files < 1:
        raise ValueError("artifact limits must be positive")
    if root.is_symlink():
        raise ArtifactPolicyViolation("artifact root cannot be a symlink")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ArtifactPolicyViolation("artifact root must be a directory")

    total = 0
    expanded_total = 0
    paths: list[str] = []
    for directory, directories, files in os.walk(resolved, followlinks=False):
        current = Path(directory)
        for name in [*directories, *files]:
            candidate = current / name
            details = candidate.lstat()
            if stat.S_ISLNK(details.st_mode):
                raise ArtifactPolicyViolation("artifact symlink is forbidden")
            if not (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode)):
                raise ArtifactPolicyViolation("artifact device or special file is forbidden")
        for name in files:
            candidate = current / name
            relative = candidate.relative_to(resolved).as_posix()
            paths.append(relative)
            if len(paths) > maximum_files:
                raise ArtifactPolicyViolation("artifact file count exceeds policy")
            if zipfile.is_zipfile(candidate):
                expanded_total += _validate_zip(candidate, maximum_bytes, maximum_files)
            elif tarfile.is_tarfile(candidate):
                expanded_total += _validate_tar(candidate, maximum_bytes, maximum_files)
            if expanded_total > maximum_bytes:
                raise ArtifactPolicyViolation("archive size exceeds policy")
            total += candidate.stat().st_size
            if total > maximum_bytes:
                raise ArtifactPolicyViolation("artifact byte limit exceeds policy")
    return tuple(sorted(paths))


def _validate_zip(path: Path, maximum_bytes: int, maximum_files: int) -> int:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > maximum_files:
            raise ArtifactPolicyViolation("archive file count exceeds policy")
        total = 0
        for entry in entries:
            _archive_path(entry.filename)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ArtifactPolicyViolation("archive symlink is forbidden")
            total += entry.file_size
            if total > maximum_bytes:
                raise ArtifactPolicyViolation("archive size exceeds policy")
            if entry.file_size > 1_048_576 and entry.compress_size * 1_000 < entry.file_size:
                raise ArtifactPolicyViolation("archive compression ratio exceeds policy")
        return total


def _validate_tar(path: Path, maximum_bytes: int, maximum_files: int) -> int:
    with tarfile.open(path) as archive:
        total = 0
        count = 0
        for entry in archive:
            count += 1
            if count > maximum_files:
                raise ArtifactPolicyViolation("archive file count exceeds policy")
            _archive_path(entry.name)
            if entry.issym() or entry.islnk() or entry.isdev():
                raise ArtifactPolicyViolation("archive link or device is forbidden")
            total += entry.size
            if total > maximum_bytes:
                raise ArtifactPolicyViolation("archive size exceeds policy")
        return total


def _archive_path(value: str) -> None:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactPolicyViolation("archive path escapes artifact root")


def _parents_until_root(path: Path) -> tuple[Path, ...]:
    return tuple([path, *path.parents])


def _git(repository: Path, arguments: tuple[str, ...]) -> str:
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), *arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": str(repository),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxRuntimeError("repair worktree command failed") from exc
    if result.returncode != 0:
        raise SandboxRuntimeError("repair worktree command failed")
    return result.stdout
