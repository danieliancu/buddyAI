"""Operator API: all devices, pairing (optionally to an account), settings, personas, history."""

from __future__ import annotations

from typing import Any

from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from app import accounts, mfa
from app.api.common import (
    PersonaBody,
    VoiceSampleBody,
    conversations_out,
    device_out,
    hub_of,
    options,
    patch_settings,
    read_settings,
    voice_sample,
)
from app.config import get_settings
from app.db.models import Account, AdminUser, AuditLog, Persona, utcnow
from app.db.repositories import ConversationRepo, DeviceRepo, PersonaRepo
from app.db.session import get_session
from app.gateway.hub import PairingError
from app.ratelimit import LOGIN_PER_ACCOUNT, client_ip
from app.security import current_operator, is_valid_pairing_code, require_admin, verify_password

router = APIRouter(prefix="/api", tags=["devices"], dependencies=[Depends(require_admin)])


def _with_owner(dev, db: Session, request: Request) -> dict[str, Any]:
    out = device_out(dev, hub_of(request))
    owner = db.get(Account, dev.account_id) if dev.account_id else None
    out["account"] = {"id": owner.id, "email": owner.email, "name": owner.name} if owner else None
    return out


@router.get("/devices")
def list_devices(request: Request, account_id: int | None = None, db: Session = Depends(get_session)) -> list[dict]:
    return [_with_owner(d, db, request) for d in DeviceRepo(db).list(account_id)]


@router.get("/devices/pending")
def pending(request: Request) -> list[dict]:
    return hub_of(request).list_pending()


class PairBody(BaseModel):
    code: str
    name: str = Field("ola Watch", min_length=1, max_length=80)
    account_id: int | None = None  # None = operator stock (not owned by a customer)


@router.post("/devices/pair")
async def pair(body: PairBody, request: Request, db: Session = Depends(get_session)) -> dict:
    if not is_valid_pairing_code(body.code):
        raise HTTPException(400, "code must be 6 digits")
    if body.account_id is not None and not db.get(Account, body.account_id):
        raise HTTPException(404, "account not found")
    try:
        device_id = await hub_of(request).pair(body.code, body.name, body.account_id)
    except PairingError as exc:
        raise HTTPException(404, str(exc)) from exc
    accounts.audit(db, request.session["admin"], "device.pair", body.account_id, device_id)
    if body.account_id is not None:  # paired to a customer: a pending ola Care trial starts now
        from app import care_activation

        try:
            await care_activation.activate(body.account_id)
        except Exception:  # noqa: BLE001 - the pairing stands; the activation is retried
            pass
    return {"device_id": device_id}


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.patch("/devices/{device_id}")
def rename(device_id: str, body: RenameBody, request: Request, db: Session = Depends(get_session)) -> dict:
    dev = DeviceRepo(db).rename(device_id, body.name)
    if not dev:
        raise HTTPException(404, "device not found")
    return _with_owner(dev, db, request)


class AssignBody(BaseModel):
    account_id: int | None


@router.put("/devices/{device_id}/account")
async def assign(device_id: str, body: AssignBody, request: Request, db: Session = Depends(get_session)) -> dict:
    """Move a watch to another owner (or back to stock). The previous owner's history is erased."""
    if body.account_id is not None and not db.get(Account, body.account_id):
        raise HTTPException(404, "account not found")
    dev = DeviceRepo(db).assign(device_id, body.account_id)
    if not dev:
        raise HTTPException(404, "device not found")
    hub = hub_of(request)
    hub.forget_owner(device_id)
    await hub.disconnect(device_id, "owner changed")  # the watch reconnects and picks up its new owner
    accounts.audit(db, request.session["admin"], "device.assign", body.account_id, device_id)
    return _with_owner(dev, db, request)


@router.delete("/devices/{device_id}")
async def revoke(device_id: str, request: Request, db: Session = Depends(get_session)) -> dict:
    if not DeviceRepo(db).revoke(device_id):
        raise HTTPException(404, "device not found")
    await hub_of(request).revoke(device_id)
    accounts.audit(db, request.session["admin"], "device.revoke", None, device_id)
    return {"ok": True}


# --- settings -------------------------------------------------------------------------------


@router.get("/devices/{device_id}/settings")
def get_settings_(device_id: str, db: Session = Depends(get_session)) -> dict:
    if not DeviceRepo(db).get(device_id):
        raise HTTPException(404, "device not found")
    return read_settings(db, device_id)


@router.patch("/devices/{device_id}/settings")
async def patch_settings_(
    device_id: str, changes: dict[str, Any], request: Request, db: Session = Depends(get_session)
) -> dict:
    dev = DeviceRepo(db).get(device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    return await patch_settings(db, hub_of(request), device_id, changes, dev.account_id)


@router.get("/options")
def options_(request: Request) -> dict:
    return options(request)


@router.post("/voice-sample")
async def voice_sample_(body: VoiceSampleBody, request: Request) -> Response:
    return await voice_sample(body, request, operator=True)


# --- system personas (operator) ------------------------------------------------------------------


@router.get("/personas")
def personas(db: Session = Depends(get_session)) -> list[dict]:
    """System personas (available to every customer)."""
    return [p.model_dump() for p in PersonaRepo(db).list(system_only=True)]


@router.post("/personas")
def create_persona(body: PersonaBody, db: Session = Depends(get_session)) -> dict:
    return PersonaRepo(db).upsert(None, body.name, body.system_prompt, body.is_default).model_dump()


def _system_persona(db: Session, persona_id: int) -> Persona:
    p = db.get(Persona, persona_id)
    if not p or p.account_id is not None:
        raise HTTPException(404, "persona not found")
    return p


@router.put("/personas/{persona_id}")
def update_persona(
    persona_id: int, body: PersonaBody, request: Request, background: BackgroundTasks, db: Session = Depends(get_session)
) -> dict:
    _system_persona(db, persona_id)
    out = PersonaRepo(db).upsert(persona_id, body.name, body.system_prompt, body.is_default).model_dump()
    background.add_task(hub_of(request).refresh_chat_titles)  # name / default shown on the watches
    return out


@router.delete("/personas/{persona_id}")
def delete_persona(
    persona_id: int, request: Request, background: BackgroundTasks, db: Session = Depends(get_session)
) -> dict:
    _system_persona(db, persona_id)
    if not PersonaRepo(db).delete(persona_id):
        raise HTTPException(400, "cannot delete the default persona")
    background.add_task(hub_of(request).refresh_chat_titles)
    return {"ok": True}


# --- history: metadata only; content needs the principal role and a stated reason, every time (audited) ----


@router.get("/devices/{device_id}/conversations")
def conversations(device_id: str, request: Request, db: Session = Depends(get_session)) -> list[dict]:
    dev = DeviceRepo(db).get(device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    if dev.account_id is not None:
        accounts.audit(db, request.session["admin"], "history.view", dev.account_id, device_id)
    return conversations_out(db, device_id, dev.account_id, details=True, content=False)


class ContentAccessBody(BaseModel):
    reason: str = Field("", max_length=500)
    password: str = Field("", max_length=128)
    code: str = Field("", max_length=32)


MIN_REASON_CHARS = 20


def _content_access(request: Request, db: Session, user: AdminUser, body: ContentAccessBody, device_id: str,
                    account_id: int | None, target: str) -> str:
    """The stated reason, or 403/429. Checked afresh on every request: nothing stays unlocked afterwards."""

    def refuse(status: int, why: str, message: str) -> HTTPException:
        accounts.audit(db, user.username, "content.access_refused", account_id, device_id,
                       f"{target}: {why}; reason given: {body.reason.strip()}")
        return HTTPException(status, message)

    if user.role != "principal":
        raise refuse(403, "not the principal role", "Only the principal administrator can open customer content.")
    if not user.mfa_enabled:
        raise refuse(403, "two-factor authentication off", "Turn on two-factor authentication first.")
    reason = body.reason.strip()
    if len(reason) < MIN_REASON_CHARS:
        raise refuse(403, "no sufficient reason", f"State the reason (at least {MIN_REASON_CHARS} characters).")
    LOGIN_PER_ACCOUNT.hit(f"opcontent|{client_ip(request)}|{user.username.lower()}")
    if not verify_password(body.password, user.password_hash) or not mfa.verify_totp(user, body.code):
        raise refuse(403, "wrong password or code", "The password or the two-factor code is not valid.")
    used = db.exec(
        select(func.count()).select_from(AuditLog).where(
            col(AuditLog.actor) == user.username,
            col(AuditLog.action) == "content.reveal",
            col(AuditLog.created_at) >= utcnow() - timedelta(days=1),
        )
    ).one()
    if used >= get_settings().content_reveal_daily_limit:
        raise refuse(429, "daily limit reached", "The daily limit for opening customer content has been reached.")
    return reason


@router.post("/devices/{device_id}/conversations/{conversation_id}/reveal")
def reveal_conversation(device_id: str, conversation_id: int, body: ContentAccessBody, request: Request,
                        user: AdminUser = Depends(current_operator), db: Session = Depends(get_session)) -> dict:
    """One conversation of one watch, as an exception. There is deliberately no form of this for a whole history."""
    dev = DeviceRepo(db).get(device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    target = f"conversation {conversation_id}"
    reason = _content_access(request, db, user, body, device_id, dev.account_id, target)
    found = next((c for c in conversations_out(db, device_id, dev.account_id, details=True)
                  if c["id"] == conversation_id), None)
    if found is None:
        raise HTTPException(404, "conversation not found")
    accounts.audit(db, user.username, "content.reveal", dev.account_id, device_id, f"{target}: {reason}")
    return found


@router.post("/devices/{device_id}/conversations/delete")
def delete_history(device_id: str, body: ContentAccessBody, request: Request,
                   user: AdminUser = Depends(current_operator), db: Session = Depends(get_session)) -> dict:
    """Customers delete their own history themselves (api/me.py); an operator needs the same checks as /reveal."""
    dev = DeviceRepo(db).get(device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    reason = _content_access(request, db, user, body, device_id, dev.account_id, "whole history (delete)")
    accounts.audit(db, user.username, "history.delete", dev.account_id, device_id, reason)
    deleted = ConversationRepo(db).delete_device_history(device_id)  # vectors and references go by cascade
    hub_of(request).clear_rag_context(device_id)
    return {"deleted_turns": deleted}
