"""Add preference profiles and observed usage records.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preference_profiles",
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_index(
        "ix_preference_profiles_tenant_id",
        "preference_profiles",
        ["tenant_id"],
    )
    op.create_table(
        "usage_records",
        sa.Column("usage_id", sa.String(length=256), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "usage_id"),
    )
    op.create_index("ix_usage_records_tenant_id", "usage_records", ["tenant_id"])
    op.create_index("ix_usage_records_observed_at", "usage_records", ["observed_at"])
    if op.get_bind().dialect.name == "postgresql":
        tenant_setting = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
        for table in ("preference_profiles", "usage_records"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING (tenant_id = {tenant_setting}) "
                f"WITH CHECK (tenant_id = {tenant_setting})"
            )


def downgrade() -> None:
    op.drop_index("ix_usage_records_observed_at", table_name="usage_records")
    op.drop_index("ix_usage_records_tenant_id", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index("ix_preference_profiles_tenant_id", table_name="preference_profiles")
    op.drop_table("preference_profiles")
