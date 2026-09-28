"""Offline mock providers (BUDDYAI_MOCK_PROVIDERS=true, tests, demos without API keys)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np

from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest
from app.providers.stt.base import PartialCallback, STTProvider, STTSession
from app.providers.tts.base import PCMChunk, TTSProvider, TTSRequest

MOCK_TRANSCRIPTS = {"ro": "Ce vreme va fi mâine?", "en": "What will the weather be like tomorrow?"}
MOCK_REPLIES = {
    "ro": "Sigur, mâine va fi însorit, cu maxime de 24 de grade. Dimineața poate fi răcoare, așa că ia o jachetă.",
    "en": "Sure, tomorrow will be sunny, with highs of 24 degrees. The morning may be chilly, so take a jacket.",
}


class MockSTT(STTProvider):
    name = "mock_stt"
    model = "mock"

    def __init__(self, latency_s: float = 0.15) -> None:
        self.latency_s = latency_s

    async def start(self, language: str, sample_rate: int, on_partial: PartialCallback | None) -> STTSession:
        return _MockSTTSession(language, sample_rate, on_partial, self.latency_s)


class _MockSTTSession(STTSession):
    def __init__(self, language: str, rate: int, on_partial: PartialCallback | None, latency_s: float) -> None:
        self.language, self.rate, self.on_partial, self.latency_s = language, rate, on_partial, latency_s
        self.bytes = 0

    async def send(self, pcm: bytes) -> None:
        self.bytes += len(pcm)

    async def finish(self) -> str:
        await asyncio.sleep(self.latency_s)
        if self.bytes < self.rate * 2 * 0.3:  # < 300 ms of audio
            return ""
        return MOCK_TRANSCRIPTS.get(self.language, MOCK_TRANSCRIPTS["en"])

    async def close(self) -> None:
        pass


class MockLLM(LLMProvider):
    name = "mock_llm"

    def __init__(self, first_token_s: float = 0.2, per_token_s: float = 0.02) -> None:
        self.first_token_s, self.per_token_s = first_token_s, per_token_s

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        system = request.messages[0]["content"] if request.messages else ""
        lang = "ro" if "Romanian" in system else "en"
        await asyncio.sleep(self.first_token_s)
        words = MOCK_REPLIES[lang].split(" ")
        for i, w in enumerate(words):
            yield LLMChunk(delta=(w if i == 0 else " " + w))
            await asyncio.sleep(self.per_token_s)
        yield LLMChunk(input_tokens=sum(len(m["content"]) for m in request.messages) // 4, output_tokens=len(words))


class MockTTS(TTSProvider):
    name = "mock_tts"
    model = "mock"
    rate = 24000

    def __init__(self, first_audio_s: float = 0.08, ms_per_char: float = 45.0) -> None:
        self.first_audio_s, self.ms_per_char = first_audio_s, ms_per_char

    async def stream(self, text_chunks: AsyncIterator[str], request: TTSRequest) -> AsyncIterator[PCMChunk]:
        async for text in text_chunks:
            await asyncio.sleep(self.first_audio_s)
            n = int(self.rate * len(text) * self.ms_per_char / 1000)
            t = np.arange(n) / self.rate
            tone = (0.2 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes()
            step = self.rate * 2 // 10  # 100 ms pieces
            for off in range(0, len(tone), step):
                yield PCMChunk(tone[off : off + step], self.rate)
                await asyncio.sleep(0)
