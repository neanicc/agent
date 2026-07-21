from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from loopguard.control.paths import ensure_private_home

from .client import BrowserBrokerClient


class BrowserLeaseStatus(StrEnum):
    REGISTERED = "registered"
    ACTIVE = "active"
    LOST = "lost"


class BrowserLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=256)
    status: BrowserLeaseStatus
    generation: int = Field(default=0, ge=0)
    context_id: str | None = Field(default=None, min_length=32, max_length=256)
    context_marker: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    artifact_directory: str | None = Field(default=None, max_length=4_096)


class BrowserService:
    def __init__(self, path: str | Path, *, client: BrowserBrokerClient) -> None:
        self.path = Path(path).expanduser().absolute()
        ensure_private_home(self.path.parent)
        self.client = client
        self._lock = threading.RLock()
        self._leases = self._load()
        changed = False
        for session_id, lease in list(self._leases.items()):
            if lease.status is BrowserLeaseStatus.ACTIVE or lease.context_id is not None:
                self._leases[session_id] = lease.model_copy(
                    update={
                        "status": BrowserLeaseStatus.LOST,
                        "context_id": None,
                        "artifact_directory": None,
                    }
                )
                changed = True
        if changed:
            self._persist()

    async def session_started(self, session_id: str) -> BrowserLease:
        normalized = _session_id(session_id)
        with self._lock:
            existing = self._leases.get(normalized)
            if existing is not None and existing.status is BrowserLeaseStatus.ACTIVE:
                return existing
            lease = BrowserLease(
                session_id=normalized,
                status=BrowserLeaseStatus.REGISTERED,
            )
            self._leases[normalized] = lease
            self._persist()
            return lease

    async def create_context(
        self,
        session_id: str,
        *,
        browser: Literal["chromium", "firefox", "webkit"],
        allowed_origins: list[str],
        storage_state_path: str | None = None,
    ) -> BrowserLease:
        normalized = _session_id(session_id)
        with self._lock:
            existing = self._leases.get(normalized)
        if existing is None:
            existing = await self.session_started(normalized)
        if existing.status is BrowserLeaseStatus.ACTIVE:
            raise ValueError("browser session already has an active context")
        result = await self.client.create_context(
            session_id=normalized,
            browser=browser,
            allowed_origins=allowed_origins,
            storage_state_path=storage_state_path,
        )
        if not result.ok:
            raise RuntimeError(f"browser context creation failed: {result.code}")
        context_id = result.result.get("contextId")
        artifact_directory = result.result.get("artifactDirectory")
        if (
            not isinstance(context_id, str)
            or len(context_id) < 32
            or len(context_id) > 256
            or not isinstance(artifact_directory, str)
            or not artifact_directory
            or len(artifact_directory) > 4_096
        ):
            raise RuntimeError("browser broker returned an invalid context lease")
        lease = BrowserLease(
            session_id=normalized,
            status=BrowserLeaseStatus.ACTIVE,
            generation=self.client.generation,
            context_id=context_id,
            context_marker=hashlib.sha256(context_id.encode()).hexdigest(),
            browser=browser,
            artifact_directory=artifact_directory,
        )
        with self._lock:
            self._leases[normalized] = lease
            self._persist()
        return lease

    async def session_stopped(self, session_id: str) -> None:
        normalized = _session_id(session_id)
        with self._lock:
            lease = self._leases.get(normalized)
        if (
            lease is not None
            and lease.status is BrowserLeaseStatus.ACTIVE
            and lease.context_id is not None
            and lease.generation == self.client.generation
        ):
            await self.client.close_context(
                lease.context_id,
                session_id=normalized,
            )
        with self._lock:
            self._leases.pop(normalized, None)
            self._persist()

    async def reconcile(self) -> None:
        changed = False
        with self._lock:
            for session_id, lease in list(self._leases.items()):
                if (
                    lease.status is BrowserLeaseStatus.ACTIVE
                    and lease.generation != self.client.generation
                ):
                    self._leases[session_id] = lease.model_copy(
                        update={
                            "status": BrowserLeaseStatus.LOST,
                            "context_id": None,
                            "artifact_directory": None,
                        }
                    )
                    changed = True
            if changed:
                self._persist()

    def lease(self, session_id: str) -> BrowserLease:
        normalized = _session_id(session_id)
        with self._lock:
            lease = self._leases.get(normalized)
        if lease is None:
            raise KeyError("browser lease does not exist")
        return lease

    async def close(self) -> None:
        with self._lock:
            for session_id, lease in list(self._leases.items()):
                if lease.status is BrowserLeaseStatus.ACTIVE:
                    self._leases[session_id] = lease.model_copy(
                        update={
                            "status": BrowserLeaseStatus.LOST,
                            "context_id": None,
                            "artifact_directory": None,
                        }
                    )
            self._persist()
        await self.client.close()

    def _load(self) -> dict[str, BrowserLease]:
        if self.path.is_symlink():
            raise ValueError("browser lease state cannot be a symlink")
        if not self.path.exists():
            return {}
        status = os.lstat(self.path)
        if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
            raise ValueError("browser lease state path is unsafe")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise ValueError("browser lease state must be owner-only")
        if status.st_size > 1024 * 1024:
            raise ValueError("browser lease state exceeds 1 MiB")
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("browser lease state is invalid") from exc
        if not isinstance(document, dict) or set(document) != {"schema_version", "leases"}:
            raise ValueError("browser lease state schema is invalid")
        if document["schema_version"] != 1 or not isinstance(document["leases"], list):
            raise ValueError("browser lease state schema is unsupported")
        if len(document["leases"]) > 10_000:
            raise ValueError("browser lease state exceeds the lease limit")
        leases: dict[str, BrowserLease] = {}
        for raw in document["leases"]:
            if not isinstance(raw, dict) or set(raw) != {
                "session_id",
                "status",
                "generation",
                "context_marker",
                "browser",
            }:
                raise ValueError("browser lease marker is invalid")
            lease = BrowserLease.model_validate({**raw, "context_id": None})
            if lease.session_id in leases:
                raise ValueError("browser lease state has duplicate sessions")
            leases[lease.session_id] = lease
        return leases

    def _persist(self) -> None:
        document = {
            "schema_version": 1,
            "leases": [
                {
                    "session_id": lease.session_id,
                    "status": lease.status.value,
                    "generation": lease.generation,
                    "context_marker": lease.context_marker,
                    "browser": lease.browser,
                }
                for lease in sorted(self._leases.values(), key=lambda item: item.session_id)
            ],
        }
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, self.path)
            if os.name == "posix":
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()


def _session_id(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > 256 or "\x00" in normalized:
        raise ValueError("browser session ID must be bounded and non-empty")
    return normalized
