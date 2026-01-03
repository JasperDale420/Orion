"""Add bucket-specific darkpool columns.

Revision ID: 0021_darkpool_buckets
Revises: 0020_ml_feature_gaps
Create Date: 2025-12-31
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "0021_darkpool_buckets"
down_revision = "0020_ml_feature_gaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add bucket-specific darkpool volume columns.
    
    Different time windows for different trade buckets:
    - darkpool_30m: 0DTE trades (ultra-short momentum)
    - darkpool_4h: POSITION trades (medium term accumulation)
    - darkpool_1d: LEAP trades (longer term accumulation)
    
    Note: darkpool_volume_1h already exists from prior migration.
    """
    # Add new darkpool columns
    op.add_column(
        "price_target_labels",
        sa.Column("darkpool_30m", sa.Float(), nullable=True)
    )
    op.add_column(
        "price_target_labels",
        sa.Column("darkpool_4h", sa.Float(), nullable=True)
    )
    op.add_column(
        "price_target_labels",
        sa.Column("darkpool_1d", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    """Remove bucket-specific darkpool columns."""
    op.drop_column("price_target_labels", "darkpool_30m")
    op.drop_column("price_target_labels", "darkpool_4h")
    op.drop_column("price_target_labels", "darkpool_1d")
