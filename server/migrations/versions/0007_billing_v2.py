"""billing v2: plan settings, complimentary subscriptions, top-ups, revenue, usage notices,
search cache, GBP costs frozen per usage record

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30 15:00:00.000000

Data steps (safe to run on a live database):
- usage_records: cost_micro_gbp/fx_rate backfilled with the current display rate (GBP only; other
  display currencies fall back to 0.75), mock rows flagged from the provider name.
- accounts.internal: set only for the built-in operator stock account created by 0002
  (owner@buddyai.local) when it has no Stripe customer and no orders.
- billing_settings: one row with enforcement OFF (behaviour unchanged until the operator enables it).
"""
import json
import os
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
T = sqlmodel.sql.sqltypes.UTCDateTime


def _usd_gbp_rate() -> str:
    """The operator's current USD->GBP display rate, if the display currency is GBP."""
    try:
        from app.config import get_settings
        s = get_settings()
        cur, rate = s.display_currency, s.usd_to_display_rate
        f = Path(s.data_dir) / "currency.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            cur, rate = data.get("currency", cur), data.get("usd_rate", rate)
        return str(rate) if str(cur).upper() == "GBP" else "0.75"
    except Exception:  # noqa: BLE001 - migrations must not fail on settings
        return "0.75"


def upgrade() -> None:
    op.create_table('billing_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('enforce', sa.Boolean(), nullable=False),
    sa.Column('care_price_pence', sa.Integer(), nullable=False),
    sa.Column('care_allowance_pence', sa.Integer(), nullable=False),
    sa.Column('topup_price_pence', sa.Integer(), nullable=False),
    sa.Column('topup_allowance_pence', sa.Integer(), nullable=False),
    sa.Column('thresholds', S(length=40), nullable=False),
    sa.Column('usd_gbp_rate', S(length=16), nullable=False),
    sa.Column('reserve_pence', sa.Integer(), nullable=False),
    sa.Column('updated_at', T(), nullable=False),
    sa.Column('updated_by', S(length=80), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('topups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('stripe_session_id', S(length=255), nullable=True),
    sa.Column('stripe_payment_intent', S(length=255), nullable=True),
    sa.Column('amount_pence', sa.Integer(), nullable=False),
    sa.Column('allowance_pence', sa.Integer(), nullable=False),
    sa.Column('status', S(length=16), nullable=False),
    sa.Column('period_start', T(), nullable=False),
    sa.Column('period_end', T(), nullable=False),
    sa.Column('created_at', T(), nullable=False),
    sa.Column('paid_at', T(), nullable=True),
    sa.Column('refunded_at', T(), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_topups_account_id', 'topups', ['account_id'], unique=False)
    op.create_index('ix_topups_stripe_session_id', 'topups', ['stripe_session_id'], unique=True)
    op.create_index('ix_topups_stripe_payment_intent', 'topups', ['stripe_payment_intent'], unique=False)
    op.create_table('revenue_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', S(length=255), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('kind', S(length=16), nullable=False),
    sa.Column('amount_pence', sa.Integer(), nullable=False),
    sa.Column('tax_pence', sa.Integer(), nullable=False),
    sa.Column('currency', S(length=3), nullable=False),
    sa.Column('created_at', T(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_revenue_events_source_id', 'revenue_events', ['source_id'], unique=True)
    op.create_index('ix_revenue_events_account_id', 'revenue_events', ['account_id'], unique=False)
    op.create_index('ix_revenue_events_created_at', 'revenue_events', ['created_at'], unique=False)
    op.create_table('usage_notices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('period_start', T(), nullable=False),
    sa.Column('threshold', sa.Integer(), nullable=False),
    sa.Column('created_at', T(), nullable=False),
    sa.Column('dismissed_web_at', T(), nullable=True),
    sa.Column('shown_watch_at', T(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'period_start', 'threshold', name='uq_usage_notice')
    )
    op.create_index('ix_usage_notices_account_id', 'usage_notices', ['account_id'], unique=False)
    op.create_table('search_cache',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', S(length=64), nullable=False),
    sa.Column('scope', S(length=32), nullable=False),
    sa.Column('category', S(length=16), nullable=False),
    sa.Column('location', S(length=80), nullable=False),
    sa.Column('language', S(length=8), nullable=False),
    sa.Column('query', S(length=300), nullable=False),
    sa.Column('answer', S(), nullable=False),
    sa.Column('fetched_at', T(), nullable=False),
    sa.Column('expires_at', T(), nullable=False),
    sa.Column('hits', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_search_cache_key', 'search_cache', ['key'], unique=True)
    op.create_index('ix_search_cache_expires_at', 'search_cache', ['expires_at'], unique=False)

    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('internal', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', S(length=16), nullable=False, server_default='stripe'))
        batch_op.add_column(sa.Column('current_period_start', T(), nullable=True))
        batch_op.add_column(sa.Column('allowance_pence', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('granted_by', S(length=80), nullable=True))
        batch_op.add_column(sa.Column('note', S(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('created_at', T(), nullable=True))
        batch_op.alter_column('stripe_subscription_id', existing_type=S(length=255), nullable=True)
        batch_op.alter_column('stripe_customer_id', existing_type=S(length=64), nullable=True)

    with op.batch_alter_table('usage_records', schema=None) as batch_op:
        batch_op.alter_column('unit', existing_type=S(length=16), type_=S(length=24), existing_nullable=False)
        batch_op.add_column(sa.Column('cost_micro_gbp', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('fx_rate', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('billable', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('mock', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('turn_uid', S(length=40), nullable=True))
        batch_op.add_column(sa.Column('turn_status', S(length=16), nullable=True))
        batch_op.create_index('ix_usage_records_turn_uid', ['turn_uid'], unique=False)

    # --- data ---
    bind = op.get_bind()
    rate = _usd_gbp_rate()
    bind.execute(sa.text(
        "UPDATE usage_records SET mock = CASE WHEN provider LIKE 'mock%' THEN TRUE ELSE FALSE END"
    ))
    bind.execute(sa.text(
        "UPDATE usage_records SET fx_rate = :r, cost_micro_gbp = CAST(ROUND(cost_usd * :r * 1000000) AS BIGINT) "
        "WHERE cost_usd IS NOT NULL"
    ), {"r": float(rate)})
    bind.execute(sa.text(
        "UPDATE usage_records SET turn_uid = 't' || turn_id, "
        "turn_status = (SELECT status FROM turns WHERE turns.id = usage_records.turn_id) "
        "WHERE turn_id IS NOT NULL"
    ))
    bind.execute(sa.text(
        "UPDATE subscriptions SET created_at = updated_at WHERE created_at IS NULL"
    ))
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.alter_column('created_at', existing_type=T(), nullable=False)
    bind.execute(sa.text(
        "UPDATE accounts SET internal = TRUE WHERE email = 'owner@buddyai.local' AND stripe_customer_id IS NULL "
        "AND id NOT IN (SELECT account_id FROM orders WHERE account_id IS NOT NULL)"
    ))
    bind.execute(sa.text(
        "INSERT INTO billing_settings (id, enforce, care_price_pence, care_allowance_pence, topup_price_pence, "
        "topup_allowance_pence, thresholds, usd_gbp_rate, reserve_pence, updated_at, updated_by) "
        "VALUES (1, FALSE, 799, 250, 199, 65, '80,95,100', :r, 3, CURRENT_TIMESTAMP, 'migration')"
    ), {"r": rate})


def downgrade() -> None:
    with op.batch_alter_table('usage_records', schema=None) as batch_op:
        batch_op.drop_index('ix_usage_records_turn_uid')
        batch_op.drop_column('turn_status')
        batch_op.drop_column('turn_uid')
        batch_op.drop_column('mock')
        batch_op.drop_column('billable')
        batch_op.drop_column('fx_rate')
        batch_op.drop_column('cost_micro_gbp')
        batch_op.alter_column('unit', existing_type=S(length=24), type_=S(length=16), existing_nullable=False)
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_column('created_at')
        batch_op.drop_column('note')
        batch_op.drop_column('granted_by')
        batch_op.drop_column('allowance_pence')
        batch_op.drop_column('current_period_start')
        batch_op.drop_column('source')
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('internal')
    op.drop_index('ix_search_cache_expires_at', table_name='search_cache')
    op.drop_index('ix_search_cache_key', table_name='search_cache')
    op.drop_table('search_cache')
    op.drop_index('ix_usage_notices_account_id', table_name='usage_notices')
    op.drop_table('usage_notices')
    op.drop_index('ix_revenue_events_created_at', table_name='revenue_events')
    op.drop_index('ix_revenue_events_account_id', table_name='revenue_events')
    op.drop_index('ix_revenue_events_source_id', table_name='revenue_events')
    op.drop_table('revenue_events')
    op.drop_index('ix_topups_stripe_payment_intent', table_name='topups')
    op.drop_index('ix_topups_stripe_session_id', table_name='topups')
    op.drop_index('ix_topups_account_id', table_name='topups')
    op.drop_table('topups')
    op.drop_table('billing_settings')
