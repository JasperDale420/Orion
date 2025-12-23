"""Add silver UW alerts table

Revision ID: 0003_add_silver_uw_alerts
Revises: 0002_event_envelope_and_trading_date
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0003_add_silver_uw_alerts"
down_revision: Union[str, None] = "0002_event_envelope_and_trading_date"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "silver_uw_alerts",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("alert_ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("put_call", sa.String(length=1), nullable=True),
        sa.Column("expiry", sa.String(), nullable=True),
        sa.Column("strike", sa.Float(), nullable=True),
        sa.Column("option_price", sa.Float(), nullable=True),
        sa.Column("size_contracts", sa.Integer(), nullable=True),
        sa.Column("premium_usd", sa.Float(), nullable=True),
        sa.Column("volume_contract", sa.Float(), nullable=True),
        sa.Column("open_interest", sa.Float(), nullable=True),
        sa.Column("flags_json", sa.JSON(), nullable=True),
        sa.Column("alert_tags", sa.JSON(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_silver_alerts_ticker_time", "silver_uw_alerts", ["ticker", "alert_ts_utc"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_silver_alerts_ticker_time", table_name="silver_uw_alerts")
    op.drop_table("silver_uw_alerts")
