"""regime_vix_source

Add ``vix_source`` and ``vix_observed_at`` to ``regime_snapshots``.

RegimeGate hard-blocks all trading market-wide when the latest snapshot's
vix_level classifies as SHOCK. The only vix source wired in today (an
ETF-price proxy: VIXY/UVIX/VIXM close * a fixed multiplier) is not a real
spot-VIX print and was found, via 2026-08-18 adversarial review, to
hard-block trading on an unvalidated signal. RegimeGate now only treats a
SHOCK reading as ground truth for the hard block when ``vix_source`` is a
trusted value (see ``orion.analysis.regime.is_trusted_vix_source`` --
no source in this codebase currently qualifies, so the hard block is inert
until a real spot-VIX feed is wired in; sizing still degrades on an
untrusted SHOCK reading, just not to a full block).

``vix_observed_at`` separately tracks the underlying vix observation's own
timestamp (e.g. the VIXY bar's own bar time), distinct from the snapshot
row's write time -- a live process can re-persist the same stale
observation every ~5 minute cycle and look perpetually "fresh" by write
time alone.

Both columns are nullable and not backfilled: NULL means "no known source /
no known observation time" and RegimeGate treats that as untrusted / stale,
which is the safe default for every row written before this column existed.

OPERATOR NOTE: the live TimescaleDB is managed by ``init_db`` ``create_all``
plus manual ALTERs -- its ``alembic_version`` table is empty, so ``alembic
upgrade`` will not be what applies this (see b6_risk_daily_loss_date for the
same note). Before restarting services on this code, run:

    ALTER TABLE regime_snapshots ADD COLUMN IF NOT EXISTS vix_source VARCHAR(20);
    ALTER TABLE regime_snapshots ADD COLUMN IF NOT EXISTS vix_observed_at TIMESTAMPTZ;

Revision ID: b7_regime_vix_source
Revises: b6_risk_daily_loss_date
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7_regime_vix_source"
down_revision: Union[str, Sequence[str], None] = "b6_risk_daily_loss_date"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add vix provenance columns to regime_snapshots."""
    with op.batch_alter_table("regime_snapshots") as batch_op:
        batch_op.add_column(sa.Column("vix_source", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("vix_observed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove vix provenance columns from regime_snapshots."""
    with op.batch_alter_table("regime_snapshots") as batch_op:
        batch_op.drop_column("vix_observed_at")
        batch_op.drop_column("vix_source")
