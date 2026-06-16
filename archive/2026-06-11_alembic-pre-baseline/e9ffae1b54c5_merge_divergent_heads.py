"""merge divergent heads

Revision ID: e9ffae1b54c5
Revises: 2c4f1a8b9d3e, 72d3429dcac5
Create Date: 2026-06-10 18:51:54.633990

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "e9ffae1b54c5"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = ("2c4f1a8b9d3e", "72d3429dcac5")  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
