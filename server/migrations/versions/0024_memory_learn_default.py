"""accounts.memory_learn: learning from conversations is on by default (the customer can switch it off)

Existing accounts are switched on too: until now nobody could have chosen it (the Memory page had only just
appeared with it off), so this is the default, not an overridden choice.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-06 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0024'
down_revision: Union[str, None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column("memory_learn", existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.true())
    op.execute(sa.text("UPDATE accounts SET memory_learn = TRUE"))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.alter_column("memory_learn", existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.false())
