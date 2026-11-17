"""The "your order is on its way" email, through the outbox so a failure is retried and its outcome is visible.

Each send (the first dispatch, and every Resend) is its own outbox row, keyed `order-shipped:{order id}:{n}`;
the operator sees the newest row's outcome next to the order. No column on the order: the outbox already
holds the state (pending -> sent | gave_up) and the last error.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlmodel import Session, col, select

from app import email, email_outbox
from app.db.models import EmailOutbox, Order

PREFIX = "order-shipped:"


def _key(order_id: int) -> str:
    return f"{PREFIX}{order_id}:{uuid.uuid4().hex[:12]}"


async def send_shipped(db: Session, order: Order) -> None:
    """Queue the email and try it now. Never raises for a delivery problem: the outbox retries it."""
    message = email.order_shipped(order.email, order.id, order.carrier, order.tracking_number)
    await email_outbox.send(db, message, _key(order.id), order.account_id)


def _state(row: EmailOutbox) -> dict[str, Any]:
    if row.status == "sent":
        state = "sent"
    elif row.status == "gave_up":
        state = "failed"
    else:
        state = "retrying" if row.attempts else "sending"
    return {"state": state, "at": row.sent_at or row.created_at, "error": row.last_error, "attempts": row.attempts}


def states(db: Session, order_ids: list[int]) -> dict[int, dict[str, Any]]:
    """The newest dispatch email per order: {order id: {state, at, error, attempts}}; orders never emailed are absent."""
    wanted = set(order_ids)
    if not wanted:
        return {}
    out: dict[int, dict[str, Any]] = {}
    rows = db.exec(select(EmailOutbox).where(col(EmailOutbox.dedupe_key).startswith(PREFIX)).order_by(col(EmailOutbox.id))).all()
    for row in rows:  # oldest first: the newest send per order wins
        try:
            order_id = int(row.dedupe_key[len(PREFIX):].split(":", 1)[0])
        except ValueError:
            continue
        if order_id in wanted:
            out[order_id] = _state(row)
    return out


def with_state(db: Session, orders: list[Order]) -> list[dict[str, Any]]:
    """Orders for the operator, each with `ship_email` (None if no dispatch email was ever queued)."""
    found = states(db, [o.id for o in orders if o.id is not None])
    return [o.model_dump() | {"ship_email": found.get(o.id)} for o in orders]
