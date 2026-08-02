"""Add append-only metering, quota, and billing records.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "meter_entries",
    "quota_reservations",
    "billing_subscriptions",
    "billing_events",
    "billing_adjustments",
    "billing_invoices",
)


def tenant_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )


def timestamps() -> tuple[sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "price_catalogs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("version", sa.String(64), nullable=False, unique=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("prices", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "meter_entries",
        *tenant_columns(),
        sa.Column("usage_id", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("units", sa.Numeric(28, 8), nullable=False),
        sa.Column("provider_cost_usd", sa.Numeric(28, 8), nullable=True),
        sa.Column(
            "catalog_version",
            sa.String(64),
            sa.ForeignKey("price_catalogs.version"),
            nullable=False,
        ),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "usage_id"),
    )
    op.create_index(
        "ix_meter_entries_tenant_observed",
        "meter_entries",
        ["tenant_id", "observed_at"],
    )
    op.create_table(
        "quota_reservations",
        *tenant_columns(),
        sa.Column("reservation_id", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("units", sa.Numeric(28, 8), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "reservation_id"),
    )
    op.create_table(
        "billing_subscriptions",
        *tenant_columns(),
        sa.Column("provider_customer_id", sa.String(256), nullable=False),
        sa.Column("provider_subscription_id", sa.String(256), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "latest_provider_event_created",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("tenant_id"),
        sa.UniqueConstraint("provider_customer_id"),
    )
    op.create_table(
        "billing_events",
        *tenant_columns(),
        sa.Column("provider_event_id", sa.String(256), nullable=False),
        sa.Column("provider_created", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("provider_event_id"),
    )
    op.create_table(
        "billing_adjustments",
        *tenant_columns(),
        sa.Column("adjustment_id", sa.String(256), nullable=False),
        sa.Column("amount_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "adjustment_id"),
    )
    op.create_table(
        "billing_invoices",
        *tenant_columns(),
        sa.Column("invoice_id", sa.String(256), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("adjustments_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("total_usd", sa.Numeric(20, 2), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "invoice_id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        tenant_setting = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
        for table in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING (tenant_id = {tenant_setting}) "
                f"WITH CHECK (tenant_id = {tenant_setting})"
            )


def downgrade() -> None:
    op.drop_table("billing_invoices")
    op.drop_table("billing_adjustments")
    op.drop_table("billing_events")
    op.drop_table("billing_subscriptions")
    op.drop_table("quota_reservations")
    op.drop_index("ix_meter_entries_tenant_observed", table_name="meter_entries")
    op.drop_table("meter_entries")
    op.drop_table("price_catalogs")
