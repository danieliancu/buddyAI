"""Durable memory jobs (table memory_jobs), run by one asyncio loop per server process.

A job is claimed with a lease (owner + lease_expires_at, `FOR UPDATE SKIP LOCKED` on PostgreSQL), like
usage operations: a crash leaves the lease to expire and the job is picked up again. Failures back off
(30 s * 2^n) and end as `dead` after MAX_ATTEMPTS without blocking anything. Errors are stored as their
class name only, never with user content. Handlers are idempotent: an embedding is skipped when its text
hash is current, a job for a deleted memory or conversation does nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete as sa_delete, or_
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db.models import Memory, MemoryEmbedding, MemoryJob, utcnow
from app.db.session import OWNER, is_postgres, session_scope

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
LEASE_S = 120
BATCH = 20
BACKFILL_BATCH = 200
SUPERSEDED_KEEP_DAYS = 90
FINISHED_KEEP_DAYS = 7


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def enqueue(db: Session, kind: str, account_id: int, *, memory_id: int | None = None,
            conversation_id: int | None = None, upto_turn_id: int | None = None,
            run_after: datetime | None = None) -> MemoryJob:
    """Add a job, or move the open one of the same memory / conversation (debounce). Not committed."""
    q = select(MemoryJob).where(MemoryJob.kind == kind, MemoryJob.state == "pending")
    q = q.where(MemoryJob.memory_id == memory_id) if memory_id is not None else q.where(MemoryJob.conversation_id == conversation_id)
    job = db.exec(q).first()
    now = utcnow()
    if job is None:
        job = MemoryJob(kind=kind, account_id=account_id, memory_id=memory_id, conversation_id=conversation_id,
                        created_at=now)
    job.upto_turn_id = upto_turn_id if upto_turn_id is not None else job.upto_turn_id
    job.run_after = run_after or now
    job.updated_at = now
    db.add(job)
    db.flush()
    return job


# --- claiming ------------------------------------------------------------------------------------------


def _claim(limit: int = BATCH) -> list[int]:
    with session_scope() as db:
        now = utcnow()
        q = select(MemoryJob).where(or_(
            (MemoryJob.state == "pending") & (col(MemoryJob.run_after) <= now),
            (MemoryJob.state == "running") & (col(MemoryJob.lease_expires_at) < now),
        )).order_by(MemoryJob.run_after).limit(limit)
        if is_postgres():
            q = q.with_for_update(skip_locked=True)
        jobs = db.exec(q).all()
        for j in jobs:
            j.state, j.owner, j.lease_expires_at, j.updated_at = "running", OWNER, now + timedelta(seconds=LEASE_S), now
            db.add(j)
        db.commit()
        return [j.id for j in jobs]


def _finish(job_id: int, error: BaseException | None) -> None:
    with session_scope() as db:
        j = db.get(MemoryJob, job_id)
        if j is None:
            return
        now = utcnow()
        if error is None:
            j.state, j.last_error = "done", None
        else:
            j.attempts += 1
            j.last_error = type(error).__name__[:200]
            if j.attempts >= MAX_ATTEMPTS:
                j.state = "dead"
                log.warning("memory job dead kind=%s account=%s error=%s", j.kind, j.account_id, j.last_error)
            else:
                j.state = "pending"
                j.run_after = now + timedelta(seconds=30 * 2 ** (j.attempts - 1))
        j.owner, j.lease_expires_at, j.updated_at = None, None, now
        db.add(j)
        db.commit()


# --- handlers ------------------------------------------------------------------------------------------


async def run_embed(router: Any, job: MemoryJob) -> None:
    provider = router.embedding() if router else None
    if provider is None:
        return
    with session_scope() as db:
        m = db.get(Memory, job.memory_id) if job.memory_id else None
        if m is None or m.account_id != job.account_id or m.status == "superseded":
            return
        content, mid, acc = m.content, m.id, m.account_id
        current = db.exec(select(MemoryEmbedding).where(
            MemoryEmbedding.memory_id == mid, MemoryEmbedding.model_key == provider.model_key)).first()
        if current is not None and current.text_hash == text_hash(content):
            return
    res = await provider.embed([content])
    from app import usage_ops
    from app.providers.base import UsageItem

    await asyncio.to_thread(usage_ops.record_free_operation, "memory", "-", acc,
                            [UsageItem("embedding", provider.name, provider.model, "input_token", res.input_tokens)])
    with session_scope() as db:
        m = db.get(Memory, mid)
        if m is None or m.content != content:  # deleted or edited meanwhile: the next job does it
            return
        row = db.exec(select(MemoryEmbedding).where(
            MemoryEmbedding.memory_id == mid, MemoryEmbedding.model_key == provider.model_key)).first()
        row = row or MemoryEmbedding(memory_id=mid, account_id=acc, model_key=provider.model_key, dims=provider.dims,
                                     text_hash="", embedding=[])
        row.embedding, row.text_hash, row.dims, row.created_at = res.vectors[0], text_hash(content), provider.dims, utcnow()
        db.add(row)
        db.commit()


async def run_job(router: Any, job_id: int) -> None:
    with session_scope() as db:
        job = db.get(MemoryJob, job_id)
        if job is None:
            return
        db.expunge(job)
    try:
        if job.kind == "embed":
            if get_settings().memory_embeddings_enabled and await asyncio.to_thread(_vectors_ready):
                await run_embed(router, job)
        elif job.kind == "extract":
            from app.memory.extract import run_extract

            await run_extract(router, job)
        await asyncio.to_thread(_finish, job_id, None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.info("memory job failed kind=%s account=%s error=%s", job.kind, job.account_id, type(exc).__name__)
        await asyncio.to_thread(_finish, job_id, exc)


# --- housekeeping ----------------------------------------------------------------------------------------


def backfill(model_key: str, limit: int = BACKFILL_BATCH) -> int:
    """Memories without a current vector of this model (new, edited, or a new model): queue them."""
    with session_scope() as db:
        rows = db.exec(select(Memory).where(col(Memory.status).in_(["active", "pending"]))
                       .order_by(Memory.id)).all()
        if not rows:
            return 0
        have = {(e.memory_id): e.text_hash for e in db.exec(
            select(MemoryEmbedding).where(MemoryEmbedding.model_key == model_key)).all()}
        open_jobs = set(db.exec(select(MemoryJob.memory_id).where(
            MemoryJob.kind == "embed", col(MemoryJob.state).in_(["pending", "running"]))).all())
        n = 0
        for m in rows:
            if n >= limit:
                break
            if m.id in open_jobs or have.get(m.id) == text_hash(m.content):
                continue
            enqueue(db, "embed", m.account_id, memory_id=m.id)
            n += 1
        db.commit()
        return n


def housekeeping() -> None:
    from app.memory.repo import MemoryRepo

    now = utcnow()
    with session_scope() as db:
        n = MemoryRepo(db).purge_superseded(now - timedelta(days=SUPERSEDED_KEEP_DAYS))
        db.execute(sa_delete(MemoryJob).where(col(MemoryJob.state).in_(["done", "dead"]),
                                              col(MemoryJob.updated_at) < now - timedelta(days=FINISHED_KEEP_DAYS)))
        db.commit()
    if n:
        log.info("memory purge superseded rows=%s", n)


def queue_stats() -> dict[str, int]:
    """Counts by state (operator diagnostics; no content)."""
    from sqlalchemy import func

    with session_scope() as db:
        rows = db.exec(select(MemoryJob.kind, MemoryJob.state, func.count()).group_by(MemoryJob.kind, MemoryJob.state)).all()
        return {f"{k}:{s}": int(c) for k, s, c in rows}


def _vectors_ready() -> bool:
    from app.memory.repo import has_vectors

    with session_scope() as db:
        return has_vectors(db)


async def job_loop(router: Any) -> None:
    """Runs while memory is enabled; does nothing (cheaply) otherwise."""
    s = get_settings()
    last_housekeeping = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            if s.memory_enabled:
                if s.memory_embeddings_enabled and router is not None and router.embedding() is not None                         and await asyncio.to_thread(_vectors_ready):
                    await asyncio.to_thread(backfill, router.embedding().model_key)
                for job_id in await asyncio.to_thread(_claim):
                    await run_job(router, job_id)
                if loop.time() - last_housekeeping > 3600:
                    last_housekeeping = loop.time()
                    await asyncio.to_thread(housekeeping)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("memory job loop error=%s", type(exc).__name__)
        await asyncio.sleep(s.memory_job_interval_s)
