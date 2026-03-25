"""Add temporal excursion fields to candidate_labels and labels_event.

Tracks WHEN max/min returns occurred (ts_mfe, ts_mae) and derived metrics
(time_to_mfe_seconds, time_to_mae_seconds, mfe_mae_ratio, excursion_velocity,
capture_efficiency) so ML models can learn timing patterns like
"fast time-to-MFE = high conviction signal".

Revision ID: 0025_add_temporal_excursion_fields
Revises: 0024_fix_embedding_vec_dimension
Create Date: 2026-03-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_add_temporal_excursion_fields"
down_revision: str | None = "0024_fix_embedding_vec_dimension"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ["candidate_labels", "labels_event"]:
        op.add_column(table, sa.Column("ts_mfe", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("ts_mae", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("time_to_mfe_seconds", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("time_to_mae_seconds", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("mfe_mae_ratio", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("excursion_velocity", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("capture_efficiency", sa.Float(), nullable=True))


def downgrade() -> None:
    for table in ["candidate_labels", "labels_event"]:
        for col in [
            "ts_mfe",
            "ts_mae",
            "time_to_mfe_seconds",
            "time_to_mae_seconds",
            "mfe_mae_ratio",
            "excursion_velocity",
            "capture_efficiency",
        ]:
            op.drop_column(table, col)
