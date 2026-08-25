"""empty message

Revision ID: a8d3468de54f
Revises: b8e69d307135
Create Date: 2026-08-24 19:20:45.524821

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8d3468de54f'
down_revision: Union[str, Sequence[str], None] = 'b8e69d307135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
