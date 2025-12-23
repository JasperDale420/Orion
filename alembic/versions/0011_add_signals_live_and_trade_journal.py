"""Add signals_live and trade_journal_entries

Revision ID: 0011_add_signals_live_and_trade_journal
Revises: 0010_add_positions_snapshots
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0011_add_signals_live_and_trade_journal"
down_revision: Union[str, None] = "0010_add_positions_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("signals_live"):
        op.create_table(
            "signals_live",
            sa.Column("signal_id", sa.String(), primary_key=True),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False, index=True),
            sa.Column("ticker", sa.String(), nullable=False, index=True),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("rule_id", sa.String(), nullable=True, index=True),
            sa.Column("model_version", sa.String(), nullable=True, index=True),
            sa.Column("expected_return", sa.Float(), nullable=True),
            sa.Column("p_take", sa.Float(), nullable=True),
            sa.Column("risk_score", sa.Float(), nullable=True),
            sa.Column("entry_logic", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("exit_rules", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("decision_trace_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )
        op.create_index("ix_signals_live_ticker_time", "signals_live", ["ticker", "timestamp_utc"], unique=False)

    if not insp.has_table("trade_journal_entries"):
        op.create_table(
            "trade_journal_entries",
            sa.Column("decision_id", sa.String(), primary_key=True),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("signal_id", sa.String(), nullable=False, index=True),
            sa.Column("candidate_id", sa.String(), nullable=True, index=True),
            sa.Column("solver_id", sa.String(), nullable=True, index=True),
            sa.Column("ticker", sa.String(), nullable=False, index=True),
            sa.Column("direction", sa.String(), nullable=True),
            sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("decision_trace_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("client_order_id", sa.String(), nullable=True, index=True),
            sa.Column("broker_order_id", sa.String(), nullable=True, index=True),
            sa.Column("filled_qty", sa.Float(), nullable=True),
            sa.Column("filled_avg_price", sa.Float(), nullable=True),
            sa.Column("filled_at_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("realized_pnl", sa.Float(), nullable=True),
            sa.Column("notes", sa.String(), nullable=True),
            sa.Column("raw_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )
        op.create_index(
            "ix_trade_journal_ticker_created", "trade_journal_entries", ["ticker", "created_at_utc"], unique=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("trade_journal_entries"):
        op.drop_index("ix_trade_journal_ticker_created", table_name="trade_journal_entries")
        op.drop_table("trade_journal_entries")

    if insp.has_table("signals_live"):
        op.drop_index("ix_signals_live_ticker_time", table_name="signals_live")
        op.drop_table("signals_live")
