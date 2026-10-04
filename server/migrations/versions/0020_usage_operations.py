"""usage_operations: every AI-consuming operation (admission, reservation, lease, settlement) in the database,
so several server processes share one budget; usage_records get the operation, a dedup key and the period key

Additive only: existing usage, periods, subscriptions and top-ups stay as they are; no operations are invented
for history (legacy usage rows keep period_key NULL and are counted by their created_at window).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-04 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0020'
down_revision: Union[str, None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE = "state IN ('reserved', 'running')"
S = sqlmodel.sql.sqltypes.AutoString
TS = sqlmodel.sql.sqltypes.UTCDateTime


def upgrade() -> None:
    op.create_table(
        "usage_operations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("op_uid", S(length=32), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("device_id", S(length=64), nullable=False),
        sa.Column("request_key", S(length=128), nullable=False),
        sa.Column("request_kind", S(length=8), nullable=False),
        sa.Column("request_fingerprint", S(length=64), nullable=False),
        sa.Column("kind", S(length=16), nullable=False),
        sa.Column("period_key", S(length=80), nullable=True),
        sa.Column("period_kind", S(length=16), nullable=True),
        sa.Column("period_start", TS(), nullable=True),
        sa.Column("period_end", TS(), nullable=True),
        sa.Column("source", S(length=16), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("enforced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", S(length=12), nullable=False),
        sa.Column("cost_certainty", S(length=12), nullable=False, server_default="pending"),
        sa.Column("reserved_micro", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recorded_cost_micro", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("billable_cost_micro", sa.BigInteger(), nullable=True),
        sa.Column("unpriced_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billable", sa.Boolean(), nullable=True),
        sa.Column("result_status", S(length=16), nullable=True),
        sa.Column("reason", S(length=32), nullable=True),
        sa.Column("reason_detail", S(length=200), nullable=True),
        sa.Column("owner", S(length=80), nullable=True),
        sa.Column("exec_token", S(length=32), nullable=True),
        sa.Column("accepted_at", TS(), nullable=False),
        sa.Column("lease_expires_at", TS(), nullable=True),
        sa.Column("heartbeat_at", TS(), nullable=True),
        sa.Column("finished_at", TS(), nullable=True),
        sa.Column("last_cost_at", TS(), nullable=True),
        sa.Column("turn_db_id", sa.Integer(), nullable=True),
        sa.Column("created_at", TS(), nullable=False),
        sa.Column("updated_at", TS(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("op_uid", name="uq_usage_operations_op_uid"),
        sa.CheckConstraint("state IN ('reserved','running','settled','cancelled','expired')", name="ck_usage_ops_state"),
        sa.CheckConstraint("cost_certainty IN ('pending','exact','unpriced','uncertain')", name="ck_usage_ops_certainty"),
        sa.CheckConstraint("reserved_micro >= 0", name="ck_usage_ops_reserved_nonneg"),
        sa.CheckConstraint("recorded_cost_micro >= 0", name="ck_usage_ops_recorded_nonneg"),
    )
    # one operation per request of an account (or of an unowned watch)
    op.create_index("uq_usage_ops_account_request", "usage_operations", ["account_id", "request_key"], unique=True,
                    postgresql_where=sa.text("account_id IS NOT NULL"), sqlite_where=sa.text("account_id IS NOT NULL"))
    op.create_index("uq_usage_ops_device_request", "usage_operations", ["device_id", "request_key"], unique=True,
                    postgresql_where=sa.text("account_id IS NULL"), sqlite_where=sa.text("account_id IS NULL"))
    op.create_index("ix_usage_ops_active", "usage_operations", ["account_id", "period_key"],
                    postgresql_where=sa.text(ACTIVE), sqlite_where=sa.text(ACTIVE))
    op.create_index("ix_usage_ops_lease", "usage_operations", ["lease_expires_at"],
                    postgresql_where=sa.text(ACTIVE), sqlite_where=sa.text(ACTIVE))

    with op.batch_alter_table("usage_records") as batch:
        batch.add_column(sa.Column("operation_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("dedup_key", S(length=160), nullable=True))
        batch.add_column(sa.Column("period_key", S(length=80), nullable=True))
        batch.add_column(sa.Column("stage", S(length=16), nullable=True))
        batch.create_foreign_key("fk_usage_records_operation", "usage_operations", ["operation_id"], ["id"])
    op.create_index("uq_usage_records_dedup", "usage_records", ["dedup_key"], unique=True)
    op.create_index("ix_usage_records_operation", "usage_records", ["operation_id"])
    op.create_index("ix_usage_records_account_period", "usage_records", ["account_id", "period_key"])

    with op.batch_alter_table("topups") as batch:
        batch.add_column(sa.Column("period_key", S(length=80), nullable=True))
    with op.batch_alter_table("billing_settings") as batch:
        batch.add_column(sa.Column("admission_paused", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM usage_operations")).scalar():
        # Never delete operations or their costs to go back: run the previous code against this schema
        # instead (it ignores the new columns), see docs/BILLING.md "Rollback".
        raise RuntimeError("usage_operations is not empty: downgrade refused (see docs/BILLING.md)")
    with op.batch_alter_table("billing_settings") as batch:
        batch.drop_column("admission_paused")
    with op.batch_alter_table("topups") as batch:
        batch.drop_column("period_key")
    op.drop_index("ix_usage_records_account_period", table_name="usage_records")
    op.drop_index("ix_usage_records_operation", table_name="usage_records")
    op.drop_index("uq_usage_records_dedup", table_name="usage_records")
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_constraint("fk_usage_records_operation", type_="foreignkey")
        batch.drop_column("stage")
        batch.drop_column("period_key")
        batch.drop_column("dedup_key")
        batch.drop_column("operation_id")
    for ix in ("ix_usage_ops_lease", "ix_usage_ops_active", "uq_usage_ops_device_request", "uq_usage_ops_account_request"):
        op.drop_index(ix, table_name="usage_operations")
    op.drop_table("usage_operations")
