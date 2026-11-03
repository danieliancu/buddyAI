"""billing: ola Care price back to £7.99 (only where the seeded value was never changed)

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-03 12:00:00.000000

The site, the terms and the Stripe price say £7.99. This value is display only: the amount charged
comes from the Stripe price, so no subscription changes.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0033'
down_revision: Union[str, None] = '0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE billing_settings SET care_price_pence = 799 WHERE id = 1 AND care_price_pence = 790")


def downgrade() -> None:
    op.execute("UPDATE billing_settings SET care_price_pence = 790 WHERE id = 1 AND care_price_pence = 799")
