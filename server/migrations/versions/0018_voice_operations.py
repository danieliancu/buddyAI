"""voice_operations: durable record of every voice change, written in the same transaction as the change
(a technical retry of the same operation returns the stored result instead of running it again)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-04 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0018'
down_revision: Union[str, None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voice_operations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("op_key", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("tool", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(length=300), nullable=False),
        sa.Column("result", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("op_key", name="uq_voice_operations_op_key"),
    )
    op.create_index("ix_voice_operations_account_id", "voice_operations", ["account_id"])
    op.create_index("ix_voice_operations_created_at", "voice_operations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_voice_operations_created_at", table_name="voice_operations")
    op.drop_index("ix_voice_operations_account_id", table_name="voice_operations")
    op.drop_table("voice_operations")
