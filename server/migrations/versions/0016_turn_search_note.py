"""turns: search_note ("query -> answer" of the turn's web search, kept in the model's history)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-03 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('turns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('search_note', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('turns', schema=None) as batch_op:
        batch_op.drop_column('search_note')
