"""store destination-specific delivery estimates on orders

Revision ID: 5c7d29a8e1f4
Revises: 4b07b497595b
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5c7d29a8e1f4"
down_revision: Union[str, Sequence[str], None] = "4b07b497595b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivery_destination", sa.String(length=255), nullable=True))
    op.add_column("orders", sa.Column("estimated_delivery_minutes", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("confirmed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "confirmed_at")
    op.drop_column("orders", "estimated_delivery_minutes")
    op.drop_column("orders", "delivery_destination")
