"""journal_exit_legs

Add exit-leg fill columns to trade_journal_entries so both the entry and exit
sides of a round trip are preserved on the same row without one clobbering
the other.

Before this migration, persist_realized_pnl_to_journal wrote the exit fill
timestamp into ``filled_at_utc`` — the ENTRY fill column — destroying entry
provenance on every close. Cost-basis reconstruction and round-trip audit both
depend on the entry price/qty/time remaining intact after the position closes
(B3 RCA 2026-06-11).

Hand-written post-baseline migration. ``down_revision`` chains off
``b3_pnl_attribution`` so ``alembic heads`` stays a single head.

Revision ID: b4_journal_exit_legs
Revises: b3_pnl_attribution
Create Date: 2026-06-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b4_journal_exit_legs"
down_revision: Union[str, Sequence[str], None] = "b3_pnl_attribution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add exit-leg fill columns to trade_journal_entries."""
    with op.batch_alter_table("trade_journal_entries") as batch_op:
        batch_op.add_column(sa.Column("exit_filled_qty", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("exit_filled_avg_price", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("exit_filled_at_utc", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("exit_broker_order_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove exit-leg fill columns from trade_journal_entries."""
    with op.batch_alter_table("trade_journal_entries") as batch_op:
        batch_op.drop_column("exit_broker_order_id")
        batch_op.drop_column("exit_filled_at_utc")
        batch_op.drop_column("exit_filled_avg_price")
        batch_op.drop_column("exit_filled_qty")
