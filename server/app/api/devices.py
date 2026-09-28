"""Devices: list, pairing, rename, revoke, settings, personas, history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session

from app.db.repositories import ConversationRepo, DeviceRepo, PersonaRepo, SettingsRepo
from app.db.models import Persona
from app.db.session import get_session
from app.device_settings import THEME_PRESETS, DeviceSettings
from app.gateway.hub import DeviceHub, PairingError
from app.security import is_valid_pairing_code, require_admin

router = APIRouter(prefix="/api", tags=["devices"], dependencies=[Depends(require_admin)])


def hub_of(request: Request) -> DeviceHub:
    return request.app.state.hub


def _device_out(dev, hub: DeviceHub) -> dict[str, Any]:
    return {
        "id": dev.id,
        "name": dev.name,
        "hw_model": dev.hw_model,
        "fw_version": dev.fw_version,
        "paired_at": dev.paired_at,
        "last_seen_at": dev.last_seen_at,
        "last_ip": dev.last_ip,
        "battery_pct": dev.battery_pct,
        "charging": dev.charging,
        "rssi": dev.rssi,
        "online": hub.is_online(dev.id),
        "state": hub.state_of(dev.id),
    }


@router.get("/devices")
def list_devices(request: Request, db: Session = Depends(get_session)) -> list[dict]:
    hub = hub_of(request)
    return [_device_out(d, hub) for d in DeviceRepo(db).list()]


@router.get("/devices/pending")
def pending(request: Request) -> list[dict]:
    return hub_of(request).list_pending()


class PairBody(BaseModel):
    code: str
    name: str = Field("BuddyAI Watch", min_length=1, max_length=80)


@router.post("/devices/pair")
async def pair(body: PairBody, request: Request) -> dict:
    if not is_valid_pairing_code(body.code):
        raise HTTPException(400, "code must be 6 digits")
    try:
        device_id = await hub_of(request).pair(body.code, body.name)
    except PairingError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"device_id": device_id}


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.patch("/devices/{device_id}")
def rename(device_id: str, body: RenameBody, request: Request, db: Session = Depends(get_session)) -> dict:
    dev = DeviceRepo(db).rename(device_id, body.name)
    if not dev:
        raise HTTPException(404, "device not found")
    return _device_out(dev, hub_of(request))


@router.delete("/devices/{device_id}")
async def revoke(device_id: str, request: Request, db: Session = Depends(get_session)) -> dict:
    if not DeviceRepo(db).revoke(device_id):
        raise HTTPException(404, "device not found")
    await hub_of(request).revoke(device_id)
    return {"ok": True}


# --- settings -------------------------------------------------------------------------------


@router.get("/devices/{device_id}/settings")
def get_settings_(device_id: str, db: Session = Depends(get_session)) -> dict:
    if not DeviceRepo(db).get(device_id):
        raise HTTPException(404, "device not found")
    settings, version = SettingsRepo(db).ensure(device_id)
    return {"settings": settings.model_dump(), "version": version}


@router.patch("/devices/{device_id}/settings")
async def patch_settings(
    device_id: str, changes: dict[str, Any], request: Request, db: Session = Depends(get_session)
) -> dict:
    if not DeviceRepo(db).get(device_id):
        raise HTTPException(404, "device not found")
    unknown = sorted(set(changes) - set(DeviceSettings.model_fields))
    if unknown:
        raise HTTPException(422, [{"loc": [k], "msg": "unknown setting", "type": "extra_forbidden"} for k in unknown])
    try:
        settings, version = SettingsRepo(db).update(device_id, changes)
    except ValidationError as exc:
        # include_context=False: the context may hold exception objects that are not JSON-serializable
        raise HTTPException(422, exc.errors(include_url=False, include_context=False, include_input=False)) from exc
    await hub_of(request).push_settings(device_id)
    return {"settings": settings.model_dump(), "version": version}


@router.get("/options")
def options(request: Request) -> dict:
    """Choices for the settings UI (models, voices, presets) — all from config."""
    r = request.app.state.router
    return {
        "llm_models": r.llm_models(),
        "tts": {lang: {"voices": v["voices"], "default_voice": v["default_voice"]} for lang, v in r.tts_options().items()},
        "theme_presets": THEME_PRESETS,
        "languages": [{"id": "ro", "label": "Română"}, {"id": "en", "label": "English"}],
        "vad_sensitivity": ["low", "medium", "high"],
    }


# --- personas -------------------------------------------------------------------------------


class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    system_prompt: str = Field(min_length=1, max_length=4000)
    is_default: bool = False


@router.get("/personas")
def personas(db: Session = Depends(get_session)) -> list[dict]:
    return [p.model_dump() for p in PersonaRepo(db).list()]


@router.post("/personas")
def create_persona(body: PersonaBody, db: Session = Depends(get_session)) -> dict:
    return PersonaRepo(db).upsert(None, body.name, body.system_prompt, body.is_default).model_dump()


@router.put("/personas/{persona_id}")
def update_persona(persona_id: int, body: PersonaBody, db: Session = Depends(get_session)) -> dict:
    if not db.get(Persona, persona_id):
        raise HTTPException(404, "persona not found")
    return PersonaRepo(db).upsert(persona_id, body.name, body.system_prompt, body.is_default).model_dump()


@router.delete("/personas/{persona_id}")
def delete_persona(persona_id: int, db: Session = Depends(get_session)) -> dict:
    if not PersonaRepo(db).delete(persona_id):
        raise HTTPException(400, "cannot delete (missing or default persona)")
    return {"ok": True}


# --- history --------------------------------------------------------------------------------


@router.get("/devices/{device_id}/conversations")
def conversations(device_id: str, db: Session = Depends(get_session)) -> list[dict]:
    repo = ConversationRepo(db)
    out = []
    for c in repo.list_for_device(device_id):
        turns = repo.turns(c.id)
        out.append(
            {
                "id": c.id,
                "started_at": c.started_at,
                "last_activity_at": c.last_activity_at,
                "turns": [
                    {
                        "id": t.id,
                        "status": t.status,
                        "language": t.language,
                        "user_text": t.user_text,
                        "assistant_text": t.assistant_text,
                        "ttfa_ms": t.ttfa_device_ms or t.ttfa_server_ms,
                        "created_at": t.created_at,
                    }
                    for t in turns
                ],
            }
        )
    return out


@router.delete("/devices/{device_id}/conversations")
def delete_history(device_id: str, db: Session = Depends(get_session)) -> dict:
    return {"deleted_turns": ConversationRepo(db).delete_device_history(device_id)}
