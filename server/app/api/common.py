"""Logic shared by the operator API (/api/...) and the customer API (/api/me/...).

Ownership is decided by the caller (operator: any device; customer: DeviceRepo.owned); these
helpers never widen access. `account_id` scopes personas and history to one owner.
"""

from __future__ import annotations

import io
import wave
from datetime import datetime
from typing import Any, Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session

from app import languages
from app.db.models import Device
from app.db.repositories import ConversationRepo, PersonaRepo, SettingsRepo
from app.device_settings import THEME_PRESETS, DeviceSettings
from app.gateway.hub import DeviceHub
from app.providers.base import ProviderError
from app.providers.tts.base import TTSRequest


def hub_of(request: Request) -> DeviceHub:
    return request.app.state.hub


def device_out(dev: Device, hub: DeviceHub) -> dict[str, Any]:
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


def read_settings(db: Session, device_id: str) -> dict[str, Any]:
    settings, version = SettingsRepo(db).ensure(device_id)
    return {"settings": settings.model_dump(), "version": version}


async def patch_settings(
    db: Session, hub: DeviceHub, device_id: str, changes: dict[str, Any], account_id: int | None
) -> dict[str, Any]:
    unknown = sorted(set(changes) - set(DeviceSettings.model_fields))
    if unknown:
        raise HTTPException(422, [{"loc": [k], "msg": "unknown setting", "type": "extra_forbidden"} for k in unknown])
    persona_id = changes.get("persona_id")
    if persona_id is not None and not PersonaRepo(db).visible_to(persona_id, account_id):
        raise HTTPException(422, [{"loc": ["persona_id"], "msg": "unknown persona", "type": "value_error"}])
    try:
        settings, version = SettingsRepo(db).update(device_id, changes)
    except ValidationError as exc:
        # include_context=False: the context may hold exception objects that are not JSON-serializable
        raise HTTPException(422, exc.errors(include_url=False, include_context=False, include_input=False)) from exc
    await hub.push_settings(device_id)
    return {"settings": settings.model_dump(), "version": version}


def options(request: Request) -> dict[str, Any]:
    """Choices for the settings UI (models, voices, presets, languages) — all from config."""
    r = request.app.state.router
    return {
        "llm_models": r.llm_models(),
        "tts": r.tts_options(),
        "theme_presets": THEME_PRESETS,
        "languages": [lang.public() for lang in languages.supported()],
        "vad_sensitivity": ["low", "medium", "high"],
    }


def conversations_out(db: Session, device_id: str, account_id: int | None) -> list[dict[str, Any]]:
    """History of one watch. With account_id, only that owner's turns are returned."""
    repo = ConversationRepo(db)
    out = []
    for c in repo.list_for_device(device_id):
        turns = repo.turns(c.id, account_id)
        if not turns:
            continue
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


class VoiceSampleBody(BaseModel):
    voice: str = Field(min_length=1, max_length=64)
    language: str = Field("en", min_length=2, max_length=8)


async def voice_sample(body: VoiceSampleBody, request: Request) -> Response:
    """A short sample sentence in `language` spoken with `voice`, as WAV."""
    if body.language not in languages.supported_codes():
        raise HTTPException(422, "unsupported language")
    r = request.app.state.router
    sel = r.tts(body.language, DeviceSettings(), voice=body.voice)
    if sel.voice != body.voice:
        raise HTTPException(422, "unknown voice for this language")

    async def one():
        yield languages.sample_sentence(body.language)

    pcm, rate = bytearray(), 24000
    try:
        req = TTSRequest(sel.voice, body.language, 1.0, sel.instructions)
        async for chunk in sel.provider.stream(one(), req):
            pcm += chunk.pcm
            rate = chunk.sample_rate
    except ProviderError as exc:
        raise HTTPException(502, exc.message) from exc
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(pcm))
    return Response(buf.getvalue(), media_type="audio/wav")


class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    system_prompt: str = Field(min_length=1, max_length=4000)
    is_default: bool = False


class ItemBody(BaseModel):
    """A note or reminder from the web app. `due_at` is ISO 8601 with an offset (the browser's local time).

    Text limits per kind (note 10000, reminder 80) are checked by ItemRepo.
    """

    kind: Literal["note", "reminder"] = "note"
    text: str = Field(min_length=1, max_length=10000)
    due_at: datetime | None = None
    end_at: datetime | None = None  # reminders, optional: end of a time range
    notify_before_min: int | None = None  # reminders, optional: extra alert this many minutes before
    location: str | None = None  # reminders, optional
    participants: str | None = None  # reminders, optional: "Ana, Mihai"


class ItemDoneBody(BaseModel):
    done: bool
