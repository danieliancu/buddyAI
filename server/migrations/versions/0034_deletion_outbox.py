"""deletion_events: outbox of deletions, so they can be re-applied after a backup is restored

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-03 13:00:00.000000

New tables; nothing existing changes. restore.sh may already have created the marker table on a database
restored from an older backup.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0034'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deletion_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uid', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('device_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('target', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('occurred_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column('replicated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_deletion_events_uid'), 'deletion_events', ['uid'], unique=True)
    op.create_index(op.f('ix_deletion_events_replicated_at'), 'deletion_events', ['replicated_at'], unique=False)
    if not sa.inspect(op.get_bind()).has_table('deletion_reconcile_required'):
        op.create_table(
            'deletion_reconcile_required',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('backup_created', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False, server_default=''),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('deletion_reconcile_required')
    op.drop_index(op.f('ix_deletion_events_replicated_at'), table_name='deletion_events')
    op.drop_index(op.f('ix_deletion_events_uid'), table_name='deletion_events')
    op.drop_table('deletion_events')
