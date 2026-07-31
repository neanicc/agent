from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Protocol


DELETION_STAGES = (
    "postgres",
    "objects",
    "temporal_search",
    "notifications",
    "derived_metrics",
)


@dataclass(frozen=True, slots=True)
class ExportBundle:
    tenant_id: uuid.UUID
    generated_at: datetime
    rows: tuple[dict[str, Any], ...]
    objects: tuple[dict[str, Any], ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class DeletionTombstone:
    tenant_id_hash: str
    completed_at: datetime
    workflow_id: str
    stage_count: int


@dataclass(frozen=True, slots=True)
class DeletionResult:
    status: str
    workflow_id: str
    completed_stages: tuple[str, ...]
    tombstone: DeletionTombstone | None = None


class PrivacyBackend(Protocol):
    durable: bool

    def export_rows(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]: ...

    def export_objects(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]: ...

    def delete_stage(self, tenant_id: uuid.UUID, stage: str) -> None: ...

    def save_tombstone(self, tombstone: DeletionTombstone) -> None: ...


class PrivacyCheckpointStore(Protocol):
    durable: bool

    def completed(self, workflow_id: str) -> frozenset[str]: ...

    def mark_completed(self, workflow_id: str, stage: str) -> None: ...

    def tombstone(self, workflow_id: str) -> DeletionTombstone | None: ...

    def save_tombstone(self, workflow_id: str, value: DeletionTombstone) -> None: ...


@dataclass(slots=True)
class InMemoryPrivacyBackend:
    """Test adapter representing every tenant-bearing storage subsystem."""

    durable: bool = False
    rows: dict[uuid.UUID, list[dict[str, Any]]] = field(default_factory=dict)
    objects: dict[uuid.UUID, list[dict[str, Any]]] = field(default_factory=dict)
    temporal_search: dict[uuid.UUID, list[dict[str, Any]]] = field(default_factory=dict)
    notifications: dict[uuid.UUID, list[dict[str, Any]]] = field(default_factory=dict)
    derived_metrics: dict[uuid.UUID, list[dict[str, Any]]] = field(default_factory=dict)
    tombstones: list[DeletionTombstone] = field(default_factory=list)
    fail_once_at: str | None = None
    calls: list[str] = field(default_factory=list)

    def export_rows(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        return [dict(value) for value in self.rows.get(tenant_id, [])]

    def export_objects(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        return [dict(value) for value in self.objects.get(tenant_id, [])]

    def delete_stage(self, tenant_id: uuid.UUID, stage: str) -> None:
        if stage not in DELETION_STAGES:
            raise ValueError("unknown privacy deletion stage")
        self.calls.append(stage)
        if self.fail_once_at == stage:
            self.fail_once_at = None
            raise RuntimeError("injected deletion interruption")
        mapping = {
            "postgres": self.rows,
            "objects": self.objects,
            "temporal_search": self.temporal_search,
            "notifications": self.notifications,
            "derived_metrics": self.derived_metrics,
        }[stage]
        mapping.pop(tenant_id, None)

    def save_tombstone(self, tombstone: DeletionTombstone) -> None:
        if tombstone not in self.tombstones:
            self.tombstones.append(tombstone)


@dataclass(slots=True)
class InMemoryPrivacyCheckpoints:
    durable: bool = False
    stages: dict[str, set[str]] = field(default_factory=dict)
    tombstones: dict[str, DeletionTombstone] = field(default_factory=dict)

    def completed(self, workflow_id: str) -> frozenset[str]:
        return frozenset(self.stages.get(workflow_id, set()))

    def mark_completed(self, workflow_id: str, stage: str) -> None:
        self.stages.setdefault(workflow_id, set()).add(stage)

    def tombstone(self, workflow_id: str) -> DeletionTombstone | None:
        return self.tombstones.get(workflow_id)

    def save_tombstone(self, workflow_id: str, value: DeletionTombstone) -> None:
        self.tombstones.setdefault(workflow_id, value)


class PrivacyService:
    def __init__(
        self,
        backend: PrivacyBackend,
        checkpoints: PrivacyCheckpointStore,
        *,
        tombstone_key: bytes,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        require_durable: bool = False,
    ) -> None:
        if len(tombstone_key) < 32:
            raise ValueError("tombstone HMAC key must contain at least 32 bytes")
        if require_durable and (not backend.durable or not checkpoints.durable):
            raise ValueError("hosted privacy workflows require durable adapters")
        self.backend = backend
        self.checkpoints = checkpoints
        self.tombstone_key = bytes(tombstone_key)
        self.clock = clock

    def export(self, tenant_id: uuid.UUID) -> ExportBundle:
        rows = tuple(_public_export(value) for value in self.backend.export_rows(tenant_id))
        objects = tuple(_public_export(value) for value in self.backend.export_objects(tenant_id))
        canonical = json.dumps(
            {"rows": rows, "objects": objects},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return ExportBundle(
            tenant_id=tenant_id,
            generated_at=self.clock(),
            rows=rows,
            objects=objects,
            manifest_sha256=hashlib.sha256(canonical).hexdigest(),
        )

    def delete_tenant(self, tenant_id: uuid.UUID) -> DeletionResult:
        workflow_id = "delete:" + hmac.new(
            self.tombstone_key,
            tenant_id.bytes,
            hashlib.sha256,
        ).hexdigest()
        existing = self.checkpoints.tombstone(workflow_id)
        if existing is not None:
            return DeletionResult(
                "completed",
                workflow_id,
                DELETION_STAGES,
                existing,
            )
        completed = set(self.checkpoints.completed(workflow_id))
        try:
            for stage in DELETION_STAGES:
                if stage in completed:
                    continue
                self.backend.delete_stage(tenant_id, stage)
                self.checkpoints.mark_completed(workflow_id, stage)
                completed.add(stage)
        except Exception:
            return DeletionResult(
                "interrupted",
                workflow_id,
                tuple(stage for stage in DELETION_STAGES if stage in completed),
            )
        tombstone = DeletionTombstone(
            tenant_id_hash=workflow_id.removeprefix("delete:"),
            completed_at=self.clock(),
            workflow_id=workflow_id,
            stage_count=len(DELETION_STAGES),
        )
        self.backend.save_tombstone(tombstone)
        self.checkpoints.save_tombstone(workflow_id, tombstone)
        return DeletionResult("completed", workflow_id, DELETION_STAGES, tombstone)


_SENSITIVE_EXPORT_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "private_key",
        "credential",
        "authorization",
        "presigned_url",
    }
)


def _public_export(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _public_value(item)
        for key, item in value.items()
        if key.lower() not in _SENSITIVE_EXPORT_KEYS
    }


def _public_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _public_export(value)
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return value
