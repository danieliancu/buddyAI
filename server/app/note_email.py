"""A note sent to the account's own email address: by voice (the item_email tool) or the watch's send button
(`item_email`). Only to the account's own, confirmed address - never anyone else's - and at most
NOTE_EMAILS_PER_HOUR per account (anti-abuse; the counter lives in this process, see app/ratelimit.py)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from app import email, notes_edit
from app.db.models import Account, Item
from app.db.session import session_scope
from app.ratelimit import RateLimiter

log = logging.getLogger("buddyai.note_email")

NOTE_EMAILS_PER_HOUR = 10
_LIMIT = RateLimiter(NOTE_EMAILS_PER_HOUR, 3600)


def send_note(account_id: int, it: Item) -> str:
    """Send note `it` to the account's email. Returns sent | unverified | limit | failed; never raises.
    Blocking (the mail goes out before it returns): call it from a worker thread."""
    with session_scope() as db:
        acc = db.get(Account, account_id)
        to = acc.email if acc is not None and acc.email_verified_at is not None else None
    if to is None:
        return "unverified"
    try:
        _LIMIT.hit(str(account_id))
    except HTTPException:
        return "limit"
    lines = notes_edit.note_lines(it.text)
    mail = email.note_sent(to, lines[0] if lines else "", lines[1:])
    try:
        asyncio.run(email.get_sender().send(mail))
    except Exception:  # noqa: BLE001
        log.exception("note email for account %s failed", account_id)
        return "failed"
    log.info("note %s emailed to the owner of account %s", it.number, account_id)
    return "sent"


def reset_limits() -> None:
    """Tests."""
    _LIMIT._hits.clear()
