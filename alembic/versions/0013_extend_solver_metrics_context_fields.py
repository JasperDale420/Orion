"""Extend solver_metrics with context fields

Revision ID: 0013_extend_solver_metrics_context_fields
Revises: 0012_add_solver_runs
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0013_extend_solver_metrics_context_fields"
down_revision: Union[str, None] = "0012_add_solver_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("solver_metrics"):
        return

    cols = {c["name"] for c in insp.get_columns("solver_metrics")}

    if "ticker_bucket" not in cols:
        op.add_column("solver_metrics", sa.Column("ticker_bucket", sa.String(), nullable=True))
    if "horizon_profile" not in cols:
        op.add_column("solver_metrics", sa.Column("horizon_profile", sa.String(), nullable=True))
    if "oos_expect_bp" not in cols:
        op.add_column("solver_metrics", sa.Column("oos_expect_bp", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("solver_metrics"):
        return

    cols = {c["name"] for c in insp.get_columns("solver_metrics")}
    if "oos_expect_bp" in cols:
        op.drop_column("solver_metrics", "oos_expect_bp")
    if "horizon_profile" in cols:
        op.drop_column("solver_metrics", "horizon_profile")
    if "ticker_bucket" in cols:
        op.drop_column("solver_metrics", "ticker_bucket")
