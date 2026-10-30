"""search_provider_usage: monthly request counter of paid search providers (Brave budget)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-30 12:00:00.000000

A new, empty table; nothing existing changes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0032'
down_revision: Union[str, None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        'search_provider_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('month', S(length=7), nullable=False),
        sa.Column('provider', S(length=16), nullable=False),
        sa.Column('requests', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('month', 'provider', name='uq_search_provider_month'),
    )


def downgrade() -> None:
    op.drop_table('search_provider_usage')
