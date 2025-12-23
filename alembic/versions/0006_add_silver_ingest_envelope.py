"""Add ingest envelope fields to silver tables

Revision ID: 0006_add_silver_ingest_envelope
Revises: 0005_add_audit_logs
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0006_add_silver_ingest_envelope"
down_revision: Union[str, None] = "0005_add_audit_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("silver_uw_flow"):
        op.create_table(
            "silver_uw_flow",
            sa.Column("event_id", sa.String(), primary_key=True),
            sa.Column("source_event_id", sa.String(), nullable=True),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("flow_ts_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("put_call", sa.String(length=1), nullable=False),
            sa.Column("expiry", sa.String(), nullable=False),
            sa.Column("strike", sa.Float(), nullable=False),
            sa.Column("option_price", sa.Float(), nullable=False),
            sa.Column("size_contracts", sa.Integer(), nullable=False),
            sa.Column("premium_usd", sa.Float(), nullable=False),
            sa.Column("bid", sa.Float(), nullable=True),
            sa.Column("ask", sa.Float(), nullable=True),
            sa.Column("underlying_price", sa.Float(), nullable=True),
            sa.Column("aggressor", sa.String(), nullable=True),
            sa.Column("is_sweep", sa.String(), nullable=True),
            sa.Column("flags_json", sa.JSON(), nullable=True),
            sa.Column("volume_contract", sa.Float(), nullable=True),
            sa.Column("open_interest", sa.Float(), nullable=True),
            sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_silver_flow_ticker_time", "silver_uw_flow", ["ticker", "flow_ts_utc"], unique=False)
    else:
        op.add_column("silver_uw_flow", sa.Column("source_event_id", sa.String(), nullable=True))
        op.add_column(
            "silver_uw_flow",
            sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        )

    if not inspector.has_table("silver_uw_darkpool"):
        op.create_table(
            "silver_uw_darkpool",
            sa.Column("event_id", sa.String(), primary_key=True),
            sa.Column("source_event_id", sa.String(), nullable=True),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("dark_ts_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("trade_price", sa.Float(), nullable=False),
            sa.Column("size_shares", sa.Float(), nullable=False),
            sa.Column("venue", sa.String(), nullable=True),
            sa.Column("conditions", sa.String(), nullable=True),
            sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_silver_darkpool_ticker_time", "silver_uw_darkpool", ["ticker", "dark_ts_utc"], unique=False)
    else:
        op.add_column("silver_uw_darkpool", sa.Column("source_event_id", sa.String(), nullable=True))
        op.add_column(
            "silver_uw_darkpool",
            sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        )

    op.add_column("silver_uw_alerts", sa.Column("source_event_id", sa.String(), nullable=True))
    op.add_column(
        "silver_uw_alerts", sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json"))
    )

    if not inspector.has_table("silver_alpaca_bars"):
        op.create_table(
            "silver_alpaca_bars",
            sa.Column("ticker", sa.String(), primary_key=True),
            sa.Column("bar_start_ts_utc", sa.DateTime(timezone=True), primary_key=True),
            sa.Column("open", sa.Float(), nullable=False),
            sa.Column("high", sa.Float(), nullable=False),
            sa.Column("low", sa.Float(), nullable=False),
            sa.Column("close", sa.Float(), nullable=False),
            sa.Column("volume", sa.Float(), nullable=False),
            sa.Column("vwap", sa.Float(), nullable=True),
            sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
        )
    else:
        op.add_column(
            "silver_alpaca_bars",
            sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        )


def downgrade() -> None:
    op.drop_column("silver_alpaca_bars", "ingest")

    op.drop_column("silver_uw_alerts", "ingest")
    op.drop_column("silver_uw_alerts", "source_event_id")

    op.drop_column("silver_uw_darkpool", "ingest")
    op.drop_column("silver_uw_darkpool", "source_event_id")

    op.drop_column("silver_uw_flow", "ingest")
    op.drop_column("silver_uw_flow", "source_event_id")
