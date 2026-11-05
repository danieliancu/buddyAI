"""Who may use Conversation RAG on a watch.

A watch's earlier conversations are searched only when all of these hold:
- the server switch is on and the account is allowed (Settings.conversation_rag_on_for);
- the watch belongs to that account now;
- it is treated as worn by its owner and the feature is on, unless the owner said otherwise
  (conversation_rag_authorizations; the row is created with those defaults the first time it is needed).

Someone else's watch stays off once declared: the account holder cannot consent for the wearer (no approved wearer
authorisation exists yet), and the database refuses that state too. Every change of state, wearer or owner bumps
`generation`; work started under an older generation is neither stored nor returned.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import ConversationRagAuthorization, ConversationRagEmbedding, Device, utcnow
from app.db.session import is_postgres

NOTICE_VERSION = "2026-11"  # the explanation shown next to the switch; changed when its wording changes
WEARERS = ("self", "other")


@dataclass(frozen=True)
class Access:
    on: bool = False
    generation: int = 0


OFF = Access()


def _row(db: Session, device_id: str, lock: bool = False) -> ConversationRagAuthorization | None:
    q = select(ConversationRagAuthorization).where(ConversationRagAuthorization.device_id == device_id)
    if lock and is_postgres():
        q = q.with_for_update()
    return db.exec(q).first()


def _row_or_default(db: Session, account_id: int, device_id: str) -> ConversationRagAuthorization | None:
    """The watch's row. A watch of this account without one gets the default: its owner wears it, feature on."""
    row = _row(db, device_id)
    if row is not None:
        return row
    dev = db.get(Device, device_id)
    if dev is None or dev.account_id != account_id or dev.revoked_at is not None:
        return None
    try:
        with db.begin_nested():
            db.add(ConversationRagAuthorization(device_id=device_id, account_id=account_id, wearer="self", state="on",
                                                authorized_at=utcnow(), notice_version=NOTICE_VERSION, method="default"))
        db.commit()
    except IntegrityError:  # another connection of the same watch created it first
        pass
    return _row(db, device_id)


def access(db: Session, account_id: int | None, device_id: str) -> Access:
    """The watch's access now (at connect, and again before a search returns or its result is stored)."""
    if not get_settings().conversation_rag_on_for(account_id):
        return OFF
    row = _row_or_default(db, account_id, device_id)
    dev = db.get(Device, device_id)
    if dev is None or dev.account_id != account_id or dev.revoked_at is not None or row is None:
        return OFF
    if row.account_id != account_id or row.wearer != "self" or row.state != "on":
        return OFF
    return Access(True, row.generation)


def still_allowed(db: Session, account_id: int | None, device_id: str, generation: int) -> bool:
    a = access(db, account_id, device_id)
    return a.on and a.generation == generation


def view(db: Session, account_id: int, device_id: str) -> dict:
    """The customer's view of the setting (no internal ids, no indexing state)."""
    available = get_settings().conversation_rag_on_for(account_id)
    row = _row_or_default(db, account_id, device_id) if available else _row(db, device_id)
    mine = row is not None and row.account_id == account_id
    return {
        "available": available,
        "wearer": row.wearer if mine else None,
        "enabled": bool(mine and row.state == "on" and row.wearer == "self"),
        "authorized_at": row.authorized_at.isoformat() if mine and row.authorized_at else None,
        "notice_version": NOTICE_VERSION,
    }


def set_choice(db: Session, account_id: int, device_id: str, wearer: str, enabled: bool,
               method: str = "web") -> ConversationRagAuthorization:
    """Record the wearer declaration and the switch. Raises ValueError for a choice that is not allowed.

    Switching off (or declaring another wearer) deletes the watch's vectors at once; the conversations stay.
    The caller commits."""
    if wearer not in WEARERS:
        raise ValueError("wearer must be 'self' or 'other'")
    if enabled and wearer != "self":
        raise ValueError("not available for a watch someone else wears")
    row = _row(db, device_id, lock=True)
    now = utcnow()
    if row is None or row.account_id != account_id:
        if row is not None:  # left by an earlier owner (normally removed on transfer): start clean
            db.delete(row)
            db.flush()
        row = ConversationRagAuthorization(device_id=device_id, account_id=account_id, wearer=wearer)
    state = "on" if enabled else "off"
    if (row.wearer, row.state) != (wearer, state):
        row.generation += 1
    row.wearer, row.state, row.updated_at = wearer, state, now
    if enabled:
        row.authorized_at, row.notice_version, row.method = now, NOTICE_VERSION, method
    else:
        row.authorized_at = None
        delete_vectors(db, device_id)
    row.index_failures, row.index_retry_after = 0, None
    db.add(row)
    db.flush()
    return row


def delete_vectors(db: Session, device_id: str) -> int:
    res = db.execute(sa_delete(ConversationRagEmbedding).where(ConversationRagEmbedding.device_id == device_id))
    return int(res.rowcount or 0)


def forget_device(db: Session, device_id: str) -> None:
    """The watch changes owner (or leaves the account): its choice and vectors go. The caller commits."""
    delete_vectors(db, device_id)
    db.execute(sa_delete(ConversationRagAuthorization).where(ConversationRagAuthorization.device_id == device_id))


def forget_account(db: Session, account_id: int) -> None:
    """Account deletion: every choice and vector of the account. The caller commits."""
    db.execute(sa_delete(ConversationRagEmbedding).where(ConversationRagEmbedding.account_id == account_id))
    db.execute(sa_delete(ConversationRagAuthorization).where(ConversationRagAuthorization.account_id == account_id))
