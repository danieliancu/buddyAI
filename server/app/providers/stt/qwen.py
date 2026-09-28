"""Qwen Audio ASR streaming (Model Studio realtime recognition, run-task/finish-task WebSocket protocol).

Default model: qwen-audio-3.1-asr-flash-streaming (RO + EN via language_hints).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

import websockets

from app.providers.base import ProviderError
from app.providers.stt.base import PartialCallback, STTProvider, STTSession

log = logging.getLogger(__name__)

_END = object()


class QwenStreamingSTT(STTProvider):
    name = "qwen_asr"

    def __init__(self, api_key: str, ws_url: str, model: str, finish_timeout_s: float = 8.0) -> None:
        self.api_key = api_key
        self.ws_url = ws_url
        self.model = model
        self.finish_timeout_s = finish_timeout_s

    async def start(self, language: str, sample_rate: int, on_partial: PartialCallback | None) -> STTSession:
        if not self.api_key:
            raise ProviderError("stt", "DashScope API key is not configured")
        session = _QwenSTTSession(self, language, sample_rate, on_partial)
        await session.open()
        return session


class _QwenSTTSession(STTSession):
    def __init__(self, p: QwenStreamingSTT, language: str, rate: int, on_partial: PartialCallback | None) -> None:
        self.p = p
        self.language = language
        self.rate = rate
        self.on_partial = on_partial
        self.task_id = uuid.uuid4().hex
        self.ws: websockets.ClientConnection | None = None
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.error: str | None = None
        self.final_sentences: list[str] = []
        self.partial = ""
        self._tasks: list[asyncio.Task] = []

    async def open(self) -> None:
        try:
            self.ws = await websockets.connect(
                self.p.ws_url,
                additional_headers={"Authorization": f"Bearer {self.p.api_key}"},
                max_size=2**22,
                open_timeout=5,
            )
        except Exception as exc:
            raise ProviderError("stt", f"connect failed: {exc}") from exc
        await self.ws.send(
            json.dumps(
                {
                    "header": {"action": "run-task", "task_id": self.task_id, "streaming": "duplex"},
                    "payload": {
                        "task_group": "audio",
                        "task": "asr",
                        "function": "recognition",
                        "model": self.p.model,
                        "parameters": {
                            "format": "pcm",
                            "sample_rate": self.rate,
                            # Our own VAD decides end of utterance; keep ASR sentence splitting lenient.
                            "max_sentence_silence": 1300,
                            **({"language_hints": [self.language]} if self.language not in ("", "auto") else {}),
                        },
                        "input": {},
                    },
                }
            )
        )
        self._tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._writer())]

    async def _reader(self) -> None:
        assert self.ws
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                event = msg.get("header", {}).get("event")
                if event == "task-started":
                    self.started.set()
                elif event == "result-generated":
                    sentence = msg.get("payload", {}).get("output", {}).get("sentence") or {}
                    text = sentence.get("text", "") or ""
                    if sentence.get("heartbeat"):
                        continue
                    if sentence.get("sentence_end"):
                        if text:
                            self.final_sentences.append(text)
                        self.partial = ""
                    else:
                        self.partial = text
                    if self.on_partial:
                        await self.on_partial(self.text)
                elif event == "task-finished":
                    self.finished.set()
                    return
                elif event == "task-failed":
                    header = msg.get("header", {})
                    self.error = f"{header.get('error_code')}: {header.get('error_message')}"
                    self.started.set()
                    self.finished.set()
                    return
        except websockets.ConnectionClosed as exc:
            if not self.finished.is_set():
                self.error = self.error or f"connection closed: {exc}"
        finally:
            self.started.set()
            self.finished.set()

    async def _writer(self) -> None:
        assert self.ws
        await self.started.wait()
        while True:
            item = await self.queue.get()
            if self.error:
                return
            if item is _END:
                await self.ws.send(
                    json.dumps(
                        {
                            "header": {"action": "finish-task", "task_id": self.task_id, "streaming": "duplex"},
                            "payload": {"input": {}},
                        }
                    )
                )
                return
            await self.ws.send(item)  # binary PCM

    @property
    def text(self) -> str:
        parts = self.final_sentences + ([self.partial] if self.partial else [])
        return " ".join(p.strip() for p in parts if p.strip())

    async def send(self, pcm: bytes) -> None:
        if pcm:
            self.queue.put_nowait(pcm)

    async def finish(self) -> str:
        self.queue.put_nowait(_END)
        try:
            await asyncio.wait_for(self.finished.wait(), self.p.finish_timeout_s)
        except asyncio.TimeoutError as exc:
            raise ProviderError("stt", "timeout waiting for final transcript") from exc
        if self.error:
            raise ProviderError("stt", self.error)
        return self.text

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
