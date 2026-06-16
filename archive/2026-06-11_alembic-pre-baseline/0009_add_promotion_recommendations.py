"""Add promotion_recommendations table

Revision ID: 0009_add_promotion_recommendations
Revises: 0008_extend_candidate_labels
Create Date: 2025-12-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_add_promotion_recommendations"
down_revision: str | None = "0008_extend_candidate_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("promotion_recommendations"):
        return

    op.create_table(
        "promotion_recommendations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("solver_id", sa.String(), sa.ForeignKey("solvers.solver_id"), nullable=False),
        sa.Column("current_stage", sa.String(), nullable=False),
        sa.Column("recommended_stage", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("metrics_snapshot", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(), nullable=True),
    )

    op.create_index("ix_promotion_recommendations_solver_id", "promotion_recommendations", ["solver_id"], unique=False)
    op.create_index("ix_promotion_recommendations_status", "promotion_recommendations", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("promotion_recommendations"):
        return

    op.drop_index("ix_promotion_recommendations_status", table_name="promotion_recommendations")
    op.drop_index("ix_promotion_recommendations_solver_id", table_name="promotion_recommendations")
    op.drop_table("promotion_recommendations")
