"""customer uuid

Revision ID: b8e69d307135
Revises: fec46c4a08c1
Create Date: 2026-08-24 19:20:06.811644

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e69d307135'
down_revision: Union[str, Sequence[str], None] = 'fec46c4a08c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema safely with explicit UUID casting."""
    # Drop foreign key constraints referencing customers.id
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_customer_id_fkey")
    op.execute("ALTER TABLE reservations DROP CONSTRAINT IF EXISTS reservations_customer_id_fkey")

    # Drop existing serial sequence default on customers.id
    op.execute("ALTER TABLE customers ALTER COLUMN id DROP DEFAULT")

    # Alter column types using gen_random_uuid()
    op.execute("ALTER TABLE customers ALTER COLUMN id SET DATA TYPE UUID USING gen_random_uuid()")
    op.execute("ALTER TABLE customers ALTER COLUMN id SET DEFAULT gen_random_uuid()")
    
    op.execute("ALTER TABLE orders ALTER COLUMN customer_id SET DATA TYPE UUID USING NULL")
    op.execute("ALTER TABLE reservations ALTER COLUMN customer_id SET DATA TYPE UUID USING NULL")

    # Re-add foreign key constraints
    op.create_foreign_key('orders_customer_id_fkey', 'orders', 'customers', ['customer_id'], ['id'])
    op.create_foreign_key('reservations_customer_id_fkey', 'reservations', 'customers', ['customer_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_customer_id_fkey")
    op.execute("ALTER TABLE reservations DROP CONSTRAINT IF EXISTS reservations_customer_id_fkey")

    op.execute("ALTER TABLE customers ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER TABLE customers ALTER COLUMN id SET DATA TYPE INTEGER USING 1")
    op.execute("ALTER TABLE orders ALTER COLUMN customer_id SET DATA TYPE INTEGER USING NULL")
    op.execute("ALTER TABLE reservations ALTER COLUMN customer_id SET DATA TYPE INTEGER USING NULL")

    op.create_foreign_key('orders_customer_id_fkey', 'orders', 'customers', ['customer_id'], ['id'])
    op.create_foreign_key('reservations_customer_id_fkey', 'reservations', 'customers', ['customer_id'], ['id'])
