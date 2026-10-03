"""turns: mode (chat / voice edit) and tools used, backfilled from older data

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-03 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('turns', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('mode', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False, server_default='chat')
        )
        batch_op.add_column(
            sa.Column('tools', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False, server_default='')
        )
    # Older turns: voice-edit turns are the ones outside a conversation (note or reminder is not known),
    # and a web search shows in the turn's usage records. Other tool calls were not recorded.
    op.execute("UPDATE turns SET mode = 'edit' WHERE conversation_id IS NULL")
    op.execute(
        "UPDATE turns SET tools = 'web_search' WHERE id IN "
        "(SELECT turn_id FROM usage_records WHERE unit IN ('web_search_call', 'cache_hit') AND turn_id IS NOT NULL)"
    )


def downgrade() -> None:
    with op.batch_alter_table('turns', schema=None) as batch_op:
        batch_op.drop_column('tools')
        batch_op.drop_column('mode')
