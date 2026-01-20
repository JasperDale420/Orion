"""Add ML feature gap columns for exit classifier.

Revision ID: 0020
Revises: 0019_exit_classifier_columns
Create Date: 2025-12-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0020_ml_feature_gaps"
down_revision = "0019_exit_classifier_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IV change during hold
    op.add_column("price_target_labels", sa.Column("iv_at_entry", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("iv_at_1h", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("iv_change_1h_pct", sa.Float(), nullable=True))

    # Underlying price at checkpoints
    op.add_column("price_target_labels", sa.Column("underlying_at_entry", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("underlying_at_1h", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("underlying_change_1h_pct", sa.Float(), nullable=True))

    # Delta/Gamma at entry (Greeks)
    op.add_column("price_target_labels", sa.Column("delta_at_entry", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("gamma_at_entry", sa.Float(), nullable=True))

    # Volume/OI at entry
    op.add_column("price_target_labels", sa.Column("volume_at_entry", sa.Integer(), nullable=True))
    op.add_column("price_target_labels", sa.Column("open_interest_at_entry", sa.Integer(), nullable=True))

    # Time of day features
    op.add_column("price_target_labels", sa.Column("entry_hour", sa.Integer(), nullable=True))
    op.add_column("price_target_labels", sa.Column("entry_session", sa.String(), nullable=True))  # OPEN, MID, CLOSE

    # Day of week
    op.add_column("price_target_labels", sa.Column("entry_day_of_week", sa.Integer(), nullable=True))  # 0=Mon, 4=Fri

    # Earnings proximity
    op.add_column("price_target_labels", sa.Column("days_to_earnings", sa.Integer(), nullable=True))
    op.add_column("price_target_labels", sa.Column("is_post_earnings", sa.Boolean(), nullable=True))

    # Sector/industry
    op.add_column("price_target_labels", sa.Column("sector", sa.String(), nullable=True))
    op.add_column("price_target_labels", sa.Column("industry", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("price_target_labels", "industry")
    op.drop_column("price_target_labels", "sector")
    op.drop_column("price_target_labels", "is_post_earnings")
    op.drop_column("price_target_labels", "days_to_earnings")
    op.drop_column("price_target_labels", "entry_day_of_week")
    op.drop_column("price_target_labels", "entry_session")
    op.drop_column("price_target_labels", "entry_hour")
    op.drop_column("price_target_labels", "open_interest_at_entry")
    op.drop_column("price_target_labels", "volume_at_entry")
    op.drop_column("price_target_labels", "gamma_at_entry")
    op.drop_column("price_target_labels", "delta_at_entry")
    op.drop_column("price_target_labels", "underlying_change_1h_pct")
    op.drop_column("price_target_labels", "underlying_at_1h")
    op.drop_column("price_target_labels", "underlying_at_entry")
    op.drop_column("price_target_labels", "iv_change_1h_pct")
    op.drop_column("price_target_labels", "iv_at_1h")
    op.drop_column("price_target_labels", "iv_at_entry")
