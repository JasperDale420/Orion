"""add_regime_snapshots_table

Revision ID: 72d3429dcac5
Revises: 0027_add_server_default_uuid_to_decision_id_pks
Create Date: 2026-03-26 13:47:03.890646

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "72d3429dcac5"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "0027_add_server_default_uuid_to_decision_id_pks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "regime_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("trend_regime", sa.String(length=20), nullable=True),
        sa.Column("vol_regime", sa.String(length=20), nullable=True),
        sa.Column("risk_regime", sa.String(length=20), nullable=True),
        sa.Column("session_regime", sa.String(length=20), nullable=True),
        sa.Column("vix_regime", sa.String(length=20), nullable=True),
        sa.Column("vix_level", sa.Float(), nullable=True),
        sa.Column("realized_vol", sa.Float(), nullable=True),
        sa.Column("trend_strength", sa.Float(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("confidence_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_regime_snapshots_ticker_ts", "regime_snapshots", ["ticker", "ts_utc"], unique=False)
    op.create_index(op.f("ix_regime_snapshots_ticker"), "regime_snapshots", ["ticker"], unique=False)
    op.create_index(op.f("ix_regime_snapshots_ts_utc"), "regime_snapshots", ["ts_utc"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_regime_snapshots_ts_utc"), table_name="regime_snapshots")
    op.drop_index(op.f("ix_regime_snapshots_ticker"), table_name="regime_snapshots")
    op.drop_index("idx_regime_snapshots_ticker_ts", table_name="regime_snapshots")
    op.drop_table("regime_snapshots")
