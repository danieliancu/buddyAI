"""reminders: optional advance notice (notify_before_min) and when it was delivered (early_fired_at)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-01 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notify_before_min', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('early_fired_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_column('early_fired_at')
        batch_op.drop_column('notify_before_min')
