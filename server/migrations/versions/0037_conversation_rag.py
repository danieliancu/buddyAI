"""conversation RAG: per-watch authorisation, turn vectors, source references, turn markers

Additive only, safe on a live database (docs/CONVERSATION_RAG.md):
- turns.rag_derived / turns.rag_redacted_at (defaults: false / null);
- conversation_rag_authorizations: one row per watch, absent = wearer not declared, off;
- conversation_rag_embeddings: a turn's vector per model key (no text: passages are read from turns);
- conversation_rag_sources: which earlier turns a turn's reply was built from.

The first foreign keys in the schema with ON DELETE CASCADE: vectors and source references go with their
turns on every deletion path, at database level. PostgreSQL needs pgvector (migration 0022 already requires it).

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0037'
down_revision: Union[str, None] = '0036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
TS = sqlmodel.sql.sqltypes.UTCDateTime


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    if postgres:
        vector_type = sa.types.UserDefinedType()
        vector_type.get_col_spec = lambda **kw: "vector"  # type: ignore[method-assign]
    else:
        vector_type = sa.JSON()

    # Columns first: on SQLite a batch operation may copy the table, which must happen before others point at it.
    with op.batch_alter_table("turns", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rag_derived", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("rag_redacted_at", TS(), nullable=True))

    op.create_table(
        "conversation_rag_authorizations",
        sa.Column("device_id", S(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("wearer", S(length=8), nullable=False),
        sa.Column("state", S(length=8), nullable=False, server_default="off"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notice_version", S(length=16), nullable=True),
        sa.Column("method", S(length=16), nullable=True),
        sa.Column("authorized_at", TS(), nullable=True),
        sa.Column("updated_at", TS(), nullable=False),
        sa.Column("index_lease_owner", S(length=80), nullable=True),
        sa.Column("index_lease_until", TS(), nullable=True),
        sa.Column("index_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("index_retry_after", TS(), nullable=True),
        sa.PrimaryKeyConstraint("device_id"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name="fk_conversation_rag_authorizations_device",
                                ondelete="CASCADE"),
        sa.CheckConstraint("wearer IN ('self', 'other')", name="ck_conversation_rag_authorizations_wearer"),
        sa.CheckConstraint("state IN ('off', 'on')", name="ck_conversation_rag_authorizations_state"),
        # A watch someone else wears is never on (no approved wearer authorisation yet).
        sa.CheckConstraint("state = 'off' OR wearer = 'self'", name="ck_conversation_rag_authorizations_self"),
    )
    op.create_index("ix_conversation_rag_authorizations_account_id", "conversation_rag_authorizations", ["account_id"])

    op.create_table(
        "conversation_rag_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("device_id", S(length=64), nullable=False),
        sa.Column("model_key", S(length=96), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("text_hash", S(length=64), nullable=False),
        sa.Column("embedding", vector_type, nullable=False),
        sa.Column("created_at", TS(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], name="fk_conversation_rag_embeddings_turn",
                                ondelete="CASCADE"),
        sa.UniqueConstraint("turn_id", "model_key", name="uq_conversation_rag_embeddings_model"),
    )
    op.create_index("ix_conversation_rag_embeddings_scope", "conversation_rag_embeddings",
                    ["device_id", "account_id", "model_key"])
    if postgres:
        op.execute("ALTER TABLE conversation_rag_embeddings ADD CONSTRAINT ck_conversation_rag_embeddings_size "
                   "CHECK (vector_dims(embedding) = dims)")

    op.create_table(
        "conversation_rag_sources",
        sa.Column("consumer_turn_id", sa.Integer(), nullable=False),
        sa.Column("source_turn_id", sa.Integer(), nullable=False),
        sa.Column("created_at", TS(), nullable=False),
        sa.PrimaryKeyConstraint("consumer_turn_id", "source_turn_id"),
        sa.ForeignKeyConstraint(["consumer_turn_id"], ["turns.id"], name="fk_conversation_rag_sources_consumer",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_turn_id"], ["turns.id"], name="fk_conversation_rag_sources_source",
                                ondelete="CASCADE"),
    )
    op.create_index("ix_conversation_rag_sources_source_turn_id", "conversation_rag_sources", ["source_turn_id"])


def downgrade() -> None:
    # Derived data only (vectors, references, opt-ins): the conversations themselves are untouched.
    op.drop_index("ix_conversation_rag_sources_source_turn_id", table_name="conversation_rag_sources")
    op.drop_table("conversation_rag_sources")
    op.drop_index("ix_conversation_rag_embeddings_scope", table_name="conversation_rag_embeddings")
    op.drop_table("conversation_rag_embeddings")
    op.drop_index("ix_conversation_rag_authorizations_account_id", table_name="conversation_rag_authorizations")
    op.drop_table("conversation_rag_authorizations")
    with op.batch_alter_table("turns", schema=None) as batch_op:
        batch_op.drop_column("rag_redacted_at")
        batch_op.drop_column("rag_derived")
