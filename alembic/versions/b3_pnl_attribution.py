"""pnl_reconciliation + solver/rule pnl attribution

Daily journal-vs-broker PnL reconciliation and per-solver / per-rule realized-PnL
attribution rollups (redesign R5 / task RB.3 — measurement before evolution).

Three post-baseline tables, written once per trading day by
``orion.jobs.reconcile_pnl`` (invoked from the EOD run). They hold MEASUREMENTS
and RECOMMENDATIONS ONLY — no code reads them to mutate a solver stage. Actual
demotion is the separately-gated task RD.1.

Hand-written post-baseline migration. ``down_revision`` chains off
``b1_flow_push_parity`` (the flow-push parity table, created by the parallel B1
work) so ``alembic heads`` stays a single head. After this lands,
``alembic revision --autogenerate`` against the models must show no residual
diff (the baseline-parity guard excludes these via POST_BASELINE_TABLES).

Revision ID: b3_pnl_attribution
Revises: b1_flow_push_parity
Create Date: 2026-06-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3_pnl_attribution"
down_revision: Union[str, Sequence[str], None] = "b1_flow_push_parity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pnl_reconciliation",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("journal_total", sa.Float(), nullable=False),
        sa.Column("broker_total", sa.Float(), nullable=False),
        sa.Column("drift_abs", sa.Float(), nullable=False),
        sa.Column("drift_pct", sa.Float(), nullable=True),
        sa.Column("journal_trade_count", sa.Integer(), nullable=False),
        sa.Column("broker_trade_count", sa.Integer(), nullable=False),
        sa.Column("tolerance_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trade_date"),
    )
    op.create_table(
        "solver_pnl_attribution",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("solver_id", sa.String(), nullable=False),
        sa.Column("n_trades", sa.Integer(), nullable=False),
        sa.Column("realized_pnl_total", sa.Float(), nullable=False),
        sa.Column("expectancy", sa.Float(), nullable=True),
        sa.Column("win_count", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trade_date", "solver_id"),
    )
    op.create_table(
        "rule_pnl_attribution",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("n_trades", sa.Integer(), nullable=False),
        sa.Column("realized_pnl_total", sa.Float(), nullable=False),
        sa.Column("expectancy", sa.Float(), nullable=True),
        sa.Column("win_count", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trade_date", "rule_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("rule_pnl_attribution")
    op.drop_table("solver_pnl_attribution")
    op.drop_table("pnl_reconciliation")
