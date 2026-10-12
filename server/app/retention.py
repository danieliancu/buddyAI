"""Data retention: one place that says how long each kind of data is kept, and the job that applies it.

Periods (settings, environment variables BUDDYAI_<NAME>; 0 means "keep"):

- conversation_days (`conversation_retention_days`, default 0): conversation history (turns: what was said
  and answered) and the conversations that group them. 0 = kept until the user deletes a watch's history or
  the account. When set, older turns are deleted; their usage records stay (accounting) without the link.
- incident_days (`incident_retention_days`, default 90): ola Diagnostics incidents and their events
  (applied by app/incidents/service.py).
- voice_ops_days (`voice_ops_retention_days`, default 7): voice-change idempotency records (a short summary
  of a change made by voice, used to recognise technical retries).
- memory_superseded_days (`memory_superseded_days`, default 90): memories replaced by a newer version, kept
  for a while so a correction can be checked, then deleted (applied by app/memory/jobs.py).
- memory_jobs_days (`memory_jobs_days`, default 7): finished or abandoned memory jobs (app/memory/jobs.py).
- audit_days (`audit_retention_days`, default 0): the audit log (who did what to customer data). 0 = kept.
- search_cache_grace_days (`search_cache_grace_days`, default 1): web-search answers are deleted this many
  days after they expire (app/search.py also purges when it stores a new answer).

Not covered here (kept for legal/accounting reasons, see app/accounts.py delete_account): orders, billing
consents, subscriptions, top-ups, revenue events and usage records.

The defaults reproduce the behaviour before this module existed. Changing a period changes what the privacy
notice promises: it needs business and legal approval first.

`retention_loop()` runs `run_retention_once()` every `retention_interval_s` seconds. Each step has its own
transaction and its own error handling (one failure does not stop the others) and only counts are logged.
On PostgreSQL a session advisory lock makes sure only one server instance runs it at a time. Running it
twice in a row deletes nothing the second time.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete as sa_delete, exists, text, update as sa_update
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.db.models import AuditLog, Conversation, Turn, UsageRecord
from app.db.session import get_engine, is_postgres, session_scope

log = logging.getLogger(__name__)

ADVISORY_LOCK_KEY = 0x6F6C6172  # "olar": one retention run across all instances
BATCH = 1000


@dataclass(frozen=True)
class RetentionPolicy:
    conversation_days: int = 0
    incident_days: int = 90
    voice_ops_days: int = 7
    memory_superseded_days: int = 90
    memory_jobs_days: int = 7
    audit_days: int = 0
    search_cache_grace_days: int = 1

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> "RetentionPolicy":
        s = s or get_settings()
        return cls(
            conversation_days=s.conversation_retention_days,
            incident_days=s.incident_retention_days,
            voice_ops_days=s.voice_ops_retention_days,
            memory_superseded_days=s.memory_superseded_days,
            memory_jobs_days=s.memory_jobs_days,
            audit_days=s.audit_retention_days,
            search_cache_grace_days=s.search_cache_grace_days,
        )


# --- steps (each: its own session and transaction; returns the number of rows deleted) ----------------------


def prune_voice_ops(policy: RetentionPolicy, now: datetime) -> int:
    from app.db.repositories import VoiceOpRepo

    if policy.voice_ops_days <= 0:
        return 0
    with session_scope() as db:
        return VoiceOpRepo(db).prune(now, keep_days=policy.voice_ops_days)


def prune_search_cache(policy: RetentionPolicy, now: datetime) -> int:
    from app.search import purge_expired

    with session_scope() as db:
        n = purge_expired(db, now, grace_days=policy.search_cache_grace_days)
        db.commit()
        return n


def delete_turns(db: Session, turn_ids: list[int]) -> None:
    """Delete turns; usage records stay for accounting (link removed), memories stay (link removed).
    The same rules as ConversationRepo.delete_device_history. The caller commits."""
    from app.memory.repo import MemoryRepo

    if not turn_ids:
        return
    MemoryRepo(db).detach_turns(turn_ids)
    db.execute(sa_update(UsageRecord).where(col(UsageRecord.turn_id).in_(turn_ids)).values(turn_id=None))
    db.execute(sa_delete(Turn).where(col(Turn.id).in_(turn_ids)))


def prune_conversations(policy: RetentionPolicy, now: datetime) -> int:
    """Turns older than the period, then conversations left without turns and inactive for as long."""
    from app.memory.repo import MemoryRepo

    if policy.conversation_days <= 0:
        return 0
    cutoff = now - timedelta(days=policy.conversation_days)
    n = 0
    while True:
        with session_scope() as db:
            ids = list(db.exec(select(Turn.id).where(col(Turn.created_at) < cutoff).order_by(Turn.id).limit(BATCH)).all())
            if not ids:
                break
            delete_turns(db, ids)
            db.commit()
            n += len(ids)
    with session_scope() as db:
        conv_ids = list(db.exec(select(Conversation.id).where(
            col(Conversation.last_activity_at) < cutoff,
            ~exists().where(Turn.conversation_id == Conversation.id),
        )).all())
        if conv_ids:
            MemoryRepo(db).detach_conversations(conv_ids)
            db.execute(sa_delete(Conversation).where(col(Conversation.id).in_(conv_ids)))
            db.commit()
    return n


def prune_audit(policy: RetentionPolicy, now: datetime) -> int:
    if policy.audit_days <= 0:
        return 0
    with session_scope() as db:
        res = db.execute(sa_delete(AuditLog).where(col(AuditLog.created_at) < now - timedelta(days=policy.audit_days)))
        db.commit()
        return int(res.rowcount or 0)


def _steps() -> list[tuple[str, Callable[[RetentionPolicy, datetime], int]]]:
    # looked up at call time (tests replace single steps)
    return [
        ("voice_operations", prune_voice_ops),
        ("search_cache", prune_search_cache),
        ("conversations", prune_conversations),
        ("audit_log", prune_audit),
    ]


def _run_steps(policy: RetentionPolicy, now: datetime) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for name, step in _steps():
        try:
            out[name] = step(policy, now)
        except Exception as exc:  # noqa: BLE001 - one failing step must not stop the others
            out[name] = None
            log.warning("retention: %s failed (%s)", name, type(exc).__name__)
    return out


def run_retention_once(policy: RetentionPolicy | None = None, now: datetime | None = None) -> dict[str, int | None] | None:
    """Apply the policy once. Returns rows deleted per step (None = that step failed), or None when another
    instance holds the lock (PostgreSQL)."""
    policy = policy or RetentionPolicy.from_settings()
    now = now or datetime.now(timezone.utc)
    if not is_postgres():
        out = _run_steps(policy, now)
    else:
        with get_engine().connect() as conn:
            got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar()
            conn.commit()
            if not got:
                log.info("retention: another instance is running it")
                return None
            try:
                out = _run_steps(policy, now)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
                conn.commit()
    if any(out.values()):
        log.info("retention: deleted %s", ", ".join(f"{k}={v}" for k, v in out.items() if v))
    return out


async def retention_loop() -> None:
    interval = max(60, get_settings().retention_interval_s)
    await asyncio.sleep(min(60, interval))  # not in the middle of start-up
    while True:
        try:
            await asyncio.to_thread(run_retention_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("retention run failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval)
