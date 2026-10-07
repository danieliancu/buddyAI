"""watch-only checkout + ola Care activated at pairing: care_activations, billing_consents,
orders.checkout_flow, accounts.setup_platform

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-07 12:00:00.000000

Additive only, safe on a live database: new tables, new nullable columns. Existing orders keep
checkout_flow = NULL (the legacy bundle whose trial started at purchase) and existing accounts get no
care_activations row, so pairing never starts a second trial for them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0025'
down_revision: Union[str, None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
T = sqlmodel.sql.sqltypes.UTCDateTime


def upgrade() -> None:
    op.create_table(
        'billing_consents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('stripe_checkout_session_id', S(length=255), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('stripe_customer_id', S(length=64), nullable=True),
        sa.Column('stripe_payment_method_id', S(length=255), nullable=True),
        sa.Column('status', S(length=16), nullable=False),
        sa.Column('terms_version', S(length=32), nullable=False),
        sa.Column('terms_text', S(), nullable=False),
        sa.Column('terms_sha256', S(length=64), nullable=False),
        sa.Column('amount_minor', sa.Integer(), nullable=False),
        sa.Column('currency', S(length=3), nullable=False),
        sa.Column('interval', S(length=8), nullable=False),
        sa.Column('trial_days', sa.Integer(), nullable=False),
        sa.Column('trial_start_rule', S(length=16), nullable=False),
        sa.Column('site_accepted_at', T(), nullable=False),
        sa.Column('site_ip', S(length=64), nullable=False),
        sa.Column('site_user_agent', S(length=300), nullable=False),
        sa.Column('stripe_tos_consent', S(length=16), nullable=True),
        sa.Column('stripe_consent_at', T(), nullable=True),
        sa.Column('created_at', T(), nullable=False),
        sa.Column('updated_at', T(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_billing_consents_stripe_checkout_session_id', 'billing_consents', ['stripe_checkout_session_id'], unique=True)
    op.create_index('ix_billing_consents_account_id', 'billing_consents', ['account_id'], unique=False)
    op.create_index('ix_billing_consents_order_id', 'billing_consents', ['order_id'], unique=False)

    op.create_table(
        'care_activations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('consent_id', sa.Integer(), nullable=True),
        sa.Column('stripe_customer_id', S(length=64), nullable=False),
        sa.Column('payment_method_id', S(length=255), nullable=True),
        sa.Column('currency', S(length=3), nullable=False),
        sa.Column('status', S(length=20), nullable=False),
        sa.Column('reason', S(length=40), nullable=False),
        sa.Column('stripe_subscription_id', S(length=255), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error_code', S(length=40), nullable=False),
        sa.Column('lease_until', T(), nullable=True),
        sa.Column('next_retry_at', T(), nullable=True),
        sa.Column('activated_at', T(), nullable=True),
        sa.Column('created_at', T(), nullable=False),
        sa.Column('updated_at', T(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stripe_subscription_id', name='uq_care_activations_subscription'),
    )
    op.create_index('ix_care_activations_account_id', 'care_activations', ['account_id'], unique=True)
    op.create_index('ix_care_activations_order_id', 'care_activations', ['order_id'], unique=False)

    with op.batch_alter_table('orders') as batch:
        batch.add_column(sa.Column('checkout_flow', S(length=16), nullable=True))
    with op.batch_alter_table('accounts') as batch:
        batch.add_column(sa.Column('setup_platform', S(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('setup_platform')
    with op.batch_alter_table('orders') as batch:
        batch.drop_column('checkout_flow')
    op.drop_index('ix_care_activations_order_id', table_name='care_activations')
    op.drop_index('ix_care_activations_account_id', table_name='care_activations')
    op.drop_table('care_activations')
    op.drop_index('ix_billing_consents_order_id', table_name='billing_consents')
    op.drop_index('ix_billing_consents_account_id', table_name='billing_consents')
    op.drop_index('ix_billing_consents_stripe_checkout_session_id', table_name='billing_consents')
    op.drop_table('billing_consents')
