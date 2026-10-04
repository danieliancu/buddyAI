"""pricing_rules.unit: 16 -> 24 characters ("cached_input_token" is 18; PostgreSQL enforces the length, SQLite
does not - the default pricing could not be seeded on PostgreSQL)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-04 13:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlmodel


revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('pricing_rules', schema=None) as batch_op:
        batch_op.alter_column(
            'unit',
            existing_type=sqlmodel.sql.sqltypes.AutoString(length=16),
            type_=sqlmodel.sql.sqltypes.AutoString(length=24),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Narrowing back to 16 would fail (and lose data) once "cached_input_token" rows exist: keep 24.
    pass
