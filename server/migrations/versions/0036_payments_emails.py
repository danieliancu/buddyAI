"""refund totals on orders, one cancellation email per cancellation, durable webhook emails

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-04 15:00:00.000000

Subscriptions already cancelled (or set to cancel) when this runs count as notified: the first webhook after the
upgrade must not send them a late cancellation email.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0036'
down_revision: Union[str, None] = '0035'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders') as b:
        b.add_column(sa.Column('amount_refunded', sa.Integer(), nullable=False, server_default='0'))
    with op.batch_alter_table('subscriptions') as b:
        b.add_column(sa.Column('cancel_notified_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))
    op.execute("UPDATE subscriptions SET cancel_notified_at = updated_at "
               "WHERE source = 'stripe' AND (status = 'canceled' OR cancel_at_period_end)")
    op.create_table(
        'email_outbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dedupe_key', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('to_addr', sqlmodel.sql.sqltypes.AutoString(length=254), nullable=False),
        sa.Column('subject', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('text_body', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('html_body', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('next_attempt_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column('sent_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_email_outbox_dedupe_key'), 'email_outbox', ['dedupe_key'], unique=True)
    op.create_index(op.f('ix_email_outbox_account_id'), 'email_outbox', ['account_id'], unique=False)
    op.create_index(op.f('ix_email_outbox_status'), 'email_outbox', ['status'], unique=False)
    op.create_index(op.f('ix_email_outbox_next_attempt_at'), 'email_outbox', ['next_attempt_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_outbox_next_attempt_at'), table_name='email_outbox')
    op.drop_index(op.f('ix_email_outbox_status'), table_name='email_outbox')
    op.drop_index(op.f('ix_email_outbox_account_id'), table_name='email_outbox')
    op.drop_index(op.f('ix_email_outbox_dedupe_key'), table_name='email_outbox')
    op.drop_table('email_outbox')
    with op.batch_alter_table('subscriptions') as b:
        b.drop_column('cancel_notified_at')
    with op.batch_alter_table('orders') as b:
        b.drop_column('amount_refunded')
