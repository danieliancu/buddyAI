"""Usage threshold notices (default 80 / 95 / 100 % of the account's monthly AI interactions).

Each threshold is recorded once per account and allowance period (table usage_notices): duplicate requests,
reconnections and refused requests never repeat one. The web app shows the highest one not dismissed yet (a
banner at 80/95 %, a dialog at 100 %); the watch gets a short `notice` once, never during a conversation. The
first threshold also sends an email. Internal AI costs never appear here (app/cost_monitor.py).
"""

from __future__ import annotations

import logging
from datetime import timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app import allowance as allowance_mod
from app import billing, email
from app.db.models import Account, UsageNotice, utcnow
from app.db.session import session_scope
from app.plan import get_plan

log = logging.getLogger(__name__)


def level(threshold: int) -> str:
    return "limit" if threshold >= 100 else ("warning" if threshold >= 95 else "info")


def fmt_day(dt) -> str:
    """8 November 2026 (British English, no leading zero)."""
    return f"{dt.day} {dt:%B %Y}"


def watch_text(threshold: int, reset_at=None) -> str:
    """Short text for the watch's notice screen."""
    if threshold >= 100:
        when = f"\nRenews on {reset_at.day} {reset_at:%b}." if reset_at is not None else ""
        return "Monthly AI interactions used up." + when
    return f"You've used {threshold}% of your monthly AI interactions."


def web_text(threshold: int, limit: int, reset_at) -> str:
    """The notice in the customer's words (web app, email)."""
    if threshold >= 100:
        return (f"You've reached your {limit:,} monthly AI interactions. "
                f"Your allowance renews on {fmt_day(reset_at)}.")
    return f"You've used {threshold}% of your monthly AI interactions. Your allowance renews on {fmt_day(reset_at)}."


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def current_state(db: Session, acc: Account):
    """(subscription, allowance, plan) or None when notices do not apply (not enforced / internal / no plan)."""
    plan = get_plan(db)
    if acc.internal or not plan.enforced:
        return None
    sub = billing.active_subscription(db, acc.id)
    if sub is None or not billing.is_entitled(sub):
        return None
    return sub, allowance_mod.compute(db, acc, sub, plan), plan


def record_crossed(db: Session, acc: Account) -> list[int]:
    """Create notices for thresholds reached in this period; returns the new ones (ascending)."""
    state = current_state(db, acc)
    if state is None:
        return []
    _sub, a, plan = state
    existing = set(
        db.exec(
            select(UsageNotice.threshold).where(
                UsageNotice.account_id == acc.id, UsageNotice.period_start == a.period.start
            )
        ).all()
    )
    new: list[int] = []
    for t in plan.thresholds:
        if a.used_pct >= t and t not in existing:
            db.add(UsageNotice(account_id=acc.id, period_start=a.period.start, threshold=t))
            try:
                db.commit()
                new.append(t)
            except IntegrityError:  # recorded concurrently
                db.rollback()
    return new


async def evaluate(account_id: int | None) -> list[int]:
    """Record newly reached thresholds; sends the email for the first threshold of the period."""
    if account_id is None:
        return []
    with session_scope() as db:
        acc = db.get(Account, account_id)
        if acc is None:
            return []
        new = record_crossed(db, acc)
        plan = get_plan(db)
        first = plan.thresholds[0] if plan.thresholds else None
        to = acc.email if first in new else None
        state = current_state(db, acc) if to else None
    if to and state is not None:
        _sub, a, _plan = state
        await email.send(email.allowance_warning(to, web_text(first, a.limit, a.period.end)))
    return new


def pending_web(db: Session, acc: Account) -> UsageNotice | None:
    """The highest threshold of the current period the customer has not dismissed yet."""
    state = current_state(db, acc)
    if state is None:
        return None
    _sub, a, _plan = state
    return db.exec(
        select(UsageNotice)
        .where(
            UsageNotice.account_id == acc.id,
            UsageNotice.period_start == a.period.start,
            col(UsageNotice.dismissed_web_at).is_(None),
        )
        .order_by(col(UsageNotice.threshold).desc())
    ).first()


def dismiss_web(db: Session, acc: Account, threshold: int) -> None:
    """Dismissing a notice also dismisses the lower ones of the same period (they are older news)."""
    state = current_state(db, acc)
    if state is None:
        return
    _sub, a, _plan = state
    for n in db.exec(
        select(UsageNotice).where(
            UsageNotice.account_id == acc.id,
            UsageNotice.period_start == a.period.start,
            UsageNotice.threshold <= threshold,
            col(UsageNotice.dismissed_web_at).is_(None),
        )
    ).all():
        n.dismissed_web_at = utcnow()
        db.add(n)
    db.commit()


def take_watch_notice(account_id: int) -> dict | None:
    """The highest threshold not yet shown on a watch this period, marked as shown (lower ones too)."""
    with session_scope() as db:
        acc = db.get(Account, account_id)
        state = current_state(db, acc) if acc else None
        if state is None:
            return None
        _sub, a, _plan = state
        rows = db.exec(
            select(UsageNotice).where(
                UsageNotice.account_id == account_id,
                UsageNotice.period_start == a.period.start,
                col(UsageNotice.shown_watch_at).is_(None),
            )
        ).all()
        if not rows:
            return None
        top = max(r.threshold for r in rows)
        for r in rows:
            r.shown_watch_at = utcnow()
            db.add(r)
        db.commit()
        return {"level": level(top), "text": watch_text(top, a.period.end), "threshold": top}
