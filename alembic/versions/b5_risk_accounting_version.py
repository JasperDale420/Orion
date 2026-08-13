"""risk_accounting_version

Add ``accounting_version`` to ``risk_state`` so the meaning of the stored money
columns travels with the row itself.

Option realized P&L was folded into ``current_daily_loss`` / ``current_equity``
without the 100x contract multiplier, so every row written before that fix holds
those figures at 1/100th scale. A global "baseline recorded" flag stored
elsewhere cannot prove which convention produced a given row: a rolled-back or
stale process could write 1/100-scale values while the external flag still
claimed the new convention. Binding the version to the row closes that
provenance gap — RiskManager writes the current version on every state save and
refuses order admission for any row that does not carry it.

NULL means "written before this migration" — legacy, untrusted, migration
required. It is deliberately not backfilled: backfilling would assert a
provenance the data does not have.

Hand-written post-baseline migration. ``down_revision`` chains off
``b4_journal_exit_legs`` so ``alembic heads`` stays a single head.

Revision ID: b5_risk_accounting_version
Revises: b4_journal_exit_legs
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b5_risk_accounting_version"
down_revision: Union[str, Sequence[str], None] = "b4_journal_exit_legs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the accounting-provenance column to risk_state."""
    with op.batch_alter_table("risk_state") as batch_op:
        batch_op.add_column(sa.Column("accounting_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove the accounting-provenance column from risk_state."""
    with op.batch_alter_table("risk_state") as batch_op:
        batch_op.drop_column("accounting_version")
