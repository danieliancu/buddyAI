"""ola Care: 1,000 AI interactions per period (enforced) + internal AI cost monitoring

usage_operations.interaction, billing_settings interaction / cost-threshold columns,
accounts.interaction_limit_override, topups.interactions, cost_alerts

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-08 15:00:00.000000

Safe on a live database. Historical operations keep interaction = NULL: they were never counted as
interactions and nothing is reconstructed for them, so every account starts its count at the first operation
settled after this migration. The money allowance columns stay (no longer enforced).

Two data changes, both so the new allowance starts clean:
- top-ups already bought get interactions = the plan's top-up size (250): fixed now, so a later change of the
  setting never resizes a purchase retroactively (their allowance_pence stays for history);
- usage notices recorded under the money allowance are deleted: they described money, not interactions, and
  would otherwise show "1,000 interactions reached" to an account that has just started counting, or stop the
  real 80/95/100 % notices of this period. (A downgrade does not bring them back: they are transient.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0027'
down_revision: Union[str, None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
TS = sqlmodel.sql.sqltypes.UTCDateTime
COUNTED = "interaction = true"


def upgrade() -> None:
    with op.batch_alter_table('usage_operations') as b:
        b.add_column(sa.Column('interaction', sa.Boolean(), nullable=True))
    op.create_index('ix_usage_ops_interactions', 'usage_operations', ['account_id', 'period_key'],
                    postgresql_where=sa.text(COUNTED), sqlite_where=sa.text(COUNTED))

    with op.batch_alter_table('billing_settings') as b:
        b.add_column(sa.Column('interaction_limit', sa.Integer(), nullable=False, server_default='1000'))
        b.add_column(sa.Column('topup_interactions', sa.Integer(), nullable=False, server_default='250'))
        b.add_column(sa.Column('cost_warn_pence', sa.Integer(), nullable=False, server_default='200'))
        b.add_column(sa.Column('cost_target_pence', sa.Integer(), nullable=False, server_default='250'))
        b.add_column(sa.Column('cost_critical_pence', sa.Integer(), nullable=False, server_default='500'))

    with op.batch_alter_table('accounts') as b:
        b.add_column(sa.Column('interaction_limit_override', sa.Integer(), nullable=True))

    with op.batch_alter_table('topups') as b:
        b.add_column(sa.Column('interactions', sa.Integer(), nullable=True))

    op.create_table(
        'cost_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('period_key', S(length=80), nullable=False),
        sa.Column('level', S(length=16), nullable=False),
        sa.Column('cost_micro', sa.BigInteger(), nullable=False),
        sa.Column('created_at', TS(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'period_key', 'level', name='uq_cost_alert'),
    )
    op.create_index('ix_cost_alerts_account_id', 'cost_alerts', ['account_id'])

    op.execute("UPDATE topups SET interactions = COALESCE((SELECT topup_interactions FROM billing_settings WHERE id = 1), 250) "
               "WHERE interactions IS NULL")
    op.execute("DELETE FROM usage_notices")


def downgrade() -> None:
    op.drop_index('ix_cost_alerts_account_id', table_name='cost_alerts')
    op.drop_table('cost_alerts')
    with op.batch_alter_table('topups') as b:
        b.drop_column('interactions')
    with op.batch_alter_table('accounts') as b:
        b.drop_column('interaction_limit_override')
    with op.batch_alter_table('billing_settings') as b:
        for c in ('cost_critical_pence', 'cost_target_pence', 'cost_warn_pence', 'topup_interactions',
                  'interaction_limit'):
            b.drop_column(c)
    op.drop_index('ix_usage_ops_interactions', table_name='usage_operations')
    with op.batch_alter_table('usage_operations') as b:
        b.drop_column('interaction')
