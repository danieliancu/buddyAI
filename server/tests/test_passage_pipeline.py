"""The whole reply path: a model reply with a foreign passage is heard in two voices, while the screen, the
history and the bill stay right; a reply without passages goes exactly the way it did before."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from app.device_settings import DeviceSettings
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest
from app.providers.tts.base import PCMChunk, TTSProvider
from app.providers.tts.multilang import Route
from tests.test_items import FakeIO, FakeRouter


class Recorder(TTSProvider):
    model = "rec"

    def __init__(self, name: str) -> None:
        self.name = name
        self.heard: list[tuple[str, str, str]] = []  # (voice, language, text)
        self.streams = 0

    async def stream(self, text_chunks, request):
        self.streams += 1
        async for text in text_chunks:
            self.heard.append((request.voice, request.language, text))
            yield PCMChunk(b"\x10\x00" * 4800, 24000)  # 200 ms: enough for Opus packets


class Words(LLMProvider):
    name = "words"

    def __init__(self, text: str) -> None:
        self.text = text

    async def stream(self, request: LLMRequest):
        for i in range(0, len(self.text), 3):  # small deltas: tags arrive split
            yield LLMChunk(delta=self.text[i : i + 3])
            await asyncio.sleep(0)
        yield LLMChunk(input_tokens=1, output_tokens=1)


class Router(FakeRouter):
    def __init__(self, llm, main: Recorder, other: Recorder) -> None:
        super().__init__(llm)
        self.main, self.other = main, other

    def tts(self, language, s):
        return NS(provider=self.main, voice="ro-RO-AlinaNeural", instructions="")

    def tts_passage_routes(self, language, settings, main_language):
        return [Route(self.other, f"{language}-voice", language)]


def reply(text: str, language: str = "ro"):
    main, other = Recorder("azure_tts"), Recorder("other_tts")
    pipeline = ConversationPipeline(
        Router(Words(text), main, other), ChunkerConfig(), messages_builder=lambda t, x: [{"role": "user", "content": x}]
    )
    turn = TurnContext(1, "s", "dev", language, DeviceSettings(), 16000)
    turn.user_text = "Cum spui în engleză britanică mașina mea e după colț?"
    io = FakeIO()
    asyncio.run(pipeline._reply(turn, io))
    return turn, io, main, other


def test_passage_is_heard_in_its_language_and_never_shown_as_a_tag() -> None:
    turn, io, main, other = reply(
        "În engleză britanică se spune: [[lang:en]]My car is just around the corner.[[/lang]] Sună natural."
    )
    assert other.heard == [("en-voice", "en", "My car is just around the corner.")]
    assert [h[1] for h in main.heard] == ["ro"] * len(main.heard)
    assert "My car" not in " ".join(h[2] for h in main.heard)
    caption = "".join(f["delta"] for t, f in io.sent if t == "llm_text")
    assert "[[" not in caption and "lang" not in caption
    assert caption == "În engleză britanică se spune: My car is just around the corner. Sună natural."
    assert turn.assistant_text == caption
    assert [f["language"] for t, f in io.sent if t == "tts_start"] == ["ro"]  # the watch is told as before
    tts_usage = {(u.provider, u.model): u.quantity for u in turn.usage if u.kind == "tts"}
    assert set(tts_usage) == {("azure_tts", "rec"), ("other_tts", "rec")}
    assert tts_usage[("other_tts", "rec")] == len("My car is just around the corner.")


def test_reply_without_passages_is_one_stream_as_before() -> None:
    turn, io, main, other = reply("Mașina mea e după colț, chiar aici lângă bloc. Vrei să mergem acum?")
    assert main.streams == 1 and other.heard == []
    assert "".join(h[2] for h in main.heard).replace(" ", "") == "Mașinameaedupăcolț,chiaraicilângăbloc.Vreisămergemacum?"
    assert [u.provider for u in turn.usage if u.kind == "tts"] == ["azure_tts"]


def test_whole_reply_in_another_language() -> None:
    turn, io, main, other = reply("[[lang:en]]My car is just around the corner.[[/lang]]")
    assert other.heard == [("en-voice", "en", "My car is just around the corner.")]
    assert main.heard == []
    assert not any(t == "llm_display" for t, _ in io.sent)  # the tag is not a value for the big screen
    assert turn.assistant_text == "My car is just around the corner."
