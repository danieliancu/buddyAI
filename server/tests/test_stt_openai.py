"""OpenAI realtime transcription: the session asks for numbers as digits (config stt.prompt)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.providers.stt import openai as stt_openai
from app.providers.stt.openai import OpenAIRealtimeSTT


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(3600)  # no server events in this test
        raise StopAsyncIteration

    async def close(self) -> None:
        pass


def _session_update(prompt: str, language: str) -> dict:
    ws = FakeWS()

    async def connect(*args, **kwargs):
        return ws

    async def run() -> None:
        original = stt_openai.websockets.connect
        stt_openai.websockets.connect = connect
        try:
            session = await OpenAIRealtimeSTT("sk-test", "wss://x", "gpt-live-transcribe", prompt=prompt).start(
                language, 16000, None
            )
            await session.close()
        finally:
            stt_openai.websockets.connect = original

    asyncio.run(run())
    return ws.sent[0]["session"]["audio"]["input"]["transcription"]


def test_prompt_is_sent_with_the_transcription_settings() -> None:
    t = _session_update("Write numbers with digits.", "ro")
    assert t == {"model": "gpt-live-transcribe", "language": "ro", "prompt": "Write numbers with digits."}


def test_no_prompt_key_without_a_prompt() -> None:
    assert "prompt" not in _session_update("", "auto")


def test_openai_profile_asks_for_digits() -> None:
    cfg = json.loads((Path(__file__).parent.parent / "config" / "providers.openai.json").read_text(encoding="utf-8"))
    assert "digits" in cfg["stt"]["prompt"]


class OpenFakeWS(FakeWS):
    state = stt_openai.State.OPEN


def test_a_spare_connection_skips_the_handshake() -> None:
    """With spares, a turn takes a connection opened in advance (its own: configured for this turn only)."""
    opened: list[OpenFakeWS] = []

    async def connect(*args, **kwargs):
        ws = OpenFakeWS()
        opened.append(ws)
        return ws

    async def run() -> None:
        original = stt_openai.websockets.connect
        stt_openai.websockets.connect = connect
        try:
            p = OpenAIRealtimeSTT("sk-test", "wss://x", "gpt-live-transcribe", spares=1)
            first = await p.start("ro", 16000, None)   # cold: connects itself, and the keeper opens a spare
            await asyncio.sleep(0.05)
            assert len(opened) == 2 and len(p._spare) == 1
            second = await p.start("en", 16000, None)  # takes the spare, a new one replaces it
            assert second.ws is opened[1] and first.ws is opened[0]
            assert opened[1].sent[0]["session"]["audio"]["input"]["transcription"]["language"] == "en"
            await asyncio.sleep(0.05)
            assert len(opened) == 3 and len(p._spare) == 1
            p._keeper.cancel()
            await first.close()
            await second.close()
        finally:
            stt_openai.websockets.connect = original

    asyncio.run(run())


def test_no_spares_by_default() -> None:
    assert OpenAIRealtimeSTT("sk-test", "wss://x", "m").take_spare() is None
