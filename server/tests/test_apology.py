"""A question that could not be transcribed: an apology in the user's language, not charged, no error screen;
the reason an aborted turn stopped is kept."""

import asyncio

from app.device_settings import DeviceSettings
from app.gateway.device_ws import abort_text
from app.pipeline.apology import NOT_UNDERSTOOD, not_understood
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext, TurnResult
from app.providers.base import ProviderError
from tests.test_items import FakeIO, FakeRouter


class StalledSTTPipeline(ConversationPipeline):
    async def _listen(self, turn, io):
        raise ProviderError("stt", "timeout waiting for final transcript")


def _run(language: str, fallback: str | None = None, mode: str = "chat"):
    pipeline = StalledSTTPipeline(FakeRouter(None), ChunkerConfig())
    turn = TurnContext(1, "s", "dev", language, DeviceSettings(), 16000, fallback_language=fallback)
    turn.mode = mode
    io = FakeIO()
    result = asyncio.run(pipeline.run(turn, io))
    return result, io.sent, turn


def test_stalled_stt_apologises_in_the_users_language():
    result, sent, turn = _run("ro")
    assert result == TurnResult("error", "stt_failed", "timeout waiting for final transcript", notified=True)
    assert ("llm_text", {"delta": NOT_UNDERSTOOD["ro"]}) in sent
    assert any(t == "tts_start" for t, _ in sent)  # spoken too
    assert turn.assistant_text == NOT_UNDERSTOOD["ro"]


def test_auto_language_uses_the_last_detected_one():
    _, sent, _ = _run("auto", fallback="de")
    assert ("llm_text", {"delta": NOT_UNDERSTOOD["de"]}) in sent


def test_edit_modes_show_the_apology_without_speaking():
    _, sent, _ = _run("en", mode="note")
    assert sent == [("llm_display", {"text": NOT_UNDERSTOOD["en"]})]


def test_unknown_language_falls_back_to_english():
    assert not_understood("xx") == ("en", NOT_UNDERSTOOD["en"])


def test_abort_reason_is_explained():
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000)
    turn.abort_reason = "timeout"
    assert "not charged" in abort_text(TurnResult("aborted"), turn)
    assert abort_text(TurnResult("completed"), turn) is None
