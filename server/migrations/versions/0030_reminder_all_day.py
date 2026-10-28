"""reminders: all-day entries (all_day), optionally over a range of days

due_at holds 09:00 local on the first day (the alert time), end_at 09:00 local on the last day of a range.
Additive only, safe on a live database.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-28 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0030'
down_revision: Union[str, None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('all_day', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_column('all_day')
