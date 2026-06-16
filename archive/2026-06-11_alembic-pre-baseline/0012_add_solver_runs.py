"""Add solver_runs table

Revision ID: 0012_add_solver_runs
Revises: 0011_add_signals_live_and_trade_journal
Create Date: 2025-12-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_add_solver_runs"
down_revision: str | None = "0011_add_signals_live_and_trade_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("solver_runs"):
        op.create_table(
            "solver_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("solver_id", sa.String(), nullable=False, index=True),
            sa.Column("dataset_tag", sa.String(), nullable=False),
            sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("num_candidates", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("num_trades", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("gross_pnl", sa.Float(), nullable=True),
            sa.Column("net_pnl", sa.Float(), nullable=True),
            sa.Column("profit_factor", sa.Float(), nullable=True),
            sa.Column("max_drawdown_pct", sa.Float(), nullable=True),
            sa.Column("expect_return_bp", sa.Float(), nullable=True),
            sa.Column("metrics_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_solver_runs_solver_dataset", "solver_runs", ["solver_id", "dataset_tag"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("solver_runs"):
        op.drop_index("ix_solver_runs_solver_dataset", table_name="solver_runs")
        op.drop_table("solver_runs")
