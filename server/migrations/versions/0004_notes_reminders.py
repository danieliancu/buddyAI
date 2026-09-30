"""notes and reminders

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 10:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=False),
    sa.Column('text', sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=False),
    sa.Column('due_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
    sa.Column('fired_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'kind', 'number', name='uq_items_account_kind_number')
    )
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_items_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_items_due_at'), ['due_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_items_due_at'))
        batch_op.drop_index(batch_op.f('ix_items_account_id'))
    op.drop_table('items')
