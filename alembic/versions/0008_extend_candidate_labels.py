"""Extend candidate_labels diagnostics

Revision ID: 0008_extend_candidate_labels
Revises: 0007_add_orders_fills_tables
Create Date: 2025-12-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_extend_candidate_labels"
down_revision: str | None = "0007_add_orders_fills_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidate_labels", sa.Column("time_to_hit_seconds", sa.Float(), nullable=True))
    op.add_column("candidate_labels", sa.Column("mfe", sa.Float(), nullable=True))
    op.add_column("candidate_labels", sa.Column("mae", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_labels", "mae")
    op.drop_column("candidate_labels", "mfe")
    op.drop_column("candidate_labels", "time_to_hit_seconds")
