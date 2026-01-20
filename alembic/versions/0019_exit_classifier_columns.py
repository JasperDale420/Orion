"""Add exit classifier bucket-specific checkpoints and velocity columns.

Revision ID: 0019
Revises: 0018_meta_experiments_prd_fields
Create Date: 2025-12-31
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "0019_exit_classifier_columns"
down_revision = "0018_meta_experiments_prd_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Time-to-target velocity columns
    op.add_column(
        "price_target_labels",
        sa.Column("time_to_75_pct_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("time_to_100_pct_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("time_to_150_pct_seconds", sa.Integer(), nullable=True),
    )

    # 0DTE checkpoints (15m, 30m)
    op.add_column(
        "price_target_labels",
        sa.Column("price_at_15m", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("return_at_15m", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("price_at_30m", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("return_at_30m", sa.Float(), nullable=True),
    )

    # SWING/POSITION checkpoints (8h, 1d, 2d, 3d, 1w)
    op.add_column(
        "price_target_labels",
        sa.Column("price_at_8h", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("return_at_8h", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("price_at_1d", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("return_at_1d", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("price_at_2d", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("return_at_2d", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("price_at_3d", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("return_at_3d", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("price_at_1w", sa.Float(), nullable=True),
    )
    op.add_column(
        "price_target_labels",
        sa.Column("return_at_1w", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    # Remove columns in reverse order
    op.drop_column("price_target_labels", "return_at_1w")
    op.drop_column("price_target_labels", "price_at_1w")
    op.drop_column("price_target_labels", "return_at_3d")
    op.drop_column("price_target_labels", "price_at_3d")
    op.drop_column("price_target_labels", "return_at_2d")
    op.drop_column("price_target_labels", "price_at_2d")
    op.drop_column("price_target_labels", "return_at_1d")
    op.drop_column("price_target_labels", "price_at_1d")
    op.drop_column("price_target_labels", "return_at_8h")
    op.drop_column("price_target_labels", "price_at_8h")
    op.drop_column("price_target_labels", "return_at_30m")
    op.drop_column("price_target_labels", "price_at_30m")
    op.drop_column("price_target_labels", "return_at_15m")
    op.drop_column("price_target_labels", "price_at_15m")
    op.drop_column("price_target_labels", "time_to_150_pct_seconds")
    op.drop_column("price_target_labels", "time_to_100_pct_seconds")
    op.drop_column("price_target_labels", "time_to_75_pct_seconds")
