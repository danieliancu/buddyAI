"""The account row lock shared by AI admission and every commercial writer.

Lock order, everywhere: account row -> subscriptions / top-ups -> usage_operations (ascending id) ->
usage_records / usage_notices. Taking the account row first means an admission never decides on a budget
that a top-up, refund, grant, override or suspension is changing at the same moment (PostgreSQL:
SELECT ... FOR UPDATE, held until the caller's next commit). SQLite has no row locks: its writers are
serialised by the database write lock, and admissions run in BEGIN IMMEDIATE transactions.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import Account
from app.db.session import is_postgres


def lock_account(db: Session, account_id: int | None, skip_locked: bool = False) -> Account | None:
    if account_id is None:
        return None
    q = select(Account).where(Account.id == account_id)
    if is_postgres():
        q = q.with_for_update(skip_locked=skip_locked)
    return db.exec(q).first()
