from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any


class ImmutableRecordError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    ordinary_retention: str
    tenant_deletion: str
    mutation: str
    legal_hold_overrides: bool = True


RETENTION_MATRIX: dict[str, RetentionPolicy] = {
    "events": RetentionPolicy("tenant-configured window", "delete", "delete_by_workflow"),
    "source_and_log_artifacts": RetentionPolicy("30 days default", "crypto_shred", "delete_by_workflow"),
    "verification_proofs": RetentionPolicy("contract term", "retain_or_crypto_shred", "delete_by_workflow"),
    "repair_artifacts": RetentionPolicy("contract term", "retain_or_crypto_shred", "delete_by_workflow"),
    "user_and_device_metadata": RetentionPolicy("account lifetime", "deidentify", "privileged_deidentify"),
    "billing_records": RetentionPolicy("statutory period", "retain", "append_tombstone_only"),
    "audit": RetentionPolicy("contractual/statutory period", "deidentify", "append_tombstone_only"),
}


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    actor_id: str
    action: str
    target_kind: str
    target_id: str
    request_id: str
    before_state_hash: str | None
    after_state_hash: str | None
    result: str
    actor_metadata: dict[str, Any]
    created_at: datetime


class AuditStore:
    def __init__(self) -> None:
        self._records: dict[uuid.UUID, AuditRecord] = {}

    def append(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_id: str,
        action: str,
        target_kind: str,
        target_id: str,
        request_id: str,
        before_state_hash: str | None,
        after_state_hash: str | None,
        result: str,
        actor_metadata: dict[str, Any],
    ) -> AuditRecord:
        record = AuditRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            request_id=request_id,
            before_state_hash=before_state_hash,
            after_state_hash=after_state_hash,
            result=result,
            actor_metadata=dict(actor_metadata),
            created_at=datetime.now(timezone.utc),
        )
        self._records[record.id] = record
        return record

    def update(self, _record_id: uuid.UUID, _values: dict[str, Any]) -> None:
        raise ImmutableRecordError("audit entries are append-only")

    def delete(self, _record_id: uuid.UUID) -> None:
        raise ImmutableRecordError("audit entries are append-only")

    def list(self, tenant_id: uuid.UUID) -> list[AuditRecord]:
        return sorted(
            (record for record in self._records.values() if record.tenant_id == tenant_id),
            key=lambda record: (record.created_at, record.id),
        )

    def deidentify_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        authorized_by: str,
        legal_hold: bool,
    ) -> AuditRecord:
        if legal_hold:
            raise ImmutableRecordError("legal hold prevents de-identification")
        for record_id, record in tuple(self._records.items()):
            if record.tenant_id != tenant_id:
                continue
            pseudonym = hashlib.sha256(
                f"{tenant_id}:{record.actor_id}".encode()
            ).hexdigest()[:24]
            self._records[record_id] = replace(
                record,
                actor_id=f"deleted:{pseudonym}",
                actor_metadata={"identity_state": "deidentified"},
            )
        return self.append(
            tenant_id=tenant_id,
            actor_id=authorized_by,
            action="tenant.deidentified",
            target_kind="tenant",
            target_id=str(tenant_id),
            request_id=f"retention_{uuid.uuid4().hex}",
            before_state_hash=None,
            after_state_hash=None,
            result="completed",
            actor_metadata={"authority": "privileged_retention_workflow"},
        )
