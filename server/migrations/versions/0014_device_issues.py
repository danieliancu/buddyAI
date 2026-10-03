"""device_issues: watch reboots, lost connections and interrupted turns (admin Issues tab)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-03 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("severity", sqlmodel.sql.sqltypes.AutoString(length=8), nullable=False),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("fw_version", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_issues_device_id", "device_issues", ["device_id"])
    op.create_index("ix_device_issues_account_id", "device_issues", ["account_id"])
    op.create_index("ix_device_issues_created_at", "device_issues", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_device_issues_created_at", table_name="device_issues")
    op.drop_index("ix_device_issues_account_id", table_name="device_issues")
    op.drop_index("ix_device_issues_device_id", table_name="device_issues")
    op.drop_table("device_issues")
