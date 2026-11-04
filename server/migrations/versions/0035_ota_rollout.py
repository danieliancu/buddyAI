"""automatic firmware updates: rollout fields on releases and devices, one row per update attempt

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-03 15:00:00.000000

Existing releases stay in the development stage and existing watches report their channel at the next hello:
nothing is offered until an operator turns the feature on and promotes a release.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0035'
down_revision: Union[str, None] = '0034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('firmware_releases') as b:
        b.add_column(sa.Column('hw_model', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False, server_default=''))
        b.add_column(sa.Column('channel', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False, server_default='dev'))
        b.add_column(sa.Column('stage', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False, server_default='development'))
        b.add_column(sa.Column('paused', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('paused_reason', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False, server_default=''))
        b.add_column(sa.Column('signature_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('signing_evidence', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
    with op.batch_alter_table('devices') as b:
        b.add_column(sa.Column('fw_channel', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False, server_default=''))
        b.add_column(sa.Column('ota_pilot', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'device_ota',
        sa.Column('attempt_id', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('device_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('release_id', sa.Integer(), nullable=False),
        sa.Column('previous_version', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('state', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('innocent', sa.Boolean(), nullable=False),
        sa.Column('reviewed_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('attempt_id'),
    )
    op.create_index(op.f('ix_device_ota_device_id'), 'device_ota', ['device_id'], unique=False)
    op.create_index(op.f('ix_device_ota_release_id'), 'device_ota', ['release_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_device_ota_release_id'), table_name='device_ota')
    op.drop_index(op.f('ix_device_ota_device_id'), table_name='device_ota')
    op.drop_table('device_ota')
    with op.batch_alter_table('devices') as b:
        b.drop_column('ota_pilot')
        b.drop_column('fw_channel')
    with op.batch_alter_table('firmware_releases') as b:
        for name in ('signing_evidence', 'signature_verified', 'paused_reason', 'paused', 'stage', 'channel', 'hw_model'):
            b.drop_column(name)
