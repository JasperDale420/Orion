"""Add positions_snapshots table

Revision ID: 0010_add_positions_snapshots
Revises: 0009_add_promotion_recommendations
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0010_add_positions_snapshots"
down_revision: Union[str, None] = "0009_add_promotion_recommendations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("positions_snapshots"):
        return

    op.create_table(
        "positions_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("snapshot_ts_utc", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("ticker", sa.String(), nullable=False, index=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_entry_price", sa.Float(), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("unrealized_pl", sa.Float(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    op.create_index(
        "ix_positions_snapshots_ts_ticker", "positions_snapshots", ["snapshot_ts_utc", "ticker"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("positions_snapshots"):
        return

    op.drop_index("ix_positions_snapshots_ts_ticker", table_name="positions_snapshots")
    op.drop_table("positions_snapshots")
