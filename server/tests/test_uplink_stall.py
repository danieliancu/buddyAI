"""A watch whose network stalls keeps its audio and catches up (firmware uplink_pump), so the server must wait for it
rather than decide the user said nothing or stopped."""

import asyncio
import time

from app.device_settings import DeviceSettings
from app.pipeline import conversation as conv
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.metrics import mono_ms
from app.pipeline.turn import TurnContext
from app.providers.mock import MockSTT
from tests.test_items import FakeIO, FakeRouter

FRAME = b"\0" * 1920  # 60 ms of silence


class Router(FakeRouter):
    def stt(self, language):
        return MockSTT(latency_s=0)


def _listen(monkeypatch, feed):
    monkeypatch.setattr(conv, "UPLINK_STALL_S", 0.02)
    monkeypatch.setattr(conv, "UPLINK_LOST_S", 0.2)
    pipeline = ConversationPipeline(Router(None), ChunkerConfig())
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000)
    io = FakeIO()

    async def run():
        task = asyncio.create_task(pipeline._listen(turn, io))
        await feed(turn)
        return await task

    started = time.monotonic()
    text = asyncio.run(run())
    return text, time.monotonic() - started, turn, io


def test_a_stall_before_the_first_word_does_not_end_the_turn(monkeypatch):
    async def feed(turn):
        await asyncio.sleep(0.1)  # longer than one look at the uplink, shorter than a lost link
        await turn.audio_in.put((FRAME, mono_ms()))  # the audio caught up
        await asyncio.sleep(0.02)
        assert turn.listening  # still listening: the question was not given up
        await turn.audio_in.put(None)

    text, _, turn, io = _listen(monkeypatch, feed)
    assert turn.audio_in.empty()  # everything that arrived was read
    assert ("listen_stop", {"reason": "no_speech"}) in io.sent and text == ""


def test_a_watch_that_never_comes_back_ends_the_turn(monkeypatch):
    async def feed(turn):
        pass

    text, took, turn, io = _listen(monkeypatch, feed)
    assert text == "" and took >= 0.2 and not turn.listening
    assert ("listen_stop", {"reason": "no_speech"}) in io.sent
