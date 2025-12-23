"""Add orders and fills tables

Revision ID: 0007_add_orders_fills_tables
Revises: 0006_add_silver_ingest_envelope
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007_add_orders_fills_tables"
down_revision: Union[str, None] = "0006_add_silver_ingest_envelope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_id", sa.String(), nullable=True),
        sa.Column("candidate_id", sa.String(), nullable=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("client_order_id", sa.String(), nullable=True),
        sa.Column("broker_order_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_orders_ticker", "orders", ["ticker"], unique=False)
    op.create_index("ix_orders_decision_id", "orders", ["decision_id"], unique=False)
    op.create_index("ix_orders_candidate_id", "orders", ["candidate_id"], unique=False)
    op.create_index("ix_orders_client_order_id", "orders", ["client_order_id"], unique=False)
    op.create_index("ix_orders_broker_order_id", "orders", ["broker_order_id"], unique=False)

    op.create_table(
        "fills",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("broker_order_id", sa.String(), nullable=False),
        sa.Column("client_order_id", sa.String(), nullable=True),
        sa.Column("filled_qty", sa.Float(), nullable=False),
        sa.Column("filled_avg_price", sa.Float(), nullable=True),
        sa.Column("side", sa.String(), nullable=True),
        sa.Column("filled_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_fills_ticker", "fills", ["ticker"], unique=False)
    op.create_index("ux_fills_broker_order_id", "fills", ["broker_order_id"], unique=True)
    op.create_index("ix_fills_client_order_id", "fills", ["client_order_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_fills_client_order_id", table_name="fills")
    op.drop_index("ux_fills_broker_order_id", table_name="fills")
    op.drop_index("ix_fills_ticker", table_name="fills")
    op.drop_table("fills")

    op.drop_index("ix_orders_broker_order_id", table_name="orders")
    op.drop_index("ix_orders_client_order_id", table_name="orders")
    op.drop_index("ix_orders_candidate_id", table_name="orders")
    op.drop_index("ix_orders_decision_id", table_name="orders")
    op.drop_index("ix_orders_ticker", table_name="orders")
    op.drop_table("orders")
