"""billing: ola Care price £7.99 -> £7.90 (only where the seeded default was never changed)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-02 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE billing_settings SET care_price_pence = 790 WHERE id = 1 AND care_price_pence = 799")


def downgrade() -> None:
    op.execute("UPDATE billing_settings SET care_price_pence = 799 WHERE id = 1 AND care_price_pence = 790")
