"""Reminder delivery: a background loop pushes due reminders to the account's connected watches.

A reminder is marked fired only when at least one watch received it; otherwise it is delivered when a
watch of that account connects (hello), as long as it is less than DELIVERY_WINDOW old. A reminder with
an advance notice (notify_before_min) also fires that many minutes before its start (`early`), as long as
the start is still ahead.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from app.db.models import Item, utcnow
from app.db.repositories import ItemRepo
from app.db.session import session_scope

if TYPE_CHECKING:
    from app.gateway.hub import DeviceHub

log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 15
DELIVERY_WINDOW = timedelta(hours=24)


async def deliver_due(hub: "DeviceHub", account_id: int | None = None) -> int:
    """Fire every due, undelivered reminder (optionally for one account). Returns how many were delivered."""
    now = utcnow()
    with session_scope() as db:
        repo = ItemRepo(db)
        early = repo.due_early(now, account_id=account_id)
        due = repo.due(now, since=now - DELIVERY_WINDOW, account_id=account_id)
    delivered = 0
    accounts: set[int] = set()
    for it in early:
        if not await hub.fire_reminder(it, early=True):
            continue
        with session_scope() as db:
            row = db.get(Item, it.id)
            if row is not None:
                ItemRepo(db).mark_early_fired(row)
        delivered += 1
        accounts.add(it.account_id)
    for it in due:
        if not await hub.fire_reminder(it):
            continue
        with session_scope() as db:
            row = db.get(Item, it.id)
            if row is not None:
                ItemRepo(db).mark_fired(row)
        delivered += 1
        accounts.add(it.account_id)
    for acc in accounts:
        await hub.push_items(acc)
        hub.items_changed(acc)
    return delivered


async def reminder_loop(hub: "DeviceHub", interval_s: float = CHECK_INTERVAL_S) -> None:
    while True:
        try:
            await deliver_due(hub)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("reminder delivery failed")
        await asyncio.sleep(interval_s)
