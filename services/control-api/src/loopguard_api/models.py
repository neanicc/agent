from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


UUID = uuid.UUID
CLOUD_INGEST_SEQUENCE = Sequence("events_cloud_ingest_seq_seq", start=1)


class Base(DeclarativeBase):
    pass


class IdentifierMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenantOwnedMixin:
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Tenant(IdentifierMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(256), nullable=False)


class User(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "external_subject"),)

    external_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str | None] = mapped_column(String(512))


class Membership(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)


class Device(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("tenant_id", "key_id"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    key_id: Mapped[str] = mapped_column(String(256), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Host(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "hosts"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_algorithm: Mapped[str] = mapped_column(String(32), default="Ed25519")
    health_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adapter_version: Mapped[str | None] = mapped_column(String(128))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Repository(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "repository_handle"),
        UniqueConstraint("tenant_id", "canonical_identity"),
    )

    host_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    repository_handle: Mapped[str] = mapped_column(String(256), nullable=False)
    relay_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class Session(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "host_id", "local_session_id"),)

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    host_id: Mapped[UUID] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    local_session_id: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(64), default="active", nullable=False)
    state_version: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    next_session_seq: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)


class Event(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id"),
        UniqueConstraint("cloud_ingest_seq"),
        UniqueConstraint("tenant_id", "session_id", "session_seq"),
        Index("ix_events_session_replay", "tenant_id", "session_id", "session_seq"),
    )

    event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    cloud_ingest_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    local_log_seq: Mapped[int | None] = mapped_column(BigInteger)
    repo_seq: Mapped[int | None] = mapped_column(BigInteger)
    client_stream_seq: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Action(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "actions"
    __table_args__ = (UniqueConstraint("tenant_id", "action_id"),)

    action_id: Mapped[str] = mapped_column(String(256), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    requested_by_device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"))
    target_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_state_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_state_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce_hash: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    device_key_id: Mapped[str] = mapped_column(String(256), nullable=False)
    device_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    device_signature: Mapped[str] = mapped_column(Text, nullable=False)
    cloud_key_id: Mapped[str] = mapped_column(String(256), nullable=False)
    cloud_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    cloud_signature: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    audit_entry_id: Mapped[UUID | None] = mapped_column(ForeignKey("audit_entries.id"))


class ActionDelivery(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "action_deliveries"
    __table_args__ = (UniqueConstraint("tenant_id", "action_id", "attempt"),)

    action_id: Mapped[UUID] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("tenant_id", "object_key"),)

    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(256), nullable=False)
    encryption_metadata: Mapped[dict[str, Any]] = mapped_column("encryption_metadata", JSON)
    retention_class: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(64), default="pending", nullable=False)


class Change(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "changes"

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id"), index=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("sessions.id"), index=True)
    path_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"))


class Verification(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "verifications"

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id"), index=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("sessions.id"), index=True)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)


class VerificationResult(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "verification_results"
    __table_args__ = (UniqueConstraint("tenant_id", "verification_id", "check_id"),)

    verification_id: Mapped[UUID] = mapped_column(
        ForeignKey("verifications.id", ondelete="CASCADE"), index=True
    )
    check_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"))


class Repair(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "repairs"

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id"), index=True)
    verification_id: Mapped[UUID | None] = mapped_column(ForeignKey("verifications.id"))
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), default="0" * 64, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    publication_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC) + timedelta(days=7),
        nullable=False,
    )
    workflow_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ranking: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rollback: Mapped[str] = mapped_column(
        Text,
        default="Revert the repair commit and rerun the original pipeline.",
        nullable=False,
    )
    publication: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    approved_action_id: Mapped[str | None] = mapped_column(String(256))
    cancellation_reason: Mapped[str | None] = mapped_column(String(256))
    total_cost: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)


class RepairCandidate(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "repair_candidates"

    repair_id: Mapped[UUID] = mapped_column(ForeignKey("repairs.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    patch_artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    proof_artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    strategy: Mapped[str] = mapped_column(String(256), default="unspecified", nullable=False)
    changed_files: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    changed_lines: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class HookCredential(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "hook_credentials"
    __table_args__ = (UniqueConstraint("tenant_id", "key_id"),)

    key_id: Mapped[str] = mapped_column(String(256), nullable=False)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id"), index=True)
    host_id: Mapped[UUID | None] = mapped_column(ForeignKey("hosts.id"))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SigningKey(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "signing_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key_id"),)

    key_id: Mapped[str] = mapped_column(String(256), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_ref: Mapped[str] = mapped_column(String(2048), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    activates_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StreamTicket(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "stream_tickets"
    __table_args__ = (UniqueConstraint("tenant_id", "ticket_hash"),)

    ticket_hash: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    origin: Mapped[str] = mapped_column(String(512), nullable=False)
    after_session_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEntry(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "audit_entries"

    actor_id: Mapped[str] = mapped_column(String(512), nullable=False)
    action: Mapped[str] = mapped_column(String(256), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(512), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_state_hash: Mapped[str | None] = mapped_column(String(128))
    after_state_hash: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class RelayOutbox(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "relay_outbox"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id"),)

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    cloud_ingest_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PreferenceProfile(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "preference_profiles"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class UsageRecord(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("tenant_id", "usage_id"),)

    usage_id: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Any] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("sessions.id"))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class MeterEntry(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "meter_entries"
    __table_args__ = (UniqueConstraint("tenant_id", "usage_id"),)

    usage_id: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    provider_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    catalog_version: Mapped[str] = mapped_column(
        ForeignKey("price_catalogs.version"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class QuotaReservationRecord(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "quota_reservations"
    __table_args__ = (UniqueConstraint("tenant_id", "reservation_id"),)

    reservation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)


class PriceCatalogRecord(IdentifierMixin, Base):
    __tablename__ = "price_catalogs"

    version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    prices: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)


class SubscriptionRecord(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        UniqueConstraint("provider_customer_id"),
    )

    provider_customer_id: Mapped[str] = mapped_column(
        String(256), nullable=False
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    latest_provider_event_created: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingEventRecord(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "billing_events"
    __table_args__ = (UniqueConstraint("provider_event_id"),)

    provider_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_created: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False)


class BillingAdjustmentRecord(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "billing_adjustments"
    __table_args__ = (UniqueConstraint("tenant_id", "adjustment_id"),)

    adjustment_id: Mapped[str] = mapped_column(String(256), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)


class InvoiceRecord(IdentifierMixin, TenantOwnedMixin, Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "invoice_id"),)

    invoice_id: Mapped[str] = mapped_column(String(256), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    adjustments_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False
    )
    total_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
