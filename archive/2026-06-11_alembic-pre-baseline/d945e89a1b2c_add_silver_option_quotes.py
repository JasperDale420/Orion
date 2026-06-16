"""Add silver_option_quotes table for real option price tracking.

Revision ID: d945e89a1b2c
Revises: c8607c37e339
Create Date: 2026-01-08

Stores real option quotes from Alpaca API at checkpoint intervals
for more accurate ML training labels.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "d945e89a1b2c"
down_revision = "c8607c37e339"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "silver_option_quotes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("option_symbol", sa.String(32), nullable=False, index=True),
        sa.Column("underlying_ticker", sa.String(10), nullable=False, index=True),
        sa.Column("flow_event_id", sa.String(64), nullable=False, index=True),
        sa.Column("checkpoint", sa.String(10), nullable=False),  # 'entry', '15m', '30m', etc.
        sa.Column("ts_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        # Price data
        sa.Column("bid_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("ask_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("mid_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("last_trade_price", sa.Numeric(10, 4), nullable=True),
        # Greeks at checkpoint
        sa.Column("delta", sa.Numeric(10, 6), nullable=True),
        sa.Column("gamma", sa.Numeric(10, 6), nullable=True),
        sa.Column("theta", sa.Numeric(10, 6), nullable=True),
        sa.Column("vega", sa.Numeric(10, 6), nullable=True),
        sa.Column("iv", sa.Numeric(10, 6), nullable=True),
        # Metadata
        sa.Column("created_at_utc", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        # Unique constraint
        sa.UniqueConstraint("flow_event_id", "checkpoint", name="uq_option_quotes_event_checkpoint"),
    )

    # Index for efficient lookups by flow event
    op.create_index("ix_option_quotes_flow_event", "silver_option_quotes", ["flow_event_id", "checkpoint"])


def downgrade() -> None:
    op.drop_index("ix_option_quotes_flow_event", table_name="silver_option_quotes")
    op.drop_table("silver_option_quotes")
