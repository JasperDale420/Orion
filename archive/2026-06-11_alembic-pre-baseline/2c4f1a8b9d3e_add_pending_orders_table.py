"""Add pending_orders table for restart-durable risk state.

Closes predict finding H-04: previously `RiskManager.pending_orders` was
in-memory-only, so a restart between order submission and fill lost
in-flight exposure tracking until the next Gateway sync.

Revision ID: 2c4f1a8b9d3e
Revises: d945e89a1b2c
Create Date: 2026-05-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c4f1a8b9d3e"  # pragma: allowlist secret
down_revision: str | None = "d945e89a1b2c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_orders",
        sa.Column("order_id", sa.String(), primary_key=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("signed_cost", sa.Float(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_pending_orders_ticker", "pending_orders", ["ticker"])


def downgrade() -> None:
    op.drop_index("ix_pending_orders_ticker", table_name="pending_orders")
    op.drop_table("pending_orders")
