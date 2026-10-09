"""Listening: silence before the first word is never sent to the (billed) speech-to-text."""

from __future__ import annotations

import asyncio

from app.device_settings import DeviceSettings
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import PREROLL_MS, UPLINK_RATE, ConversationPipeline
from app.pipeline.turn import TurnContext
from app.pipeline.vad import EnergyProbability
from app.providers.stt.base import STTProvider, STTSession
from tests.test_items import FakeIO
from tests.test_vad import silence, tone

CHUNK_MS = 60


class RecordingSTT(STTProvider):
    name, model = "rec", "rec"

    def __init__(self) -> None:
        self.started = 0
        self.sent = 0

    async def start(self, language, sample_rate, on_partial) -> STTSession:
        self.started += 1
        stt = self

        class Session(STTSession):
            async def send(self, pcm: bytes) -> None:
                stt.sent += len(pcm)

            async def finish(self) -> str:
                return "hello"

            async def close(self) -> None:
                pass

        return Session()


class Router:
    def __init__(self, stt: STTProvider) -> None:
        self._stt = stt

    def stt(self, language):
        return self._stt


def _listen(audio: bytes, wait_s: int = 20) -> tuple[RecordingSTT, str, FakeIO]:
    stt = RecordingSTT()
    pipeline = ConversationPipeline(Router(stt), ChunkerConfig(), vad_factory=EnergyProbability)
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(wait_for_speech_s=wait_s), 16000)
    step = UPLINK_RATE * 2 * CHUNK_MS // 1000
    for off in range(0, len(audio), step):
        turn.audio_in.put_nowait((audio[off : off + step], 0.0))
    turn.audio_in.put_nowait(None)  # uplink ended
    io = FakeIO()
    text = asyncio.run(pipeline._listen(turn, io))
    return stt, text, io


def ms(n_bytes: int) -> float:
    return n_bytes / 2 / UPLINK_RATE * 1000


def test_silence_before_speech_is_not_sent() -> None:
    stt, text, io = _listen(silence(8000) + tone(1000) + silence(1500))
    assert text == "hello" and stt.started == 1
    assert ("listen_stop", {"reason": "vad"}) in io.sent
    # ~1 s of speech + up to PREROLL_MS before it + the trailing silence until the end is detected;
    # never the 8 s of silence before the first word.
    assert 1000 <= ms(stt.sent) <= 1000 + PREROLL_MS + 1500


def test_no_speech_sends_nothing_to_the_stt() -> None:
    stt, text, io = _listen(silence(6000), wait_s=5)
    # The connection opens at once (free, hides the handshake), but no audio is ever sent.
    assert text == "" and stt.sent == 0
    assert ("listen_stop", {"reason": "no_speech"}) in io.sent


def test_speech_after_a_long_wait_is_still_heard() -> None:
    stt, text, _ = _listen(silence(18000) + tone(800) + silence(1500), wait_s=20)
    assert text == "hello" and ms(stt.sent) < 4000


def _listen_with_partial(partial: str, audio: bytes) -> tuple[float, FakeIO]:
    """Like _listen, with an STT whose live transcript is `partial` from the first audio on."""
    sent = [0]

    class PartialSTT(RecordingSTT):
        async def start(self, language, sample_rate, on_partial) -> STTSession:
            class Session(STTSession):
                async def send(self, pcm: bytes) -> None:
                    sent[0] += len(pcm)
                    await on_partial(partial)

                async def finish(self) -> str:
                    return partial

                async def close(self) -> None:
                    pass

            return Session()

    pipeline = ConversationPipeline(Router(PartialSTT()), ChunkerConfig(), vad_factory=EnergyProbability)
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000)
    step = UPLINK_RATE * 2 * CHUNK_MS // 1000
    for off in range(0, len(audio), step):
        turn.audio_in.put_nowait((audio[off : off + step], 0.0))
    turn.audio_in.put_nowait(None)
    io = FakeIO()
    asyncio.run(pipeline._listen(turn, io))
    return ms(sent[0]), io


def test_a_thinking_pause_after_an_unfinished_sentence_does_not_end_it() -> None:
    # "remind me to ... (1.5 s) ... call mum": one question, the second part is heard too.
    heard_ms, io = _listen_with_partial("remind me to", tone(800) + silence(1500) + tone(800) + silence(3000))
    assert ("listen_stop", {"reason": "vad"}) in io.sent
    assert heard_ms >= 800 + 1500 + 800


def test_a_finished_sentence_still_ends_at_the_normal_pause() -> None:
    heard_ms, _ = _listen_with_partial("what time is it", tone(800) + silence(1500) + tone(800) + silence(3000))
    assert heard_ms < 800 + 1500  # ended in the first pause: the second part never reached the STT
