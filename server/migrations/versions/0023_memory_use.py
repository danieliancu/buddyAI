"""accounts.memory_use: the customer's separate switch for using memories in conversations (on by default)

Additive only and portable.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-06 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0023'
down_revision: Union[str, None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("memory_use", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("memory_use")
