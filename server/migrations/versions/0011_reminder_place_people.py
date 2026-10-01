"""reminders: optional location and participants (taken from the reminder's text by the assistant)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-01 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('participants', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_column('participants')
        batch_op.drop_column('location')
