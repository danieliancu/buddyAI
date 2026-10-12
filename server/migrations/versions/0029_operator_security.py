"""Operator security: roles, revocable sessions, disabled flag, TOTP two-factor authentication

admin_users gains:
- role ('admin' | 'support'); existing operators become 'admin' so they keep full access,
- session_version (bumped to sign an operator out everywhere),
- disabled (a disabled operator cannot sign in and every session stops at once),
- mfa_enabled, totp_secret_enc (Fernet-encrypted TOTP secret, app/mfa.py) and recovery_codes
  (JSON list of bcrypt hashes of the unused recovery codes).

Additive only, safe on a live database. Downgrade drops the new columns (roles and two-factor settings are lost).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-12 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0029'
down_revision: Union[str, None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    with op.batch_alter_table('admin_users') as b:
        b.add_column(sa.Column('role', S(length=20), nullable=False, server_default='admin'))
        b.add_column(sa.Column('session_version', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('totp_secret_enc', sa.Text(), nullable=True))
        b.add_column(sa.Column('recovery_codes', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('admin_users') as b:
        for name in ('recovery_codes', 'totp_secret_enc', 'mfa_enabled', 'disabled', 'session_version', 'role'):
            b.drop_column(name)
