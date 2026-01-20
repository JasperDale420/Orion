"""Add extended checkpoints 2w, 3w, 4w

Revision ID: b067d4e73c45
Revises: c41e5be876d8
Create Date: 2026-01-06 15:27:00.000000

Adds 2-week, 3-week, and 4-week checkpoint columns to price_target_labels
for POSITION bucket trades that have longer holding periods.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b067d4e73c45"
down_revision: Union[str, None] = "c41e5be876d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 2-week checkpoints
    op.add_column("price_target_labels", sa.Column("price_at_2w", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("return_at_2w", sa.Float(), nullable=True))

    # Add 3-week checkpoints
    op.add_column("price_target_labels", sa.Column("price_at_3w", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("return_at_3w", sa.Float(), nullable=True))

    # Add 4-week checkpoints
    op.add_column("price_target_labels", sa.Column("price_at_4w", sa.Float(), nullable=True))
    op.add_column("price_target_labels", sa.Column("return_at_4w", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("price_target_labels", "return_at_4w")
    op.drop_column("price_target_labels", "price_at_4w")
    op.drop_column("price_target_labels", "return_at_3w")
    op.drop_column("price_target_labels", "price_at_3w")
    op.drop_column("price_target_labels", "return_at_2w")
    op.drop_column("price_target_labels", "price_at_2w")
