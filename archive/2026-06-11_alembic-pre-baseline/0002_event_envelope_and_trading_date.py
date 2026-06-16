"""Add canonical event envelope + fix trading_date type

Revision ID: 0002_event_envelope_and_trading_date
Revises: 0001_initial_schema_update
Create Date: 2025-12-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_event_envelope_and_trading_date"
down_revision: str | None = "0001_initial_schema_update"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bronze_events", sa.Column("source_event_id", sa.String(), nullable=True))
    op.add_column("bronze_events", sa.Column("ingest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))

    op.alter_column(
        "bronze_events",
        "trading_date",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.Date(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "bronze_events",
        "trading_date",
        existing_type=sa.Date(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
    )

    op.drop_column("bronze_events", "ingest")
    op.drop_column("bronze_events", "source_event_id")
