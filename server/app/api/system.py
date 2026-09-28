"""Server info, provider keys (write-only, masked) and provider connection tests."""

from __future__ import annotations

import asyncio
import socket

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app import languages
from app.config import get_settings
from app.device_settings import DeviceSettings
from app.providers.base import ProviderError
from app.providers.llm.base import LLMRequest
from app.security import require_admin

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_admin)])

KEY_NAMES = ("openai_api_key", "dashscope_api_key", "azure_speech_key", "azure_speech_region")


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # no packets are sent for UDP connect
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def base_url() -> str:
    s = get_settings()
    return s.public_url.rstrip("/") or f"http://{lan_ip()}:{s.port}"


def _mask(value: str) -> str:
    return "" if not value else (value[:3] + "…" + value[-3:] if len(value) > 8 else "•••")


@router.get("/info")
def info() -> dict:
    s = get_settings()
    return {
        "version": "0.1.0",
        "device_ws_url": base_url().replace("http", "ws", 1) + "/ws/device",
        "base_url": base_url(),
        "mdns_enabled": s.mdns_enabled,
        "mock_providers": s.mock_providers,
        "ai_profile": s.ai_profile,
        "keys": {k: (s.provider_key(k) if k == "azure_speech_region" else _mask(s.provider_key(k))) for k in KEY_NAMES},
    }


class KeysBody(BaseModel):
    openai_api_key: str | None = None
    dashscope_api_key: str | None = None
    azure_speech_key: str | None = None
    azure_speech_region: str | None = None


@router.put("/keys")
def set_keys(body: KeysBody, request: Request) -> dict:
    get_settings().store_provider_keys({k: v.strip() for k, v in body.model_dump().items() if v})
    request.app.state.router._cache.clear()  # rebuild provider clients with the new keys
    return info()


@router.post("/test/{target}")
async def test_provider(target: str, request: Request) -> dict:
    """target: llm | stt | tts_<language code> (e.g. tts_en, tts_ro, tts_de)"""
    router_ = request.app.state.router
    settings = DeviceSettings()
    try:
        if target == "llm":
            llm, model = router_.llm(settings)
            text = ""
            req = LLMRequest([{"role": "user", "content": "Say OK."}], model, 8, router_.llm_params())
            async for chunk in llm.stream(req):
                text += chunk.delta
            return {"ok": True, "detail": f"{model}: {text.strip()[:40]}"}
        if target.startswith("tts_") and target[4:] in languages.supported_codes():
            from app.providers.tts.base import TTSRequest

            lang = target[4:]
            sel = router_.tts(lang, settings)

            async def one():
                yield languages.sample_sentence(lang)

            n = 0
            async for pcm in sel.provider.stream(one(), TTSRequest(sel.voice, lang)):
                n += len(pcm.pcm)
            return {"ok": n > 0, "detail": f"{sel.provider.name}/{sel.voice}: {n / 48000:.2f}s audio"}
        if target == "stt":
            stt = router_.stt("auto")
            session = await stt.start("auto", 16000, None)
            try:
                await session.send(b"\x00\x00" * 16000)
                text = await asyncio.wait_for(session.finish(), 10)
            finally:
                await session.close()
            return {"ok": True, "detail": f"{stt.model}: connected (silence -> {text!r})"}
    except (ProviderError, asyncio.TimeoutError) as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": False, "detail": f"unknown target {target!r}"}
