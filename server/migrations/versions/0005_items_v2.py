"""notes and reminders v2: no title, longer note text

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_column('title')
        batch_op.alter_column('text', existing_type=sqlmodel.sql.sqltypes.AutoString(length=1000),
                              type_=sqlmodel.sql.sqltypes.AutoString(length=10000), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.alter_column('text', existing_type=sqlmodel.sql.sqltypes.AutoString(length=10000),
                              type_=sqlmodel.sql.sqltypes.AutoString(length=1000), existing_nullable=False)
        batch_op.add_column(sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=False,
                                      server_default=''))
