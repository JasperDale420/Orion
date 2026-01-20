"""add_options_fields_to_candidate_trades

Revision ID: c41e5be876d8
Revises: 0022_p1_ml_features
Create Date: 2026-01-06 14:21:52.811127

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c41e5be876d8'
down_revision: Union[str, Sequence[str], None] = '0022_p1_ml_features'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add options-specific fields to candidate_trades table."""
    # Add options fields (nullable for backward compatibility)
    op.add_column("candidate_trades", sa.Column("option_symbol", sa.String(), nullable=True))
    op.add_column("candidate_trades", sa.Column("strike_price", sa.Float(), nullable=True))
    op.add_column("candidate_trades", sa.Column("expiration_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("candidate_trades", sa.Column("option_type", sa.String(), nullable=True))
    op.add_column("candidate_trades", sa.Column("underlying_price", sa.Float(), nullable=True))
    op.add_column("candidate_trades", sa.Column("premium", sa.Float(), nullable=True))
    
    # Add index on option_symbol
    op.create_index("ix_candidate_option_symbol", "candidate_trades", ["option_symbol"])


def downgrade() -> None:
    """Remove options fields from candidate_trades table."""
    op.drop_index("ix_candidate_option_symbol", table_name="candidate_trades")
    op.drop_column("candidate_trades", "premium")
    op.drop_column("candidate_trades", "underlying_price")
    op.drop_column("candidate_trades", "option_type")
    op.drop_column("candidate_trades", "expiration_date")
    op.drop_column("candidate_trades", "strike_price")
    op.drop_column("candidate_trades", "option_symbol")
