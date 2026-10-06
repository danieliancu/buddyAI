"""memory_embeddings: memory vectors (pgvector on PostgreSQL, JSON on SQLite)

PostgreSQL needs the pgvector extension files in the database image (deploy/postgres.Dockerfile). If
they are missing this migration stops with a clear error before changing anything (the DDL runs in one
transaction), so the database stays at 0021 and the previous release keeps working.

The vector column has no fixed size: each row records its model (model_key) and size (dims, checked),
and searches only ever compare vectors of one model_key. No approximate index: searches are always
within one account's few hundred memories (exact search).

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-06 16:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0022'
down_revision: Union[str, None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
TS = sqlmodel.sql.sqltypes.UTCDateTime


def upgrade() -> None:
    conn = op.get_bind()
    postgres = conn.dialect.name == "postgresql"
    if postgres:
        available = conn.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar()
        if not available:
            raise RuntimeError(
                "pgvector is not installed in this PostgreSQL image: deploy deploy/postgres.Dockerfile first "
                "(deploy/README.md, 'Memory: pgvector'). Nothing was changed."
            )
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        vector_type = sa.types.UserDefinedType()
        vector_type.get_col_spec = lambda **kw: "vector"  # type: ignore[method-assign]
    else:
        vector_type = sa.JSON()
    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("model_key", S(length=96), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("text_hash", S(length=64), nullable=False),
        sa.Column("embedding", vector_type, nullable=False),
        sa.Column("created_at", TS(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], name="fk_memory_embeddings_memory"),
        sa.UniqueConstraint("memory_id", "model_key", name="uq_memory_embeddings_model"),
        sa.CheckConstraint("dims > 0", name="ck_memory_embeddings_dims"),
    )
    op.create_index("ix_memory_embeddings_account_model", "memory_embeddings", ["account_id", "model_key"])
    if postgres:
        op.execute("ALTER TABLE memory_embeddings ADD CONSTRAINT ck_memory_embeddings_size "
                   "CHECK (vector_dims(embedding) = dims)")


def downgrade() -> None:
    # Vectors are derived data (recomputed from the memories); the extension stays installed.
    op.drop_index("ix_memory_embeddings_account_model", table_name="memory_embeddings")
    op.drop_table("memory_embeddings")
