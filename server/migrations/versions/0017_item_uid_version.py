"""items: uid (stable id for voice references - numbers are reused) and version (bumped on every content write)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-04 12:00:00.000000
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('uid', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    conn = op.get_bind()
    items = sa.table('items', sa.column('id', sa.Integer), sa.column('uid', sa.String))
    for (item_id,) in conn.execute(sa.select(items.c.id)).fetchall():
        conn.execute(items.update().where(items.c.id == item_id).values(uid=uuid.uuid4().hex))
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.alter_column('uid', existing_type=sa.String(length=32), nullable=False)
        batch_op.create_unique_constraint('uq_items_uid', ['uid'])


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_constraint('uq_items_uid', type_='unique')
        batch_op.drop_column('version')
        batch_op.drop_column('uid')
