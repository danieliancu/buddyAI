"""Memory storage. Every method takes the owning account and filters on it: there is no way to read,
change or delete another account's memory through this class.

Foreign keys have no ON DELETE rules in this schema (all cascades are explicit), so deleting a memory
also deletes its vectors and jobs, and links from other memories / to turns are cleared first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from sqlalchemy import delete as sa_delete, func, or_, update as sa_update
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db.models import Memory, MemoryEmbedding, MemoryJob, utcnow
from app.memory import policy

LIVE = ("active", "pending")
MAX_PENDING = 50


class MemoryLimitError(Exception):
    pass


class MemoryConflictError(Exception):
    """The memory changed (version) or is gone since it was read."""


@dataclass
class SaveResult:
    memory: Memory
    outcome: str  # created | duplicate | superseded
    replaced: Memory | None = None


def _aware(dt: datetime | None) -> datetime | None:
    from datetime import timezone

    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


class MemoryRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    # --- reading -------------------------------------------------------------------------------------

    def get(self, account_id: int, uid: str) -> Memory | None:
        return self.s.exec(select(Memory).where(Memory.account_id == account_id, Memory.uid == uid)).first()

    def list(self, account_id: int, statuses: Iterable[str] = LIVE) -> list[Memory]:
        q = select(Memory).where(Memory.account_id == account_id, col(Memory.status).in_(list(statuses)))
        return list(self.s.exec(q.order_by(col(Memory.updated_at).desc(), col(Memory.id).desc())).all())

    def for_device(self, account_id: int, device_id: str, now: datetime | None = None, limit: int | None = None) -> list[Memory]:
        """Active, unexpired memories this watch may use: the account's shared ones and the watch's own."""
        now = now or utcnow()
        q = select(Memory).where(
            Memory.account_id == account_id,
            Memory.status == "active",
            or_(col(Memory.device_id).is_(None), Memory.device_id == device_id),
            or_(col(Memory.valid_until).is_(None), col(Memory.valid_until) > now),
        ).order_by(col(Memory.updated_at).desc(), col(Memory.id).desc())
        if limit:
            q = q.limit(limit)
        return list(self.s.exec(q).all())

    def count(self, account_id: int, status: str = "active") -> int:
        return int(self.s.exec(
            select(func.count()).select_from(Memory).where(Memory.account_id == account_id, Memory.status == status)
        ).one())

    # --- writing -------------------------------------------------------------------------------------

    def save(
        self,
        account_id: int,
        content: str,
        kind: str,
        origin: str,
        *,
        device_id: str | None = None,
        subject: str | None = None,
        attribute: str | None = None,
        status: str = "active",
        confidence: float | None = None,
        sensitivity: str | None = None,
        source_turn_id: int | None = None,
        valid_until: datetime | None = None,
        commit: bool = True,
    ) -> SaveResult:
        """Store one fact (policy-checked). The same fact again -> the existing one (duplicate). The same
        subject + attribute as an active fact -> that one is superseded. Committed before returning, so a
        caller can only report success for a memory that is durably saved."""
        text = policy.check(content)
        kind = kind if kind in policy.KINDS else "other"
        subject, attribute = policy.clean_key(subject), policy.clean_key(attribute)
        sensitivity = sensitivity or ("special" if policy.is_special(text) else "normal")
        h = policy.content_hash(text)
        now = utcnow()
        same = self.s.exec(select(Memory).where(
            Memory.account_id == account_id, Memory.content_hash == h, col(Memory.status).in_(list(LIVE)),
            (Memory.device_id == device_id) if device_id else col(Memory.device_id).is_(None),
        )).first()
        if same is not None:
            if status == "active" and same.status == "pending":  # asked again: that confirms it
                same.status, same.confirmed_at, same.updated_at = "active", now, now
                same.version += 1
                self.s.add(same)
                self._done(commit)
            return SaveResult(same, "duplicate")
        old = None
        if subject and attribute:
            old = self.s.exec(select(Memory).where(
                Memory.account_id == account_id, Memory.subject == subject, Memory.attribute == attribute,
                Memory.status == "active",
                (Memory.device_id == device_id) if device_id else col(Memory.device_id).is_(None),
            )).first()
        if status == "active" and old is None and self.count(account_id) >= get_settings().memory_max_active:
            raise MemoryLimitError("active")
        if status == "pending" and self.count(account_id, "pending") >= MAX_PENDING:
            raise MemoryLimitError("pending")
        m = Memory(
            account_id=account_id, device_id=device_id, kind=kind, content=text, content_hash=h, subject=subject,
            attribute=attribute, origin=origin, status=status, confidence=confidence,
            confirmed_at=now if origin in ("explicit", "web") else None, sensitivity=sensitivity,
            source_turn_id=source_turn_id, supersedes_id=old.id if (old is not None and status == "active") else None,
            valid_until=valid_until, created_at=now, updated_at=now,
        )
        if status == "pending" and old is not None:
            m.supersedes_id = old.id  # applied only when the user confirms it
        if old is not None and status == "active":
            old.status, old.updated_at = "superseded", now
            old.version += 1
            self.s.add(old)
            self.s.flush()  # the old row leaves the live set before the new one enters it
        self.s.add(m)
        self.s.flush()
        self._enqueue_embed(m)
        self._done(commit)
        return SaveResult(m, "superseded" if (old is not None and status == "active") else "created", old)

    def update(self, account_id: int, uid: str, content: str | None = None, kind: str | None = None,
               expected_version: int | None = None, origin: str = "explicit") -> SaveResult:
        """A correction: the new text becomes a new active memory that supersedes this one (history kept)."""
        m = self.get(account_id, uid)
        if m is None or m.status == "superseded":
            raise MemoryConflictError("missing")
        if expected_version is not None and m.version != expected_version:
            raise MemoryConflictError("changed")
        text = policy.check(content) if content else m.content
        if text == m.content and (kind is None or kind == m.kind):
            if m.status == "pending":
                return SaveResult(self.confirm(account_id, uid), "duplicate")
            return SaveResult(m, "duplicate")
        now = utcnow()
        m.status, m.updated_at = "superseded", now
        m.version += 1
        self.s.add(m)
        self.s.flush()
        new = Memory(
            account_id=account_id, device_id=m.device_id, kind=kind if kind in policy.KINDS else m.kind,
            content=text, content_hash=policy.content_hash(text), subject=m.subject, attribute=m.attribute,
            origin=origin, status="active", confirmed_at=now,
            sensitivity="special" if (m.sensitivity == "special" or policy.is_special(text)) else "normal",
            source_turn_id=m.source_turn_id, supersedes_id=m.id, valid_until=m.valid_until,
            created_at=now, updated_at=now,
        )
        clash = self.s.exec(select(Memory).where(
            Memory.account_id == account_id, Memory.content_hash == new.content_hash,
            col(Memory.status).in_(list(LIVE)),
            (Memory.device_id == m.device_id) if m.device_id else col(Memory.device_id).is_(None),
        )).first()
        if clash is not None:  # the corrected text is already known: keep that one
            self.s.commit()
            return SaveResult(clash, "duplicate", m)
        self.s.add(new)
        self.s.flush()
        self._enqueue_embed(new)
        self.s.commit()
        return SaveResult(new, "superseded", m)

    def confirm(self, account_id: int, uid: str) -> Memory:
        """A pending (learned) memory confirmed by the user; one it was meant to replace is superseded now."""
        m = self.get(account_id, uid)
        if m is None or m.status != "pending":
            raise MemoryConflictError("missing")
        now = utcnow()
        if m.supersedes_id is not None:
            old = self.s.get(Memory, m.supersedes_id)
            if old is not None and old.account_id == account_id and old.status == "active":
                old.status, old.updated_at = "superseded", now
                old.version += 1
                self.s.add(old)
                self.s.flush()
        m.status, m.confirmed_at, m.updated_at = "active", now, now
        m.version += 1
        self.s.add(m)
        self.s.commit()
        self.s.refresh(m)
        return m

    def forget(self, account_id: int, uid: str) -> int:
        """Delete this memory and the earlier versions it replaced (nothing of it is kept). Returns rows deleted."""
        m = self.get(account_id, uid)
        if m is None:
            return 0
        ids, cur = [], m
        while cur is not None and cur.account_id == account_id and cur.id not in ids:
            ids.append(cur.id)
            cur = self.s.get(Memory, cur.supersedes_id) if cur.supersedes_id else None
        n = self._delete_ids(account_id, ids)
        self.s.commit()
        return n

    def forget_all(self, account_id: int, commit: bool = True) -> int:
        ids = list(self.s.exec(select(Memory.id).where(Memory.account_id == account_id)).all())
        self.s.execute(sa_delete(MemoryJob).where(MemoryJob.account_id == account_id))
        n = self._delete_ids(account_id, ids)
        self._done(commit)
        return n

    def _delete_ids(self, account_id: int, ids: Sequence[int]) -> int:
        if not ids:
            return 0
        ids = list(ids)
        if has_vectors(self.s):
            self.s.execute(sa_delete(MemoryEmbedding).where(
                MemoryEmbedding.account_id == account_id, col(MemoryEmbedding.memory_id).in_(ids)))
        self.s.execute(sa_delete(MemoryJob).where(MemoryJob.account_id == account_id, col(MemoryJob.memory_id).in_(ids)))
        # later versions of a deleted memory stay, without the link
        self.s.execute(sa_update(Memory).where(
            Memory.account_id == account_id, col(Memory.supersedes_id).in_(ids), col(Memory.id).not_in(ids)
        ).values(supersedes_id=None))
        # rows of the set point at each other: unlink first, then delete
        self.s.execute(sa_update(Memory).where(Memory.account_id == account_id, col(Memory.id).in_(ids))
                       .values(supersedes_id=None))
        res = self.s.execute(sa_delete(Memory).where(Memory.account_id == account_id, col(Memory.id).in_(ids)))
        return int(res.rowcount or 0)

    def touch_used(self, account_id: int, ids: Sequence[int]) -> None:
        """last_used_at, at most once a day per memory (recall ranking and cleanup)."""
        if not ids:
            return
        now = utcnow()
        self.s.execute(sa_update(Memory).where(
            Memory.account_id == account_id, col(Memory.id).in_(list(ids)),
            or_(col(Memory.last_used_at).is_(None), col(Memory.last_used_at) < now - timedelta(days=1)),
        ).values(last_used_at=now))
        self.s.commit()

    def set_prefs(self, account_id: int, explicit: bool | None = None, learn: bool | None = None) -> None:
        from app.db.models import Account

        acc = self.s.get(Account, account_id)
        if acc is None:
            return
        if explicit is not None:
            acc.memory_explicit = explicit
        if learn is not None:
            acc.memory_learn = learn
        self.s.add(acc)
        self.s.commit()

    # --- links to history (no ON DELETE in this schema) ------------------------------------------------

    def detach_turns(self, turn_ids: Sequence[int]) -> None:
        """Before turns are deleted: memories stay (they are not history), only the link goes."""
        if turn_ids:
            self.s.execute(sa_update(Memory).where(col(Memory.source_turn_id).in_(list(turn_ids)))
                           .values(source_turn_id=None))

    def detach_conversations(self, conversation_ids: Sequence[int]) -> None:
        """Before conversations are deleted: their pending learning jobs are dropped."""
        if conversation_ids:
            self.s.execute(sa_delete(MemoryJob).where(col(MemoryJob.conversation_id).in_(list(conversation_ids))))

    def purge_superseded(self, older_than: datetime) -> int:
        ids = list(self.s.exec(select(Memory.id, Memory.account_id).where(
            Memory.status == "superseded", col(Memory.updated_at) < older_than)).all())
        n = 0
        by_acc: dict[int, list[int]] = {}
        for mid, acc in ids:
            by_acc.setdefault(acc, []).append(mid)
        for acc, mids in by_acc.items():
            n += self._delete_ids(acc, mids)
        self.s.commit()
        return n

    # --- helpers ---------------------------------------------------------------------------------------

    def _enqueue_embed(self, m: Memory) -> None:
        if get_settings().memory_embeddings_enabled:
            from app.memory.jobs import enqueue

            enqueue(self.s, "embed", m.account_id, memory_id=m.id)

    def _done(self, commit: bool) -> None:
        if commit:
            self.s.commit()
        else:
            self.s.flush()


def aware(dt: datetime | None) -> datetime | None:
    return _aware(dt)


_VECTORS: list[bool] = []


def has_vectors(s: Session) -> bool:
    """Whether migration 0022 (memory_embeddings, pgvector on PostgreSQL) is applied. Milestone A runs
    without it; once the table exists it stays, so a positive answer is cached."""
    if _VECTORS:
        return True
    from sqlalchemy import inspect

    if inspect(s.connection()).has_table("memory_embeddings"):
        _VECTORS.append(True)
        return True
    return False
