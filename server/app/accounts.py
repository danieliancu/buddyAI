"""Customer accounts: sign-up, login, email tokens, data export and deletion."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any

from sqlmodel import Session, col, select

from app.config import get_settings
from app.account_lock import lock_account
from app.db.models import (
    Account,
    AuditLog,
    AuthToken,
    Conversation,
    Device,
    DeviceSettingsRow,
    Item,
    Persona,
    Turn,
    UsageRecord,
    utcnow,
)
from app.db.repositories import _aware
from app.security import hash_password, verify_password

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
# set_password: the welcome link of an account created at checkout (it also has to survive shipping).
TOKEN_TTL = {"verify_email": timedelta(hours=48), "reset_password": timedelta(hours=1), "set_password": timedelta(days=7)}


class AccountError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AccountError(422, "enter a valid email address")
    return email


def check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(422, f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > 128:
        raise AccountError(422, "password is too long")


def by_email(db: Session, email: str) -> Account | None:
    return db.exec(select(Account).where(Account.email == email)).first()


def create(db: Session, email: str, password: str | None, name: str = "", country: str | None = None) -> Account:
    email = normalize_email(email)
    if password is not None:
        check_password(password)
    if by_email(db, email):
        raise AccountError(409, "an account with this email already exists")
    acc = Account(
        email=email,
        password_hash=hash_password(password) if password else None,
        name=name.strip()[:120],
        country=(country or "").upper()[:2] or None,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


# Hash used when the account doesn't exist, so login timing doesn't reveal which emails exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def authenticate(db: Session, email: str, password: str) -> Account:
    try:
        email = normalize_email(email)
    except AccountError:
        raise AccountError(401, "wrong email or password") from None
    acc = by_email(db, email)
    if acc is None or acc.password_hash is None:
        verify_password(password, _DUMMY_HASH)
        raise AccountError(401, "wrong email or password")
    if not verify_password(password, acc.password_hash):
        raise AccountError(401, "wrong email or password")
    if acc.status != "active":
        raise AccountError(403, "this account is suspended — contact support")
    acc.last_login_at = utcnow()
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


# --- email tokens ----------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db: Session, account: Account, purpose: str) -> str:
    # One live token per purpose: older unused ones are invalidated.
    for old in db.exec(
        select(AuthToken).where(
            AuthToken.account_id == account.id, AuthToken.purpose == purpose, col(AuthToken.used_at).is_(None)
        )
    ).all():
        old.used_at = utcnow()
        db.add(old)
    token = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            account_id=account.id,
            purpose=purpose,
            token_hash=_hash(token),
            expires_at=utcnow() + TOKEN_TTL[purpose],
        )
    )
    db.commit()
    return token


def consume_token(db: Session, token: str, purpose: str | tuple[str, ...]) -> Account:
    purposes = (purpose,) if isinstance(purpose, str) else purpose
    row = db.exec(
        select(AuthToken).where(AuthToken.token_hash == _hash(token or ""), col(AuthToken.purpose).in_(purposes))
    ).first()
    if row is None or row.used_at is not None or _aware(row.expires_at) < utcnow():
        raise AccountError(400, "this link is invalid or has expired")
    acc = db.get(Account, row.account_id)
    if acc is None or acc.status == "deleted":
        raise AccountError(400, "this link is invalid or has expired")
    row.used_at = utcnow()
    db.add(row)
    db.commit()
    return acc


def invalidate_tokens(db: Session, account: Account, purposes: tuple[str, ...]) -> None:
    for old in db.exec(
        select(AuthToken).where(
            AuthToken.account_id == account.id, col(AuthToken.purpose).in_(purposes), col(AuthToken.used_at).is_(None)
        )
    ).all():
        old.used_at = utcnow()
        db.add(old)
    db.commit()


def link(path: str, token: str, query: str = "") -> str:
    """App link carrying a one-time token (kept last in the URL). `query`: extra "a=b&" parameters."""
    s = get_settings()
    base = s.app_url.rstrip("/") or f"http://localhost:{s.port}"
    return f"{base}{path}?{query}token={token}"


def welcome_link(db: Session, account: Account) -> str:
    """Set-password link for an account created at checkout (7 days; opens the setup afterwards)."""
    token = issue_token(db, account, "set_password")
    return link("/reset-password", token, "welcome=1&")


def set_password(db: Session, account: Account, password: str) -> None:
    check_password(password)
    account.password_hash = hash_password(password)
    account.session_version += 1  # log out other sessions
    db.add(account)
    db.commit()
    db.refresh(account)


def mark_verified(db: Session, account: Account) -> None:
    if account.email_verified_at is None:
        account.email_verified_at = utcnow()
        db.add(account)
        db.commit()
        db.refresh(account)


def audit(db: Session, actor: str, action: str, account_id: int | None = None, device_id: str | None = None, detail: str = "") -> None:
    db.add(AuditLog(actor=actor[:80], action=action, account_id=account_id, device_id=device_id, detail=detail[:2000]))
    db.commit()


def public(acc: Account) -> dict[str, Any]:
    return {
        "id": acc.id,
        "email": acc.email,
        "name": acc.name,
        "country": acc.country,
        "email_verified": acc.email_verified_at is not None,
        "status": acc.status,
        "has_password": acc.password_hash is not None,
        "created_at": acc.created_at,
        "memory": get_settings().memory_on_for(acc.id),
    }


# --- GDPR: export and delete -----------------------------------------------------------------


def export(db: Session, account: Account) -> dict[str, Any]:
    devices = db.exec(select(Device).where(Device.account_id == account.id)).all()
    out_devices = []
    for d in devices:
        row = db.get(DeviceSettingsRow, d.id)
        turns = db.exec(select(Turn).where(Turn.device_id == d.id, Turn.account_id == account.id).order_by(Turn.id)).all()
        out_devices.append(
            {
                "id": d.id,
                "name": d.name,
                "paired_at": d.paired_at,
                "settings": row.data if row else None,
                "conversation_turns": [
                    {
                        "at": t.created_at,
                        "language": t.language,
                        "status": t.status,
                        "you_said": t.user_text,
                        "assistant_replied": t.assistant_text,
                    }
                    for t in turns
                ],
            }
        )
    personas = db.exec(select(Persona).where(Persona.account_id == account.id)).all()
    items = db.exec(select(Item).where(Item.account_id == account.id).order_by(Item.kind, Item.number)).all()
    from app.db.models import Memory

    names = {d.id: d.name for d in devices}
    memories = db.exec(select(Memory).where(Memory.account_id == account.id).order_by(Memory.id)).all()
    return {
        "exported_at": utcnow(),
        "account": public(account),
        "watches": out_devices,
        "personas": [{"name": p.name, "instructions": p.system_prompt} for p in personas],
        "notes_and_reminders": [
            {"kind": i.kind, "number": i.number, "text": i.text, "due_at": i.due_at}
            for i in items
        ],
        "memories": [
            {
                "id": m.uid, "fact": m.content, "kind": m.kind, "status": m.status, "origin": m.origin,
                "sensitivity": m.sensitivity, "confidence": m.confidence, "confirmed_at": m.confirmed_at,
                "watch": names.get(m.device_id, m.device_id) if m.device_id else None,
                "valid_until": m.valid_until, "created_at": m.created_at, "updated_at": m.updated_at,
            }
            for m in memories
        ],
    }


def delete_account(db: Session, account: Account) -> list[str]:
    """Erase personal data. Returns the unpaired device ids (the caller disconnects them).

    Usage records are kept for accounting but detached from content (turn link removed).
    """
    lock_account(db, account.id)
    device_ids = [d.id for d in db.exec(select(Device).where(Device.account_id == account.id)).all()]
    turns = db.exec(select(Turn).where(Turn.account_id == account.id)).all()
    turn_ids = [t.id for t in turns]
    from app.memory.repo import MemoryRepo

    MemoryRepo(db).forget_all(account.id, commit=False)  # memories, their vectors and jobs
    MemoryRepo(db).detach_turns(turn_ids)
    MemoryRepo(db).detach_conversations(list(db.exec(
        select(Conversation.id).where(col(Conversation.device_id).in_(device_ids or [""]))).all()))
    if turn_ids:
        for u in db.exec(select(UsageRecord).where(col(UsageRecord.turn_id).in_(turn_ids))).all():
            u.turn_id = None
            db.add(u)
    for t in turns:
        db.delete(t)
    for c in db.exec(select(Conversation).where(col(Conversation.device_id).in_(device_ids or [""]))).all():
        db.delete(c)
    for dev_id in device_ids:
        dev = db.get(Device, dev_id)
        if dev:
            dev.account_id = None
            dev.token_hash = None
            dev.revoked_at = utcnow()
            db.add(dev)
        row = db.get(DeviceSettingsRow, dev_id)
        if row:
            db.delete(row)
    for p in db.exec(select(Persona).where(Persona.account_id == account.id)).all():
        db.delete(p)
    for it in db.exec(select(Item).where(Item.account_id == account.id)).all():
        db.delete(it)
    for tok in db.exec(select(AuthToken).where(AuthToken.account_id == account.id)).all():
        db.delete(tok)
    account.status = "deleted"
    account.email = f"deleted-{account.id}@deleted.invalid"
    account.name = ""
    account.password_hash = None
    account.country = None
    account.session_version += 1
    db.add(account)
    db.commit()
    return device_ids
