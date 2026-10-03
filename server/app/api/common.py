"""Logic shared by the operator API (/api/...) and the customer API (/api/me/...).

Ownership is decided by the caller (operator: any device; customer: DeviceRepo.owned); these
helpers never widen access. `account_id` scopes personas and history to one owner.
"""

from __future__ import annotations

import io
import wave
from datetime import datetime
from typing import Any, Literal, Sequence

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, col, select

from app import languages
from app.db.models import Device, Turn, UsageRecord
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
    except ValueError as exc:  # merge(): e.g. a theme other than the two presets
        raise HTTPException(422, [{"loc": ["theme"], "msg": str(exc), "type": "value_error"}]) from exc
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


def conversations_out(
    db: Session, device_id: str, account_id: int | None, details: bool = False
) -> list[dict[str, Any]]:
    """History of one watch. With account_id, only that owner's turns are returned.

    details (operator only): each turn also carries its type, tools, stage timings and the usage
    records with their cost; voice-edit turns (notes / reminders, outside any conversation) are
    added as one group per day."""
    repo = ConversationRepo(db)
    groups: list[tuple[dict[str, Any], Sequence[Turn]]] = []
    for c in repo.list_for_device(device_id):
        turns = repo.turns(c.id, account_id)
        if turns:
            groups.append(({"id": c.id, "started_at": c.started_at, "last_activity_at": c.last_activity_at}, turns))
    if details:
        q = select(Turn).where(Turn.device_id == device_id, col(Turn.conversation_id).is_(None))
        if account_id is not None:
            q = q.where(Turn.account_id == account_id)
        by_day: dict[str, list[Turn]] = {}
        for t in db.exec(q.order_by(col(Turn.id))).all():
            by_day.setdefault(t.created_at.strftime("%Y-%m-%d"), []).append(t)
        for n, day_turns in enumerate(by_day.values(), start=1):
            meta = {"id": -n, "edits": True, "started_at": day_turns[0].created_at, "last_activity_at": day_turns[-1].created_at}
            groups.append((meta, day_turns))
    usage = _usage_by_turn(db, [t.id for _, ts in groups for t in ts]) if details else {}
    return [{**meta, "turns": [_turn_out(t, usage, details) for t in turns]} for meta, turns in groups]


def _usage_by_turn(db: Session, turn_ids: list[int | None]) -> dict[int, list[UsageRecord]]:
    out: dict[int, list[UsageRecord]] = {}
    ids = [i for i in turn_ids if i is not None]
    for start in range(0, len(ids), 500):  # keep the IN list short
        rows = db.exec(select(UsageRecord).where(col(UsageRecord.turn_id).in_(ids[start : start + 500]))).all()
        for u in rows:
            out.setdefault(u.turn_id, []).append(u)  # type: ignore[arg-type]
    return out


def _turn_out(t: Turn, usage: dict[int, list[UsageRecord]], details: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": t.id,
        "status": t.status,
        "language": t.language,
        "user_text": t.user_text,
        "assistant_text": t.assistant_text,
        "ttfa_ms": t.ttfa_device_ms or t.ttfa_server_ms,
        "created_at": t.created_at,
    }
    if not details:
        return out
    records = usage.get(t.id or 0, [])
    priced = [u.cost_micro_gbp for u in records if u.cost_micro_gbp is not None]
    out.update(
        mode=t.mode,
        tools=[x for x in t.tools.split(",") if x],
        search=t.search_note or None,
        error=t.error,
        stt_ms=t.stt_ms,
        llm_first_token_ms=t.llm_first_token_ms,
        tts_first_audio_ms=t.tts_first_audio_ms,
        cost_gbp=sum(priced) / 1_000_000 if priced else (0.0 if not records else None),
        unpriced=sum(1 for u in records if u.cost_micro_gbp is None and not u.mock),
        billable=any(u.billable for u in records) if records else None,
        mock=bool(records) and all(u.mock for u in records),
        usage=[
            {
                "kind": u.kind,
                "provider": u.provider,
                "model": u.model,
                "unit": u.unit,
                "quantity": u.quantity,
                "cost_gbp": None if u.cost_micro_gbp is None else u.cost_micro_gbp / 1_000_000,
                "billable": u.billable,
            }
            for u in records
        ],
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


class ItemPinBody(BaseModel):
    pinned: bool


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
