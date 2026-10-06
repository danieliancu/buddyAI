"""Customer API for long-term memory (/api/me/memories): see, correct, confirm and forget what the
assistant remembers. Every query is scoped to the signed-in account; another account's memory is 404."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app import accounts
from app.api.me import current_account
from app.config import get_settings
from app.db.models import Account, Device, Memory
from app.db.session import get_session
from app.memory import policy
from app.memory.repo import MemoryConflictError, MemoryLimitError, MemoryRepo

router = APIRouter(prefix="/api/me/memories", tags=["customer"])


def _out(m: Memory, names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": m.uid, "fact": m.content, "kind": m.kind, "status": m.status, "origin": m.origin,
        "sensitive": m.sensitivity == "special", "confirmed": m.confirmed_at is not None, "version": m.version,
        "watch": names.get(m.device_id, "") if m.device_id else None, "valid_until": m.valid_until,
        "created_at": m.created_at, "updated_at": m.updated_at,
    }


def _available(acc: Account) -> None:
    if not get_settings().memory_on_for(acc.id):
        raise HTTPException(404, "memory is not available")


@router.get("")
def list_memories(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    s = get_settings()
    if not s.memory_on_for(acc.id):
        return {"available": False}
    from sqlmodel import select

    names = {d.id: d.name for d in db.exec(select(Device).where(Device.account_id == acc.id)).all()}
    rows = MemoryRepo(db).list(acc.id, ("active", "pending", "superseded"))
    return {
        "available": True,
        "learning_available": s.memory_inference_enabled,
        "remember_requests": acc.memory_explicit,
        "use_memories": acc.memory_use,
        "learn": acc.memory_learn,
        "max": s.memory_max_active,
        "kinds": list(policy.KINDS),
        "memories": [_out(m, names) for m in rows],
    }


class MemoryBody(BaseModel):
    fact: str = Field(min_length=1, max_length=policy.CONTENT_MAX)
    kind: str = "other"


class MemoryPatch(BaseModel):
    fact: str | None = Field(default=None, max_length=policy.CONTENT_MAX)
    kind: str | None = None
    version: int | None = None


class PrefsBody(BaseModel):
    remember_requests: bool | None = None
    use_memories: bool | None = None
    learn: bool | None = None


class ClearBody(BaseModel):
    password: str


@router.post("")
def add_memory(body: MemoryBody, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    _available(acc)
    try:
        res = MemoryRepo(db).save(acc.id, body.fact, body.kind, "web")
    except policy.MemoryRefused as exc:
        raise HTTPException(422, str(exc)) from exc
    except MemoryLimitError as exc:
        raise HTTPException(409, "memory is full: forget some first") from exc
    return {"id": res.memory.uid, "outcome": res.outcome}


@router.patch("/{uid}")
def edit_memory(uid: str, body: MemoryPatch, acc: Account = Depends(current_account),
                db: Session = Depends(get_session)) -> dict:
    _available(acc)
    try:
        res = MemoryRepo(db).update(acc.id, uid, content=body.fact, kind=body.kind, expected_version=body.version,
                                    origin="web")
    except policy.MemoryRefused as exc:
        raise HTTPException(422, str(exc)) from exc
    except MemoryConflictError as exc:
        raise HTTPException(404 if str(exc) == "missing" else 409, "memory not found" if str(exc) == "missing"
                            else "it changed meanwhile: reload") from exc
    return {"id": res.memory.uid, "outcome": res.outcome}


@router.post("/{uid}/confirm")
def confirm_memory(uid: str, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    _available(acc)
    try:
        m = MemoryRepo(db).confirm(acc.id, uid)
    except MemoryConflictError as exc:
        raise HTTPException(404, "memory not found") from exc
    return {"id": m.uid, "status": m.status}


@router.delete("/{uid}")
def forget_memory(uid: str, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    _available(acc)
    if not MemoryRepo(db).forget(acc.id, uid):
        raise HTTPException(404, "memory not found")
    return {"ok": True}


@router.post("/clear")
def clear_memories(body: ClearBody, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    """Forget everything: needs the account password (like deleting the account)."""
    _available(acc)
    try:
        accounts.authenticate(db, acc.email, body.password)
    except accounts.AccountError as exc:
        raise HTTPException(403, "wrong password") from exc
    return {"deleted": MemoryRepo(db).forget_all(acc.id)}


@router.put("/settings")
def memory_settings(body: PrefsBody, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    _available(acc)
    MemoryRepo(db).set_prefs(acc.id, explicit=body.remember_requests, learn=body.learn, use=body.use_memories)
    db.refresh(acc)
    return {"remember_requests": acc.memory_explicit, "use_memories": acc.memory_use, "learn": acc.memory_learn}
