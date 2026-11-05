"""Background indexing: vectors for opted-in watches' eligible turns, never inside a voice turn.

The durable state is the data itself: an eligible turn without a vector for the current model key still needs one.
That makes indexing incremental, resumable after a restart, a backfill for older turns, and a re-index after a
model change, without a job queue. One watch at a time is leased on its authorisation row (several server
processes never index the same watch); a failure backs that watch off exponentially.

Consent is checked before the embedding call and again, under the same generation, in the transaction that
stores the vectors: switching off (or a new owner) while a call is running stores nothing. A turn deleted
meanwhile is not stored either: the turns are re-read (PostgreSQL: FOR KEY SHARE, so a deletion waits for the
commit and then cascades) and only those still present get a vector.

Voice turns come first: the loop waits while `conversation_rag_index_pause_turns` turns are running.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_
from sqlmodel import Session, col, select

from app.config import get_settings
from app.conversation_rag import authz
from app.db.models import ConversationRagAuthorization, ConversationRagEmbedding, Turn, utcnow
from app.db.session import OWNER, is_postgres, session_scope
from app.providers.base import UsageItem

log = logging.getLogger(__name__)

LEASE_S = 120
TEXT_MAX = 2000
RETRY_BASE_S = 30
RETRY_MAX_S = 3600


def eligible(account_id: int, device_id: str):
    """Turns that may be indexed and searched: completed chat turns with a question, not built from earlier
    conversations, of this watch while it belongs to this account."""
    return and_(Turn.account_id == account_id, Turn.device_id == device_id, Turn.status == "completed",
                Turn.mode == "chat", col(Turn.rag_derived).is_(False), Turn.user_text != "")


def is_web_answer(turn: Turn) -> bool:
    return "web_search" in (turn.tools or "").split(",")


def index_text(turn: Turn) -> str:
    """The question and the reply; only the question when the reply came from a web search (it was true that
    day only, and must not be found later as if it were knowledge)."""
    text = turn.user_text.strip()
    if turn.assistant_text and not is_web_answer(turn):
        text = f"{text}\n{turn.assistant_text.strip()}"
    return text[:TEXT_MAX]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _claim() -> tuple[str, int, int] | None:
    """Lease the watch that waited longest: (device_id, account_id, generation)."""
    s = get_settings()
    now = utcnow()
    with session_scope() as db:
        q = (select(ConversationRagAuthorization)
             .where(ConversationRagAuthorization.state == "on", ConversationRagAuthorization.wearer == "self",
                    or_(col(ConversationRagAuthorization.index_retry_after).is_(None),
                        ConversationRagAuthorization.index_retry_after <= now),
                    or_(col(ConversationRagAuthorization.index_lease_owner).is_(None),
                        ConversationRagAuthorization.index_lease_until < now))
             .order_by(col(ConversationRagAuthorization.index_lease_until).asc().nulls_first())
             .limit(10))
        if is_postgres():
            q = q.with_for_update(skip_locked=True)
        for row in db.exec(q).all():
            if not s.conversation_rag_on_for(row.account_id):
                continue
            row.index_lease_owner, row.index_lease_until = OWNER, now + timedelta(seconds=LEASE_S)
            db.add(row)
            db.commit()
            return row.device_id, row.account_id, row.generation
        db.commit()
    return None


def _release(device_id: str, failed: bool) -> None:
    with session_scope() as db:
        row = db.get(ConversationRagAuthorization, device_id)
        if row is None or row.index_lease_owner != OWNER:
            return
        now = utcnow()
        row.index_lease_owner, row.index_lease_until = None, now  # "last visited": the others go first
        if failed:
            row.index_failures += 1
            delay = min(RETRY_MAX_S, RETRY_BASE_S * 2 ** (row.index_failures - 1))
            row.index_retry_after = now + timedelta(seconds=delay)
        else:
            row.index_failures, row.index_retry_after = 0, None
        db.add(row)
        db.commit()


def _pending(device_id: str, account_id: int, model_key: str, limit: int) -> list[tuple[int, str]]:
    with session_scope() as db:
        has = exists().where(ConversationRagEmbedding.turn_id == Turn.id,
                             ConversationRagEmbedding.model_key == model_key)
        rows = db.exec(select(Turn).where(eligible(account_id, device_id), ~has)
                       .order_by(col(Turn.id)).limit(limit)).all()
        return [(t.id, index_text(t)) for t in rows]


def _store(device_id: str, account_id: int, generation: int, model_key: str, dims: int,
           items: list[tuple[int, str, list[float]]]) -> int:
    """Store vectors if the watch is still allowed (same generation) and the turns still exist."""
    with session_scope() as db:
        q = select(ConversationRagAuthorization).where(ConversationRagAuthorization.device_id == device_id)
        if is_postgres():
            q = q.with_for_update()  # a concurrent switch-off waits for this commit, then deletes these vectors
        db.exec(q).first()
        if not authz.still_allowed(db, account_id, device_id, generation):
            db.commit()
            return 0
        ids = [i for i, _, _ in items]
        tq = select(Turn.id).where(col(Turn.id).in_(ids), eligible(account_id, device_id))
        if is_postgres():
            tq = tq.with_for_update(read=True, key_share=True)
        present = set(db.exec(tq).all())
        done = set(db.exec(select(ConversationRagEmbedding.turn_id).where(
            col(ConversationRagEmbedding.turn_id).in_(ids), ConversationRagEmbedding.model_key == model_key)).all())
        n = 0
        for turn_id, text, vec in items:
            if turn_id in present and turn_id not in done:
                db.add(ConversationRagEmbedding(turn_id=turn_id, account_id=account_id, device_id=device_id,
                                                model_key=model_key, dims=dims, text_hash=_hash(text), embedding=vec))
                n += 1
        db.commit()
        return n


def _allowed(device_id: str, account_id: int, generation: int) -> bool:
    with session_scope() as db:
        return authz.still_allowed(db, account_id, device_id, generation)


def _record_usage(device_id: str, account_id: int, provider: Any, tokens: int) -> None:
    from app import usage_ops

    usage_ops.record_free_operation("conversation_rag", device_id, account_id,
                                    [UsageItem("embedding", provider.name, provider.model, "input_token", tokens)])


async def index_once(provider: Any) -> int:
    """One batch for one watch. Returns how many vectors were stored."""
    s = get_settings()
    claimed = await asyncio.to_thread(_claim)
    if claimed is None:
        return 0
    device_id, account_id, generation = claimed
    failed = False
    try:
        items = await asyncio.to_thread(_pending, device_id, account_id, provider.model_key,
                                        max(1, s.conversation_rag_index_batch_size))
        if not items or not await asyncio.to_thread(_allowed, device_id, account_id, generation):
            return 0
        res = await provider.embed([text for _, text in items])
        await asyncio.to_thread(_record_usage, device_id, account_id, provider, res.input_tokens)
        stored = await asyncio.to_thread(
            _store, device_id, account_id, generation, provider.model_key, provider.dims,
            [(turn_id, text, vec) for (turn_id, text), vec in zip(items, res.vectors)])
        log.info("conversation_rag index device=%s turns=%d stored=%d", device_id, len(items), stored)
        return stored
    except Exception as exc:  # noqa: BLE001 - one watch failing never stops the loop
        failed = True
        log.warning("conversation_rag index failed device=%s error=%s", device_id, type(exc).__name__)
        return 0
    finally:
        await asyncio.to_thread(_release, device_id, failed)


def busy_turns(hub: Any) -> int:
    if hub is None:
        return 0
    return sum(1 for c in list(hub.connections.values()) if getattr(c, "active", None) is not None)


async def index_loop(hub: Any) -> None:
    """Runs in every process; idle (cheaply) unless conversation RAG and its indexing are switched on."""
    from app.conversation_rag.embed import provider as embed_provider

    s = get_settings()
    while True:
        stored = 0
        try:
            p = embed_provider()
            if (s.conversation_rag_enabled and s.conversation_rag_indexing_enabled and p is not None
                    and busy_turns(hub) < s.conversation_rag_index_pause_turns):
                stored = await index_once(p)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("conversation_rag index loop error=%s", type(exc).__name__)
        # A full batch: more is probably waiting (backfill), continue soon; otherwise the normal interval.
        await asyncio.sleep(1 if stored >= s.conversation_rag_index_batch_size else s.conversation_rag_index_interval_s)


def counts(db: Session) -> dict[str, int]:
    """Diagnostics: watches switched on and vectors stored (no content)."""
    on = db.exec(select(func.count()).select_from(ConversationRagAuthorization)
                 .where(ConversationRagAuthorization.state == "on")).one()
    vectors = db.exec(select(func.count()).select_from(ConversationRagEmbedding)).one()
    return {"watches_on": int(on), "vectors": int(vectors)}
