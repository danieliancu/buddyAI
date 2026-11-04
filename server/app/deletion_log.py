"""Deletions must not come back after a backup is restored.

Every deletion a customer can ask for writes a DeletionEvent in the SAME transaction (an outbox): an event exists
exactly when the deletion does. deploy/backup/sync_deletions.sh copies new events, encrypted, to the off-site
ledger and only then marks them replicated, so it delivers at least once and repeats are dropped by uid. A storage
outage delays the copy, never the customer's deletion (monitor_loop raises the alarm).

After a restore, an event in the ledger but not in the restored database happened after the backup: reconcile()
applies it again through the normal deletion code. restore.sh leaves a marker, and the server refuses to start on
that database until this has been done.

Events hold ids only, never content.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete as sa_delete, func, inspect
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db.models import Account, DeletionEvent, DeletionReconcileRequired, Item, utcnow
from app.db.session import session_scope

log = logging.getLogger(__name__)

ACCOUNT, DEVICE_HISTORY, ITEM, MEMORY, ALL_MEMORIES = "account", "device_history", "item", "memory", "all_memories"
KINDS = (ACCOUNT, DEVICE_HISTORY, ITEM, MEMORY, ALL_MEMORIES)

# Longer than any backup is kept (30 days off-site). An older ledger event cannot be newer than a backup, and is
# ignored; the outbox keeps rows longer than this, so anything younger that a backup already holds is recognised.
LEDGER_MAX_AGE_DAYS = 120
OUTBOX_KEEP_DAYS = 180
STALE_AFTER_S = 900
ALERT_EVERY_S = 3600


def record(db: Session, kind: str, account_id: int | None = None, device_id: str | None = None, target: str = "") -> None:
    """Part of the caller's transaction: the caller commits, or rolls back and leaves no event."""
    db.add(DeletionEvent(kind=kind, account_id=account_id, device_id=device_id, target=target))


def load_ledger(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Decrypted ledger files (one JSON event per line), repeats removed, oldest first."""
    events: dict[str, dict[str, Any]] = {}
    for path in paths:
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    event = json.loads(line)
                    events[event["uid"]] = event
    return sorted(events.values(), key=lambda e: str(e["occurred_at"]))


def _when(event: dict[str, Any]) -> datetime:
    return _utc(datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00")))


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _apply(db: Session, event: dict[str, Any]) -> None:
    from app import accounts
    from app.db.repositories import ConversationRepo, ItemRepo
    from app.memory.repo import MemoryRepo

    kind, account_id = event["kind"], event.get("account_id")
    if kind == ACCOUNT:
        acc = db.get(Account, account_id)
        if acc is not None and acc.status != "deleted":
            # Stripe was told when the account was first deleted; a restored row must not cancel anything again.
            accounts.delete_account(db, acc, detail="re-applied after a restore", cancel_stripe=False)
    elif kind == DEVICE_HISTORY:
        ConversationRepo(db).delete_device_history(event["device_id"])
    elif kind == ITEM:
        it = db.exec(select(Item).where(Item.uid == event["target"])).first()
        if it is not None:
            ItemRepo(db).delete(it)
    elif kind == MEMORY:
        MemoryRepo(db).forget(account_id, event["target"])
    elif kind == ALL_MEMORIES:
        MemoryRepo(db).forget_all(account_id)
    else:
        raise ValueError(f"unknown deletion kind {kind!r}")


def reconcile(db: Session, events: list[dict[str, Any]], dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Apply the events this database does not hold. Safe to run again: an applied event is stored under its uid."""
    limit = (now or utcnow()) - timedelta(days=LEDGER_MAX_AGE_DAYS)
    out: dict[str, Any] = {"applied": 0, "already_in_database": 0, "too_old": 0, "failed": [], "would_apply": []}
    for event in events:
        if _when(event) < limit:
            out["too_old"] += 1
        elif db.exec(select(DeletionEvent.id).where(DeletionEvent.uid == event["uid"])).first() is not None:
            out["already_in_database"] += 1
        elif dry_run:
            out["would_apply"].append(f"{event['kind']} {event.get('account_id') or event.get('device_id')}")
        else:
            try:
                _apply(db, event)
                db.add(DeletionEvent(uid=event["uid"], kind=event["kind"], account_id=event.get("account_id"),
                                     device_id=event.get("device_id"), target=event.get("target") or "",
                                     occurred_at=_when(event), replicated_at=utcnow()))
                db.commit()
                out["applied"] += 1
            except Exception as exc:  # noqa: BLE001 - one failure must not hide the others; the marker stays
                db.rollback()
                log.exception("deletion %s could not be applied", event["uid"])
                out["failed"].append(f"{event['uid']} ({type(exc).__name__})")
    return out


def reconcile_required(db: Session) -> bool:
    if not inspect(db.get_bind()).has_table(DeletionReconcileRequired.__tablename__):
        return False
    return db.exec(select(DeletionReconcileRequired.id)).first() is not None


def clear_required(db: Session) -> None:
    db.execute(sa_delete(DeletionReconcileRequired))
    db.commit()


def refuse_start_if_unreconciled() -> None:
    with session_scope() as db:
        if reconcile_required(db):
            raise RuntimeError(
                "This database was restored from a backup and deletions made after that backup have not been "
                "applied again. Run: python -m app.cli reconcile-deletions (deploy/README.md section 8.5)."
            )


def status(db: Session, now: datetime | None = None) -> dict[str, Any]:
    pending = db.exec(select(func.count()).select_from(DeletionEvent).where(col(DeletionEvent.replicated_at).is_(None))).one()
    oldest = db.exec(select(func.min(DeletionEvent.occurred_at)).where(col(DeletionEvent.replicated_at).is_(None))).one()
    age = int(((now or utcnow()) - _utc(oldest)).total_seconds()) if oldest else 0
    return {"enabled": get_settings().deletion_ledger_enabled, "pending": int(pending), "oldest_pending_s": age}


def prune(now: datetime | None = None) -> int:
    """Old, replicated events (retention step). Unreplicated ones always stay."""
    with session_scope() as db:
        res = db.execute(sa_delete(DeletionEvent).where(
            col(DeletionEvent.replicated_at).is_not(None),
            col(DeletionEvent.occurred_at) < (now or utcnow()) - timedelta(days=OUTBOX_KEEP_DAYS),
        ))
        db.commit()
        return res.rowcount or 0


async def monitor_loop(hub: Any) -> None:
    """With the ledger on, deletions waiting too long to be copied off-site alert the operators."""
    last_alert = 0.0
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(300)
        if not get_settings().deletion_ledger_enabled:
            continue
        try:
            with session_scope() as db:
                state = status(db)
        except Exception as exc:  # noqa: BLE001
            log.warning("deletion ledger check failed (%s)", type(exc).__name__)
            continue
        if state["oldest_pending_s"] > STALE_AFTER_S and loop.time() - last_alert >= ALERT_EVERY_S:
            last_alert = loop.time()
            log.error("deletion ledger: %s events not copied off-site, oldest %s s", state["pending"], state["oldest_pending_s"])
            hub.publish({"type": "deletion_ledger_stale", **state}, operator_only=True)
