"""Durable delivery for emails that nothing would send again: webhook emails, operator alerts, price notices.

A webhook event is claimed once and email.send swallows errors, so an email that fails while its event is handled
would be lost. Here the email is first written to a table (in the same commit as the change it reports, where the
caller can), then tried at once. What fails is retried with backoff by retry_loop; a row left behind by a crash is
picked up after its lease. Delivery is at least once: a crash just after the provider accepted a message can send
it twice, and nothing more can be promised over SMTP.

- `dedupe_key` is unique, so a replayed event never queues the same email twice.
- Bodies can hold a one-time link (the welcome email): they and the address are cleared as soon as the row is
  finished. Finished rows stay for the dedupe key only and are pruned by app/retention.py.
- Deleting an account deletes its rows (accounts.delete_account).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app import accounts, email
from app.config import get_settings
from app.db.models import Account, AuditLog, EmailOutbox, Subscription, utcnow
from app.db.session import session_scope

log = logging.getLogger(__name__)

RETRY_MINUTES = (1, 5, 15, 30, 60, 120, 240, 480, 720, 1440)  # about two days, then it gives up
MAX_ATTEMPTS = len(RETRY_MINUTES) + 1
LEASE = timedelta(minutes=2)  # longer than one SMTP attempt: a row being sent is not picked up twice
BATCH = 50  # per pass, so a large send (a price notice to every customer) is spread out
INTERVAL_S = 60.0
KEEP_FINISHED = timedelta(days=90)


def enqueue(
    db: Session, message: email.Email, key: str, account_id: int | None = None, *, commit: bool = True, now_due: bool = False
) -> int | None:
    """Queue an email once per key. Returns the row id, or None if the key already exists. With commit=False the
    row joins the caller's transaction and the caller commits. now_due: nobody is about to try it, the retry loop
    sends it (otherwise the lease keeps the loop away while the caller tries)."""
    if db.exec(select(EmailOutbox.id).where(EmailOutbox.dedupe_key == key)).first() is not None:
        return None
    now = utcnow()
    row = EmailOutbox(dedupe_key=key, account_id=account_id, to_addr=message.to, subject=message.subject[:255],
                      text_body=message.text, html_body=message.html, next_attempt_at=now if now_due else now + LEASE)
    db.add(row)
    try:
        db.flush()
        row_id = row.id
        if commit:
            db.commit()
    except IntegrityError:  # a concurrent delivery queued it
        db.rollback()
        return None
    return row_id


def _finish(row: EmailOutbox, status: str) -> None:
    row.status = status
    row.text_body, row.html_body, row.to_addr = "", None, ""


async def attempt(db: Session, row_id: int) -> bool:
    """Try one queued email now. Never raises for a delivery problem. Returns True if it was sent."""
    row = db.get(EmailOutbox, row_id)
    if row is None or row.status != "pending":
        db.commit()
        return False
    owner = db.get(Account, row.account_id) if row.account_id else None
    if row.account_id and (owner is None or owner.status == "deleted"):
        db.delete(row)
        db.commit()
        return False
    message = email.Email(row.to_addr, row.subject, row.text_body, row.html_body)
    db.commit()  # no transaction stays open while the provider answers
    error: Exception | None = None
    try:
        await email.get_sender().send(message)
    except Exception as exc:  # noqa: BLE001 - whatever the provider does, the row stays to be retried
        error = exc
    row = db.get(EmailOutbox, row_id)
    if row is None:  # the account was deleted meanwhile
        db.commit()
        return error is None
    now = utcnow()
    row.attempts += 1
    if error is None:
        _finish(row, "sent")
        row.sent_at, row.last_error = now, ""
    else:
        row.last_error = type(error).__name__[:64]
        if row.attempts >= MAX_ATTEMPTS:
            _finish(row, "gave_up")
            log.error("email %s (%s) given up after %s attempts", row.id, row.dedupe_key, row.attempts)
            db.add(AuditLog(actor="system", action="email.gave_up", account_id=row.account_id,
                            detail=f"{row.dedupe_key}: {row.subject}"[:2000]))
        else:
            row.next_attempt_at = now + timedelta(minutes=RETRY_MINUTES[row.attempts - 1])
            log.warning("email %s (%s) failed (%s), attempt %s", row.id, row.dedupe_key, row.last_error, row.attempts)
    db.add(row)
    db.commit()
    return error is None


async def send(db: Session, message: email.Email, key: str, account_id: int | None = None) -> None:
    """Queue, commit, then try now. A replay of the same key does nothing."""
    row_id = enqueue(db, message, key, account_id)
    if row_id is not None:
        await attempt(db, row_id)


def queue_operator_alert(db: Session, key: str, subject: str, lines: list[str]) -> int | None:
    """An alert for the operator: always in the log; by email when BUDDYAI_OPERATOR_ALERT_EMAIL is set. Joins the
    caller's transaction. Keep customer details out of it, ids only (the subject also goes to the log)."""
    log.error("operator alert: %s", subject)
    to = get_settings().operator_alert_email.strip()
    if not to:
        log.error("BUDDYAI_OPERATOR_ALERT_EMAIL is not set: the alert above is only in the log and the audit log")
        return None
    return enqueue(db, email.operator_alert(to, subject, lines), key, commit=False)


async def deliver_due(now: datetime | None = None) -> int:
    """One pass of the retry loop. Returns how many rows it tried. A conditional update claims each row, so several
    server processes can run this."""
    now = now or utcnow()
    tried = 0
    with session_scope() as db:
        ids = db.exec(
            select(EmailOutbox.id)
            .where(EmailOutbox.status == "pending", col(EmailOutbox.next_attempt_at) <= now)
            .order_by(col(EmailOutbox.next_attempt_at))
            .limit(BATCH)
        ).all()
        for row_id in ids:
            claimed = db.exec(  # type: ignore[call-overload]
                update(EmailOutbox)
                .where(col(EmailOutbox.id) == row_id, col(EmailOutbox.status) == "pending",
                       col(EmailOutbox.next_attempt_at) <= now)
                .values(next_attempt_at=now + LEASE)
            )
            db.commit()
            if claimed.rowcount == 1:
                tried += 1
                await attempt(db, row_id)
    return tried


async def retry_loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await deliver_due()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("email retry pass failed (%s)", type(exc).__name__)


# --- operator-triggered: price change notice ----------------------------------------------------------------

PRICE_NOTICE_DAYS = 30  # site/src/pages/legal/subscription-terms.astro, section 5
PRICE_NOTICE_STATUSES = ("trialing", "active", "past_due")


def price_notice_recipients(db: Session) -> list[Account]:
    """Accounts with a live Stripe subscription that will renew (not those already set to cancel)."""
    ids = set(db.exec(select(Subscription.account_id).where(
        Subscription.source == "stripe", col(Subscription.status).in_(PRICE_NOTICE_STATUSES),
        col(Subscription.cancel_at_period_end).is_(False), col(Subscription.account_id).is_not(None))).all())
    if not ids:
        return []
    return list(db.exec(select(Account).where(col(Account.id).in_(ids), Account.status == "active")
                        .order_by(col(Account.id))).all())


def queue_price_notice(db: Session, old_minor: int, new_minor: int, effective: datetime, actor: str,
                       currency: str = "gbp", now: datetime | None = None) -> int:
    """Queue the price change email to every recipient; the retry loop of the running server sends them. Running it
    again with the same date and price only reaches those not queued yet. Changes nothing in Stripe. Raises
    ValueError if the notice is shorter than the terms promise. Returns how many were queued."""
    now = now or utcnow()
    effective = effective if effective.tzinfo else effective.replace(tzinfo=timezone.utc)
    if effective - now < timedelta(days=PRICE_NOTICE_DAYS):
        raise ValueError(f"the new price must start at least {PRICE_NOTICE_DAYS} days from now (subscription terms, section 5)")
    if old_minor <= 0 or new_minor <= 0 or old_minor == new_minor:
        raise ValueError("the old and new prices must be positive and different")
    queued = 0
    day = f"{effective:%Y-%m-%d}"
    for acc in price_notice_recipients(db):
        message = email.care_price_change(acc.email, old_minor, new_minor, currency, effective)
        if enqueue(db, message, f"price-change:{day}:{new_minor}:{acc.id}", acc.id, now_due=True) is not None:
            queued += 1
    accounts.audit(db, actor, "price_change.notice", detail=f"{old_minor} to {new_minor} {currency} from {day}: {queued} queued")
    return queued
