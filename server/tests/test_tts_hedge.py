"""HTTP TTS hedging: a fragment request that stalls before any audio gets a twin; first audio wins."""

from __future__ import annotations

import asyncio

from app.providers.base import ProviderError
from app.providers.tts.base import TTSRequest
from app.providers.tts.http_stream import _DONE, HTTPStreamingTTS


class ScriptedTTS(HTTPStreamingTTS):
    """Attempt n waits delays[n] seconds, then streams its label (or fails if the delay is None)."""

    def __init__(self, delays: list[float | None], hedge_after_s: float | None = 0.05) -> None:
        super().__init__(hedge_after_s=hedge_after_s)
        self.delays = delays
        self.started = 0
        self.finished: list[int] = []

    def check_configured(self) -> None:
        pass

    def build_request(self, text, request):
        return {}

    async def _attempt(self, text, request, out):
        n = self.started
        self.started += 1
        delay = self.delays[n]
        if delay is None:
            await out.put(ProviderError("tts", f"attempt {n} failed"))
            return
        await asyncio.sleep(delay)
        for part in (f"a{n}-1|".encode(), f"a{n}-2|".encode()):
            await out.put(part)
        await out.put(_DONE)
        self.finished.append(n)


def fetch(tts: ScriptedTTS) -> list:
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        await tts._fetch("text", TTSRequest(voice="v", language="ro"), q)
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        await asyncio.sleep(0.3)  # a cancelled loser must not finish
        return items

    return asyncio.run(go())


def test_fast_request_is_not_hedged() -> None:
    tts = ScriptedTTS([0.0])
    assert fetch(tts) == [b"a0-1|", b"a0-2|", _DONE]
    assert tts.started == 1


def test_stalled_request_gets_a_twin_and_loser_is_cancelled() -> None:
    tts = ScriptedTTS([0.2, 0.0])
    assert fetch(tts) == [b"a1-1|", b"a1-2|", _DONE]
    assert tts.started == 2 and tts.finished == [1]


def test_slow_original_still_wins_if_first() -> None:
    tts = ScriptedTTS([0.08, 0.2])
    assert fetch(tts) == [b"a0-1|", b"a0-2|", _DONE]
    assert tts.finished == [0]


def test_failure_before_audio_is_retried_once() -> None:
    tts = ScriptedTTS([None, 0.0])
    assert fetch(tts) == [b"a1-1|", b"a1-2|", _DONE]


def test_both_failing_reports_error() -> None:
    tts = ScriptedTTS([None, None])
    items = fetch(tts)
    assert len(items) == 1 and isinstance(items[0], ProviderError)


def test_hedging_can_be_disabled() -> None:
    tts = ScriptedTTS([0.1], hedge_after_s=None)
    assert fetch(tts) == [b"a0-1|", b"a0-2|", _DONE]
    assert tts.started == 1
