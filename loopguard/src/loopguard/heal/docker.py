"""Explicitly lower-assurance local Docker adapter for repair reproduction."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from loopguard.heal.sandbox import (
    SandboxExecution,
    SandboxRuntimeError,
    SandboxSpec,
    validate_artifacts,
)


class DockerSandboxConfig:
    def __init__(self, *, allowed_images: frozenset[str]) -> None:
        if not allowed_images:
            raise ValueError("repair sandbox image allowlist cannot be empty")
        self.allowed_images = allowed_images

    def run_options(self, spec: SandboxSpec) -> dict[str, Any]:
        spec.validate_policy()
        if spec.image not in self.allowed_images:
            raise PermissionError("repair sandbox image is not allowlisted")
        limits = spec.limits
        return {
            "command": list(spec.command),
            "detach": True,
            "network_disabled": True,
            "read_only": True,
            "user": "65532:65532",
            "working_dir": "/workspace",
            "environment": {
                **spec.environment,
                "LOOPGUARD_REPOSITORY_ID": spec.repository_id,
                "LOOPGUARD_REPOSITORY_SHA": spec.repository_sha,
            },
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "privileged": False,
            "pids_limit": limits.pids,
            "mem_limit": limits.memory_bytes,
            "memswap_limit": limits.memory_bytes,
            "nano_cpus": limits.nano_cpus,
            "tmpfs": {
                "/tmp": f"rw,noexec,nosuid,nodev,size={limits.tmpfs_bytes}",
            },
            "volumes": {
                str(spec.source_path): {"bind": "/workspace", "mode": "ro"},
                str(spec.fixture_path): {"bind": "/fixture", "mode": "ro"},
                str(spec.artifact_path): {"bind": "/artifacts", "mode": "rw"},
            },
            "log_config": {
                "type": "local",
                "config": {"max-size": "1m", "max-file": "1"},
            },
            "auto_remove": False,
        }


class LocalDockerRuntime:
    def __init__(
        self,
        *,
        allow_lower_assurance: bool,
        allowed_images: frozenset[str] = frozenset(),
        client: Any | None = None,
    ) -> None:
        if not allow_lower_assurance:
            raise PermissionError("local Docker repair requires explicit opt-in")
        self.config = DockerSandboxConfig(allowed_images=allowed_images)
        if client is None:
            try:
                import docker

                client = docker.from_env()
                client.ping()
            except Exception as exc:
                raise SandboxRuntimeError("local Docker daemon is unavailable") from exc
        self.client = client

    def run(self, spec: SandboxSpec) -> SandboxExecution:
        spec.validate_policy()
        _verify_checkout(spec.source_path, spec.repository_sha)
        _verify_image(self.client, spec.image)
        options = self.config.run_options(spec)
        container = None
        timed_out = False
        exit_code = -1
        stdout = b""
        stderr = b""
        try:
            container = self.client.containers.run(spec.image, **options)
            exit_code, timed_out = _wait_bounded(container, spec)
            stdout = _bounded_logs(container, stdout=True, limit=spec.limits.output_bytes)
            stderr = _bounded_logs(
                container,
                stdout=False,
                limit=max(0, spec.limits.output_bytes - len(stdout)),
            )
            artifacts = validate_artifacts(
                spec.artifact_path,
                maximum_bytes=spec.limits.artifact_bytes,
                maximum_files=spec.limits.artifact_files,
            )
            source, payload = _observed_failure(spec.artifact_path)
            return SandboxExecution(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                observed_source=source,
                observed_payload=payload,
                artifact_paths=artifacts,
                assurance="local_docker",
            )
        except SandboxRuntimeError:
            raise
        except Exception as exc:
            raise SandboxRuntimeError("local Docker reproduction failed safely") from exc
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception as exc:
                    raise SandboxRuntimeError("local Docker container cleanup failed") from exc


def _verify_checkout(path: Path, expected: str) -> None:
    try:
        result = subprocess.run(
            ("git", "-C", str(path), "rev-parse", "HEAD"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": str(path),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxRuntimeError("repair checkout identity is unavailable") from exc
    if result.returncode != 0 or result.stdout.strip() != expected:
        raise SandboxRuntimeError("repair checkout does not match the requested revision")


def _verify_image(client: Any, expected: str) -> None:
    try:
        image = client.images.get(expected)
        digests = image.attrs.get("RepoDigests", ())
    except Exception as exc:
        raise SandboxRuntimeError("allowlisted repair image is not present by digest") from exc
    if expected not in digests:
        raise SandboxRuntimeError("repair image digest could not be verified")


def _bounded_logs(container: Any, *, stdout: bool, limit: int) -> bytes:
    output = bytearray()
    stream = container.logs(stream=True, stdout=stdout, stderr=not stdout)
    for chunk in stream:
        remaining = limit - len(output)
        if remaining <= 0:
            break
        output.extend(bytes(chunk)[:remaining])
    return bytes(output)


def _wait_bounded(container: Any, spec: SandboxSpec) -> tuple[int, bool]:
    deadline = time.monotonic() + spec.limits.timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            container.kill()
            return -1, True
        try:
            status = container.wait(timeout=max(1, min(5, int(remaining))))
            return int(status.get("StatusCode", -1)), False
        except Exception as exc:
            if not _is_timeout(exc):
                raise
        try:
            validate_artifacts(
                spec.artifact_path,
                maximum_bytes=spec.limits.artifact_bytes,
                maximum_files=spec.limits.artifact_files,
            )
        except Exception:
            container.kill()
            raise


def _observed_failure(root: Path) -> tuple[str | None, dict[str, Any] | None]:
    path = root / "observed-failure.json"
    if not path.exists():
        return None, None
    if path.is_symlink() or path.stat().st_size > 65_536:
        raise SandboxRuntimeError("observed failure artifact is invalid")
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SandboxRuntimeError("observed failure artifact is invalid") from exc
    if not isinstance(payload, dict):
        raise SandboxRuntimeError("observed failure artifact is invalid")
    return "webhook", payload


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
