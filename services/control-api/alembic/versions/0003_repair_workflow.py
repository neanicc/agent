"""Add durable repair workflow evidence fields.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.add_column(
        "repairs", sa.Column("state_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "repairs",
        sa.Column(
            "state_hash",
            sa.String(length=64),
            nullable=False,
            server_default="0" * 64,
        ),
    )
    op.add_column(
        "repairs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "repairs",
        sa.Column(
            "publication_deadline",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    deadline_expression = (
        "CURRENT_TIMESTAMP + INTERVAL '7 days'"
        if dialect == "postgresql"
        else "datetime('now', '+7 days')"
    )
    op.execute(
        sa.text(
            "UPDATE repairs SET updated_at = CURRENT_TIMESTAMP, "
            f"publication_deadline = {deadline_expression} "
            "WHERE updated_at IS NULL OR publication_deadline IS NULL"
        )
    )
    with op.batch_alter_table("repairs") as batch:
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column(
            "publication_deadline",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    op.add_column(
        "repairs", sa.Column("workflow_state", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column("repairs", sa.Column("ranking", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column(
        "repairs",
        sa.Column(
            "rollback", sa.Text(), nullable=False, server_default="Revert the repair commit."
        ),
    )
    op.add_column(
        "repairs", sa.Column("publication", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column("repairs", sa.Column("approved_action_id", sa.String(length=256), nullable=True))
    op.add_column("repairs", sa.Column("cancellation_reason", sa.String(length=256), nullable=True))
    op.add_column(
        "repairs",
        sa.Column(
            "total_cost", sa.Numeric(precision=20, scale=8), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "repair_candidates",
        sa.Column("strategy", sa.String(length=256), nullable=False, server_default="unspecified"),
    )
    op.add_column(
        "repair_candidates",
        sa.Column("changed_files", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "repair_candidates",
        sa.Column("changed_lines", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "repair_candidates",
        sa.Column("evaluation", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_repairs_tenant_created", "repairs", ["tenant_id", "created_at", "id"])
    op.create_index(
        "ix_repair_candidates_repair_id",
        "repair_candidates",
        ["repair_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_repair_candidates_repair_id", table_name="repair_candidates")
    op.drop_index("ix_repairs_tenant_created", table_name="repairs")
    for column in ("evaluation", "changed_lines", "changed_files", "strategy"):
        op.drop_column("repair_candidates", column)
    for column in (
        "total_cost",
        "cancellation_reason",
        "approved_action_id",
        "publication",
        "rollback",
        "ranking",
        "workflow_state",
        "publication_deadline",
        "updated_at",
        "state_hash",
        "state_version",
    ):
        op.drop_column("repairs", column)
