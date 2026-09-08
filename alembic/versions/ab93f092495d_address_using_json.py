"""address using json

Revision ID: ab93f092495d
Revises: 9c8fbf1d7d7d
Create Date: 2026-09-03 14:10:23.147216

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab93f092495d'
down_revision: Union[str, Sequence[str], None] = '9c8fbf1d7d7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: change customers.destination from VARCHAR to JSON."""
    # PostgreSQL cannot cast VARCHAR->JSON implicitly; use USING clause.
    # Existing values are NULL or plain strings — NULLIF converts empty to NULL.
    op.execute(
        "ALTER TABLE customers ALTER COLUMN destination TYPE JSON "
        "USING NULLIF(destination, '')::json"
    )


def downgrade() -> None:
    """Downgrade schema: revert customers.destination from JSON to VARCHAR."""
    op.execute(
        "ALTER TABLE customers ALTER COLUMN destination TYPE VARCHAR(255) "
        "USING destination::text"
    )
