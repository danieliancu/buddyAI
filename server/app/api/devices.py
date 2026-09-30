"""Operator API: all devices, pairing (optionally to an account), settings, personas, history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app import accounts
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
from app.db.models import Account, Persona
from app.db.repositories import ConversationRepo, DeviceRepo, PersonaRepo
from app.db.session import get_session
from app.gateway.hub import PairingError
from app.security import is_valid_pairing_code, require_admin

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
    return await voice_sample(body, request)


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
def update_persona(persona_id: int, body: PersonaBody, db: Session = Depends(get_session)) -> dict:
    _system_persona(db, persona_id)
    return PersonaRepo(db).upsert(persona_id, body.name, body.system_prompt, body.is_default).model_dump()


@router.delete("/personas/{persona_id}")
def delete_persona(persona_id: int, db: Session = Depends(get_session)) -> dict:
    _system_persona(db, persona_id)
    if not PersonaRepo(db).delete(persona_id):
        raise HTTPException(400, "cannot delete the default persona")
    return {"ok": True}


# --- history (operator access is audited) --------------------------------------------------------


@router.get("/devices/{device_id}/conversations")
def conversations(device_id: str, request: Request, db: Session = Depends(get_session)) -> list[dict]:
    dev = DeviceRepo(db).get(device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    if dev.account_id is not None:
        accounts.audit(db, request.session["admin"], "history.view", dev.account_id, device_id)
    return conversations_out(db, device_id, dev.account_id)


@router.delete("/devices/{device_id}/conversations")
def delete_history(device_id: str, request: Request, db: Session = Depends(get_session)) -> dict:
    dev = DeviceRepo(db).get(device_id)
    accounts.audit(db, request.session["admin"], "history.delete", dev.account_id if dev else None, device_id)
    return {"deleted_turns": ConversationRepo(db).delete_device_history(device_id)}
