"""Add P1 ML feature columns.

Revision ID: 0022_p1_ml_features
Revises: 0021_darkpool_buckets
Create Date: 2025-12-31
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "0022_p1_ml_features"
down_revision = "0021_darkpool_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add P1 ML feature columns.

    - Relative Volume: rvol_1h (current hour vs avg), rvol_daily
    - Flow Aggression: ask_side_ratio, sweep_ratio_1h
    - Prior Trade Context: same_ticker_premium_1h
    """
    # Relative volume
    op.add_column("price_target_labels", sa.Column("rvol_1h", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("rvol_daily", sa.Float(), nullable=True))

    # Flow aggression
    op.add_column("price_target_labels", sa.Column("ask_side_ratio", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("sweep_ratio_1h", sa.Float(), nullable=True))

    # Prior trade context
    op.add_column("price_target_labels", sa.Column("same_ticker_premium_1h", sa.Float(), nullable=True))


def downgrade() -> None:
    """Remove P1 ML feature columns."""
    op.drop_column("price_target_labels", "rvol_1h")
    op.drop_column("price_target_labels", "rvol_daily")
    op.drop_column("price_target_labels", "ask_side_ratio")
    op.drop_column("price_target_labels", "sweep_ratio_1h")
    op.drop_column("price_target_labels", "same_ticker_premium_1h")
