"""Extend dead_letter_queue with canonical envelope fields for idempotent replay

Revision ID: 0017_extend_dlq_envelope_fields
Revises: 0016_prd_feature_label_contracts
Create Date: 2025-12-18
"""

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_extend_dlq_envelope_fields"
down_revision: str | None = "0016_prd_feature_label_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("dead_letter_queue"):
        return

    cols = {c["name"] for c in insp.get_columns("dead_letter_queue")}

    def add_col(name: str, col: sa.Column) -> None:
        if name not in cols:
            op.add_column("dead_letter_queue", col)

    add_col("event_id", sa.Column("event_id", sa.String(), nullable=True))
    add_col("source_event_id", sa.Column("source_event_id", sa.String(), nullable=True))
    add_col("ticker", sa.Column("ticker", sa.String(), nullable=True))
    add_col("event_ts_utc", sa.Column("event_ts_utc", sa.DateTime(timezone=True), nullable=True))
    add_col("run_id", sa.Column("run_id", sa.String(), nullable=True))
    add_col("trace_id", sa.Column("trace_id", sa.String(), nullable=True))

    # Best-effort backfill from payload json (postgres only; sqlite will no-op/fail silently in older versions).
    try:
        op.execute(sa.text("UPDATE dead_letter_queue SET event_id = COALESCE(event_id, payload->>'event_id')"))
        op.execute(
            sa.text(
                "UPDATE dead_letter_queue SET source_event_id = COALESCE(source_event_id, payload->>'source_event_id')"
            )
        )
        op.execute(sa.text("UPDATE dead_letter_queue SET ticker = COALESCE(ticker, payload->>'ticker')"))
    except Exception:
        pass

    # Indexes for faster replay selection / correlation
    with contextlib.suppress(Exception):
        op.create_index("ix_dead_letter_queue_event_id", "dead_letter_queue", ["event_id"], unique=False)
    with contextlib.suppress(Exception):
        op.create_index("ix_dead_letter_queue_ticker", "dead_letter_queue", ["ticker"], unique=False)
    with contextlib.suppress(Exception):
        op.create_index(
            "ix_dead_letter_queue_status_retry", "dead_letter_queue", ["status", "retry_count"], unique=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("dead_letter_queue"):
        return

    # Drop indexes first where supported
    for idx in ["ix_dead_letter_queue_status_retry", "ix_dead_letter_queue_ticker", "ix_dead_letter_queue_event_id"]:
        with contextlib.suppress(Exception):
            op.drop_index(idx, table_name="dead_letter_queue")

    cols = {c["name"] for c in insp.get_columns("dead_letter_queue")}
    for name in ["trace_id", "run_id", "event_ts_utc", "ticker", "source_event_id", "event_id"]:
        if name in cols:
            op.drop_column("dead_letter_queue", name)
