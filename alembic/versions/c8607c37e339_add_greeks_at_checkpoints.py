"""Add Greeks at checkpoints

Revision ID: c8607c37e339
Revises: b067d4e73c45
Create Date: 2026-01-06 15:38:00.000000

Adds Greeks (delta, gamma, theta, vega, IV) and time decay features
at each price checkpoint for ML exit optimization.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8607c37e339'
down_revision: Union[str, None] = 'b067d4e73c45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All checkpoints
CHECKPOINTS = ['5m', '10m', '15m', '30m', '1h', '2h', '4h', '8h', 'eod', '1d', '2d', '3d', '1w', '2w', '3w', '4w']

# Greeks to add at each checkpoint
GREEKS = ['delta', 'gamma', 'theta', 'vega', 'iv']

# Time decay features
DECAY_FEATURES = ['dte', 'theta_decay_pct', 'time_value_pct']


def upgrade() -> None:
    # Add Greeks at each checkpoint
    for cp in CHECKPOINTS:
        for greek in GREEKS:
            col_name = f'{greek}_at_{cp}'
            op.add_column('price_target_labels', sa.Column(col_name, sa.Float(), nullable=True))
        
        # Add time decay features
        for feat in DECAY_FEATURES:
            col_name = f'{feat}_at_{cp}'
            op.add_column('price_target_labels', sa.Column(col_name, sa.Float(), nullable=True))


def downgrade() -> None:
    # Remove all added columns
    for cp in CHECKPOINTS:
        for greek in GREEKS:
            col_name = f'{greek}_at_{cp}'
            op.drop_column('price_target_labels', col_name)
        
        for feat in DECAY_FEATURES:
            col_name = f'{feat}_at_{cp}'
            op.drop_column('price_target_labels', col_name)
