"""customer accounts: accounts, auth tokens, audit log, device/persona ownership

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_OWNER_EMAIL = "owner@buddyai.local"


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(length=254), nullable=False),
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
        sa.Column("country", sqlmodel.sql.sqltypes.AutoString(length=2), nullable=True),
        sa.Column("email_verified_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("last_login_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_accounts_email", "accounts", ["email"], unique=True)
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("expires_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("used_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_tokens_account_id", "auth_tokens", ["account_id"])
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor", sqlmodel.sql.sqltypes.AutoString(length=80), nullable=False),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("detail", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_account_id", "audit_log", ["account_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("account_id", sa.Integer(), nullable=True))
        batch.create_index("ix_devices_account_id", ["account_id"])
        batch.create_foreign_key("fk_devices_account_id", "accounts", ["account_id"], ["id"])
    with op.batch_alter_table("personas") as batch:
        batch.add_column(sa.Column("account_id", sa.Integer(), nullable=True))
        batch.create_index("ix_personas_account_id", ["account_id"])
        batch.create_foreign_key("fk_personas_account_id", "accounts", ["account_id"], ["id"])

    for table in ("turns", "usage_records"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("account_id", sa.Integer(), nullable=True))
            batch.create_index(f"ix_{table}_account_id", ["account_id"])

    # Data: watches that already exist belong to a default owner account (the operator can
    # reassign them). Personas that already exist stay system-wide (account_id NULL).
    conn = op.get_bind()
    has_devices = conn.execute(sa.text("SELECT COUNT(*) FROM devices WHERE token_hash IS NOT NULL")).scalar()
    if has_devices:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute(
            sa.text(
                "INSERT INTO accounts (email, password_hash, name, country, email_verified_at, status, "
                "session_version, created_at) VALUES (:e, NULL, :n, NULL, :now, 'active', 1, :now)"
            ),
            {"e": DEFAULT_OWNER_EMAIL, "n": "Owner", "now": now},
        )
        owner_id = conn.execute(
            sa.text("SELECT id FROM accounts WHERE email = :e"), {"e": DEFAULT_OWNER_EMAIL}
        ).scalar()
        conn.execute(sa.text("UPDATE devices SET account_id = :a WHERE token_hash IS NOT NULL"), {"a": owner_id})
        for table in ("turns", "usage_records"):
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET account_id = :a WHERE device_id IN "
                    "(SELECT id FROM devices WHERE account_id = :a)"
                ),
                {"a": owner_id},
            )


def downgrade() -> None:
    for table in ("usage_records", "turns"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_account_id")
            batch.drop_column("account_id")
    with op.batch_alter_table("personas") as batch:
        batch.drop_constraint("fk_personas_account_id", type_="foreignkey")
        batch.drop_index("ix_personas_account_id")
        batch.drop_column("account_id")
    with op.batch_alter_table("devices") as batch:
        batch.drop_constraint("fk_devices_account_id", type_="foreignkey")
        batch.drop_index("ix_devices_account_id")
        batch.drop_column("account_id")
    op.drop_table("audit_log")
    op.drop_table("auth_tokens")
    op.drop_index("ix_accounts_email", table_name="accounts")
    op.drop_table("accounts")
