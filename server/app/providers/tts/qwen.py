"""Qwen TTS realtime (WebSocket, commit mode): one connection per turn, one commit per text fragment."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncIterator

import websockets

from app.providers.base import ProviderError
from app.providers.tts.base import PCMChunk, TTSProvider, TTSRequest

log = logging.getLogger(__name__)

LANGUAGE_TYPES = {"en": "English", "ro": "Auto"}
SAMPLE_RATE = 24000


class QwenRealtimeTTS(TTSProvider):
    name = "qwen_tts"

    def __init__(self, api_key: str, ws_url: str, model: str) -> None:
        self.api_key = api_key
        self.ws_url = ws_url
        self.model = model

    async def stream(self, text_chunks: AsyncIterator[str], request: TTSRequest) -> AsyncIterator[PCMChunk]:
        if not self.api_key:
            raise ProviderError("tts", "DashScope API key is not configured")
        url = f"{self.ws_url}?model={self.model}"
        try:
            ws = await websockets.connect(
                url, additional_headers={"Authorization": f"Bearer {self.api_key}"}, max_size=2**23, open_timeout=5
            )
        except Exception as exc:
            raise ProviderError("tts", f"connect failed: {exc}") from exc

        async def send(event: dict) -> None:
            await ws.send(json.dumps(event, ensure_ascii=False))

        async def writer() -> None:
            await send(
                {
                    "type": "session.update",
                    "session": {
                        "mode": "commit",
                        "voice": request.voice,
                        "language_type": LANGUAGE_TYPES.get(request.language, "Auto"),
                        "response_format": "pcm",
                        "sample_rate": SAMPLE_RATE,
                    },
                }
            )
            async for text in text_chunks:
                await send({"type": "input_text_buffer.append", "text": text})
                await send({"type": "input_text_buffer.commit"})
            await send({"type": "session.finish"})

        writer_task = asyncio.create_task(writer())
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "response.audio.delta":
                    yield PCMChunk(base64.b64decode(msg["delta"]), SAMPLE_RATE)
                elif kind == "session.finished":
                    break
                elif kind == "error":
                    raise ProviderError("tts", str(msg.get("error")))
            if writer_task.done() and writer_task.exception():
                raise writer_task.exception()  # type: ignore[misc]
        except websockets.ConnectionClosed as exc:
            raise ProviderError("tts", f"connection closed: {exc}") from exc
        finally:
            writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await writer_task
            with contextlib.suppress(Exception):
                await ws.close()
