"""memories: long-term memory of facts the user asked to keep (and, opt-in, learned ones), durable memory jobs,
and the account's memory switches

Additive only and portable (SQLite + PostgreSQL); no extension needed. Vectors come in 0022.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-06 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0021'
down_revision: Union[str, None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
TS = sqlmodel.sql.sqltypes.UTCDateTime
LIVE = "status IN ('active', 'pending')"
OPEN = "state IN ('pending', 'running')"


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", S(length=32), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("device_id", S(length=64), nullable=True),
        sa.Column("kind", S(length=16), nullable=False),
        sa.Column("content", S(length=300), nullable=False),
        sa.Column("content_hash", S(length=64), nullable=False),
        sa.Column("subject", S(length=60), nullable=True),
        sa.Column("attribute", S(length=60), nullable=True),
        sa.Column("origin", S(length=12), nullable=False),
        sa.Column("status", S(length=12), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confirmed_at", TS(), nullable=True),
        sa.Column("sensitivity", S(length=12), nullable=False, server_default="normal"),
        sa.Column("source_turn_id", sa.Integer(), nullable=True),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("valid_until", TS(), nullable=True),
        sa.Column("last_used_at", TS(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS(), nullable=False),
        sa.Column("updated_at", TS(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid", name="uq_memories_uid"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_memories_account"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name="fk_memories_device"),
        sa.ForeignKeyConstraint(["source_turn_id"], ["turns.id"], name="fk_memories_source_turn"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["memories.id"], name="fk_memories_supersedes"),
        sa.CheckConstraint("kind IN ('preference','profile','person','routine','goal','project','other')",
                           name="ck_memories_kind"),
        sa.CheckConstraint("origin IN ('explicit','inferred','web')", name="ck_memories_origin"),
        sa.CheckConstraint("status IN ('active','pending','superseded')", name="ck_memories_status"),
        sa.CheckConstraint("sensitivity IN ('normal','special')", name="ck_memories_sensitivity"),
    )
    op.create_index("ix_memories_account_status", "memories", ["account_id", "status"])
    op.create_index("ix_memories_account_key", "memories", ["account_id", "subject", "attribute"])
    # the same fact is kept once per account and scope while it is live (active or awaiting confirmation)
    op.create_index("uq_memories_live_hash", "memories",
                    ["account_id", sa.text("coalesce(device_id, '')"), "content_hash"], unique=True,
                    postgresql_where=sa.text(LIVE), sqlite_where=sa.text(LIVE))

    op.create_table(
        "memory_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", S(length=32), nullable=False),
        sa.Column("kind", S(length=12), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("upto_turn_id", sa.Integer(), nullable=True),
        sa.Column("state", S(length=12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_after", TS(), nullable=False),
        sa.Column("lease_expires_at", TS(), nullable=True),
        sa.Column("owner", S(length=80), nullable=True),
        sa.Column("last_error", S(length=200), nullable=True),
        sa.Column("created_at", TS(), nullable=False),
        sa.Column("updated_at", TS(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid", name="uq_memory_jobs_uid"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], name="fk_memory_jobs_memory"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name="fk_memory_jobs_conversation"),
        sa.CheckConstraint("kind IN ('embed','extract','purge')", name="ck_memory_jobs_kind"),
        sa.CheckConstraint("state IN ('pending','running','done','failed','dead')", name="ck_memory_jobs_state"),
    )
    op.create_index("ix_memory_jobs_account", "memory_jobs", ["account_id"])
    op.create_index("ix_memory_jobs_due", "memory_jobs", ["run_after"],
                    postgresql_where=sa.text(OPEN), sqlite_where=sa.text(OPEN))
    # enqueueing is an upsert: one open job per memory / conversation and kind
    op.create_index("uq_memory_jobs_open_memory", "memory_jobs", ["kind", "memory_id"], unique=True,
                    postgresql_where=sa.text(f"{OPEN} AND memory_id IS NOT NULL"),
                    sqlite_where=sa.text(f"{OPEN} AND memory_id IS NOT NULL"))
    op.create_index("uq_memory_jobs_open_conversation", "memory_jobs", ["kind", "conversation_id"], unique=True,
                    postgresql_where=sa.text(f"{OPEN} AND conversation_id IS NOT NULL"),
                    sqlite_where=sa.text(f"{OPEN} AND conversation_id IS NOT NULL"))

    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("memory_explicit", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("memory_learn", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM memories")).scalar():
        # Never delete what users asked us to remember to go back: switch the feature off instead
        # (BUDDYAI_MEMORY_ENABLED=false); the previous code ignores these tables.
        raise RuntimeError("memories is not empty: downgrade refused (set BUDDYAI_MEMORY_ENABLED=false instead)")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("memory_learn")
        batch.drop_column("memory_explicit")
    op.drop_table("memory_jobs")
    op.drop_table("memories")
