"""discount code and amount on orders (watch sold at £119, codes such as VIP from the operator panel)

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-15 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0038'
down_revision: Union[str, None] = '0037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.add_column(sa.Column('amount_discount', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('discount_code', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False,
                               server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.drop_column('discount_code')
        b.drop_column('amount_discount')
