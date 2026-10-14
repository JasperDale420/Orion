"""risk_daily_loss_date

Add ``daily_loss_date`` to ``risk_state`` so the persisted ``current_daily_loss``
carries the America/New_York trading date it belongs to.

``current_daily_loss`` is the input to the ``max_daily_loss`` kill switch. It was
mutated only by fills, loaded verbatim on restart, and reset only by the one-time
operator baseline — nothing rolled it at a trading-day boundary, so the "daily"
limit was a cumulative net-realized-loss ratchet since the last baseline that
eventually halted every new entry permanently. RiskManager now discards the
figure on load, before the admission check reads it, and before a fill
accumulates into it whenever this date is not the current trading date; a
mid-day restart (date == today) preserves the running total.

NULL means "written before this migration" — the figure has no day identity and
is treated as belonging to an earlier session (rolled to zero on next load). It
is deliberately not backfilled.

OPERATOR NOTE: the live TimescaleDB is managed by ``init_db`` ``create_all`` plus
manual ALTERs — its ``alembic_version`` table is empty, so ``alembic upgrade``
will not be what applies this. Before restarting services on this code, run:

    ALTER TABLE risk_state ADD COLUMN IF NOT EXISTS daily_loss_date DATE;

Hand-written post-baseline migration. ``down_revision`` chains off
``b5_risk_accounting_version`` so ``alembic heads`` stays a single head.

Revision ID: b6_risk_daily_loss_date
Revises: b5_risk_accounting_version
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b6_risk_daily_loss_date"
down_revision: Union[str, Sequence[str], None] = "b5_risk_accounting_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the trading-date column to risk_state."""
    with op.batch_alter_table("risk_state") as batch_op:
        batch_op.add_column(sa.Column("daily_loss_date", sa.Date(), nullable=True))


def downgrade() -> None:
    """Remove the trading-date column from risk_state."""
    with op.batch_alter_table("risk_state") as batch_op:
        batch_op.drop_column("daily_loss_date")
