"""OpenAI Realtime transcription (WebSocket), default model gpt-live-transcribe.

Turn detection is off: our server-side VAD decides the end of speech, then we commit the buffer
and wait for `...input_audio_transcription.completed`. Input must be PCM16 at 24 kHz, so the
16 kHz uplink is resampled here.

The realtime service sometimes stalls (handshake timeouts, a final transcript that never comes).
The utterance is therefore also kept locally, and when the realtime path fails it is sent once to
the regular (non-streaming) transcription endpoint instead: the user is not asked to repeat.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import wave

import av
import httpx
import numpy as np
import websockets

from app.providers.base import ProviderError
from app.providers.stt.base import PartialCallback, STTProvider, STTSession

log = logging.getLogger(__name__)
INPUT_RATE = 24000


class OpenAIRealtimeSTT(STTProvider):
    name = "openai_stt"

    def __init__(
        self,
        api_key: str,
        ws_url: str,
        model: str,
        finish_timeout_s: float = 4.0,
        prompt: str = "",
        rest_url: str = "https://api.openai.com/v1",
        fallback_model: str = "gpt-4o-mini-transcribe",
        open_timeout_s: float = 5.0,
    ) -> None:
        self.api_key = api_key
        self.ws_url = ws_url
        self.model = model
        self.finish_timeout_s = finish_timeout_s  # realtime final transcript; then the fallback
        self.prompt = prompt  # style guidance for the transcript (e.g. numbers as digits)
        self.rest_url = rest_url.rstrip("/")
        self.fallback_model = fallback_model
        self.open_timeout_s = open_timeout_s
        self._http = httpx.AsyncClient(timeout=20.0)

    async def start(self, language: str, sample_rate: int, on_partial: PartialCallback | None) -> STTSession:
        if not self.api_key:
            raise ProviderError("stt", "OpenAI API key is not configured")
        session = _OpenAISTTSession(self, language, sample_rate, on_partial)
        await session.open()
        return session


class _OpenAISTTSession(STTSession):
    def __init__(self, p: OpenAIRealtimeSTT, language: str, rate: int, on_partial: PartialCallback | None) -> None:
        self.p, self.language, self.rate, self.on_partial = p, language, rate, on_partial
        self.ws: websockets.ClientConnection | None = None
        self.resampler = av.AudioResampler(format="s16", layout="mono", rate=INPUT_RATE)
        self.partial = ""
        self.final: str | None = None
        self.error: str | None = None
        self.done = asyncio.Event()
        self._reader: asyncio.Task | None = None
        self._audio: list[bytes] = []  # the whole utterance (uplink rate), for the fallback
        self._realtime_ok = False

    async def open(self) -> None:
        """Connect to the realtime service. A failure is not fatal: the session then only collects
        audio and finish() uses the regular transcription endpoint."""
        try:
            self.ws = await websockets.connect(
                self.p.ws_url,
                additional_headers={"Authorization": f"Bearer {self.p.api_key}"},
                max_size=2**22,
                open_timeout=self.p.open_timeout_s,
            )
        except Exception as exc:
            log.warning("stt realtime connect failed (%s): the fallback will transcribe", exc)
            return
        transcription = {"model": self.p.model}
        if self.language and self.language != "auto":  # "auto": let the model detect it
            transcription["language"] = self.language
        if self.p.prompt:
            transcription["prompt"] = self.p.prompt
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": INPUT_RATE},
                            "transcription": transcription,
                            "turn_detection": None,
                        }
                    },
                },
            }
        )
        self._reader = asyncio.create_task(self._read())
        self._realtime_ok = True

    async def _send(self, event: dict) -> None:
        assert self.ws
        try:
            await self.ws.send(json.dumps(event))
        except websockets.ConnectionClosed as exc:
            reason = exc.rcvd.reason if exc.rcvd else str(exc)
            self.error = self.error or f"connection closed: {reason}"
            raise ProviderError("stt", self.error) from exc

    async def _read(self) -> None:
        assert self.ws
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type", "")
                if kind == "conversation.item.input_audio_transcription.delta":
                    self.partial += msg.get("delta", "")
                    if self.on_partial:
                        await self.on_partial(self.partial)
                elif kind == "conversation.item.input_audio_transcription.completed":
                    self.final = msg.get("transcript", "")
                    self.done.set()
                elif kind == "conversation.item.input_audio_transcription.failed":
                    self.error = str(msg.get("error"))
                    self.done.set()
                elif kind == "error":
                    self.error = str(msg.get("error"))
                    self.done.set()
        except websockets.ConnectionClosed as exc:
            if not self.done.is_set():
                self.error = self.error or f"connection closed: {exc}"
        finally:
            self.done.set()

    def _to_24k(self, pcm: bytes) -> bytes:
        samples = np.frombuffer(pcm, dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self.rate
        return b"".join(r.to_ndarray().tobytes() for r in self.resampler.resample(frame))

    async def send(self, pcm: bytes) -> None:
        if not pcm:
            return
        self._audio.append(pcm)
        if not self._realtime_ok or self.error:
            return
        data = pcm if self.rate == INPUT_RATE else self._to_24k(pcm)
        if data:
            try:
                await self._send(
                    {"type": "input_audio_buffer.append", "audio": base64.b64encode(data).decode("ascii")}
                )
            except ProviderError:
                self._realtime_ok = False  # connection dropped: the fallback will transcribe

    fallback_usage: tuple[str, float] | None = None  # (model, audio seconds) sent to the fallback

    async def finish(self) -> str:
        try:
            return await self._finish_realtime()
        except ProviderError as exc:
            log.warning("stt realtime failed (%s): using %s", exc, self.p.fallback_model)
        return await self._transcribe_file()

    async def _finish_realtime(self) -> str:
        if not self._realtime_ok:
            raise ProviderError("stt", "no realtime connection")
        if self.error:
            raise ProviderError("stt", self.error)
        await self._send({"type": "input_audio_buffer.commit"})
        try:
            await asyncio.wait_for(self.done.wait(), self.p.finish_timeout_s)
        except asyncio.TimeoutError as exc:
            raise ProviderError("stt", "timeout waiting for final transcript") from exc
        if self.error:
            raise ProviderError("stt", self.error)
        return (self.final if self.final is not None else self.partial).strip()

    async def _transcribe_file(self) -> str:
        """The whole utterance, once, through the regular transcription endpoint."""
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(b"".join(self._audio))
        # Billed separately by the provider: recorded as its own usage (app/pipeline/conversation.py).
        self.fallback_usage = (self.p.fallback_model, sum(map(len, self._audio)) / 2 / self.rate)
        data = {"model": self.p.fallback_model}
        if self.language and self.language != "auto":
            data["language"] = self.language
        if self.p.prompt:
            data["prompt"] = self.p.prompt
        try:
            r = await self.p._http.post(
                f"{self.p.rest_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.p.api_key}"},
                data=data,
                files={"file": ("speech.wav", wav.getvalue(), "audio/wav")},
            )
        except httpx.HTTPError as exc:
            raise ProviderError("stt", f"fallback transcription failed: {type(exc).__name__} {exc}") from exc
        if r.status_code != 200:
            raise ProviderError("stt", f"fallback transcription failed: HTTP {r.status_code} {r.text[:200]}")
        return str(r.json().get("text", "")).strip()

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if self.ws:
            # The close handshake with OpenAI takes ~4 s; the transcript is already in hand, so do
            # not hold up the reply for it.
            task = asyncio.create_task(_close_quietly(self.ws))
            _closing.add(task)
            task.add_done_callback(_closing.discard)


_closing: set[asyncio.Task] = set()  # keeps background closes referenced until they finish


async def _close_quietly(ws: websockets.ClientConnection) -> None:
    with contextlib.suppress(Exception):
        await ws.close()
