"""MultiLangTTS: reply-language runs keep today's single stream; foreign passages get their own voice,
start early (no gap), fall back route by route and finally to the reply's own voice."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import pytest

from app.providers.base import ProviderError
from app.providers.tts.base import PCMChunk, TTSProvider, TTSRequest
from app.providers.tts.multilang import MultiLangTTS, Route


@dataclass
class Call:
    request: TTSRequest
    started: float
    texts: list[str] = field(default_factory=list)
    ended: float | None = None


class FakeTTS(TTSProvider):
    model = "fake-model"

    def __init__(self, name: str, delay: float = 0.0, fail_connect: bool = False, fail_before: bool = False,
                 break_after: bool = False, silent: bool = False, reports: bool = False) -> None:
        self.name = name
        self.delay, self.fail_connect, self.fail_before = delay, fail_connect, fail_before
        self.break_after, self.silent = break_after, silent
        self.reports_requests = reports
        self.calls: list[Call] = []

    async def stream(self, text_chunks, request: TTSRequest):
        call = Call(request, time.monotonic())
        self.calls.append(call)
        if self.fail_connect:
            raise ProviderError("tts", f"{self.name} connect failed")
        async for text in text_chunks:
            call.texts.append(text)
            if request.on_request is not None:
                request.on_request(len(text))
            if self.fail_before:
                raise ProviderError("tts", f"{self.name} failed")
            await asyncio.sleep(self.delay)
            if self.silent:
                continue
            yield PCMChunk(f"{self.name}/{request.language}:{text}|".encode(), 24000)
            if self.break_after:
                raise ProviderError("tts", f"{self.name} broke off")
        call.ended = time.monotonic()


def frags(*items: tuple[str, str]):
    async def gen():
        for item in items:
            await asyncio.sleep(0)
            yield item

    return gen()


def run(tts: MultiLangTTS, *items: tuple[str, str]) -> str:
    async def go():
        out = b""
        async for pcm in tts.stream(frags(*items)):
            out += pcm.pcm
        return out.decode()

    return asyncio.run(go())


def make(main: FakeTTS, routes: list[Route] | None = None, **kw) -> MultiLangTTS:
    return MultiLangTTS(Route(main, "voice", "ro"), lambda lang: list(routes or []), **kw)


def test_reply_without_passages_is_one_stream_with_the_same_fragments() -> None:
    main = FakeTTS("main")
    passage = FakeTTS("azure")
    tts = make(main, [Route(passage, "v", "en")])
    assert run(tts, ("ro", "Bună ziua."), ("ro", "Ce mai faci?")) == "main/ro:Bună ziua.|main/ro:Ce mai faci?|"
    assert len(main.calls) == 1 and main.calls[0].texts == ["Bună ziua.", "Ce mai faci?"]
    assert main.calls[0].request.voice == "voice" and main.calls[0].request.language == "ro"
    assert passage.calls == []


def test_the_reply_voice_is_opened_before_any_text() -> None:
    main = FakeTTS("main")
    tts = make(main)

    async def go():
        q: asyncio.Queue = asyncio.Queue()

        async def slow():
            while (x := await q.get()) is not None:
                yield x

        agen = tts.stream(slow())
        first = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.05)
        opened = len(main.calls)  # connected while the model is still thinking (as before)
        await q.put(("ro", "Salut."))
        chunk = await first
        await q.put(None)
        rest = [c async for c in agen]
        return opened, chunk, rest

    opened, chunk, rest = asyncio.run(go())
    assert opened == 1 and chunk.pcm == b"main/ro:Salut.|" and rest == []


def test_passage_gets_its_own_voice_and_order_is_kept() -> None:
    main, en = FakeTTS("main"), FakeTTS("azure")
    tts = make(main, [Route(en, "en-GB-SoniaNeural", "en")])
    out = run(tts, ("ro", "Se spune:"), ("en", "My car is around the corner."), ("ro", "Atât."))
    assert out == "main/ro:Se spune:|azure/en:My car is around the corner.|main/ro:Atât.|"
    assert [c.texts for c in main.calls] == [["Se spune:"], ["Atât."]]
    assert en.calls[0].request.voice == "en-GB-SoniaNeural" and en.calls[0].request.language == "en"
    assert tts.fallbacks == 0


def test_several_languages_in_one_reply() -> None:
    main, voices = FakeTTS("main"), FakeTTS("multi")
    tts = MultiLangTTS(Route(main, "v", "bg"), lambda lang: [Route(voices, f"{lang}-voice", lang)])
    out = run(tts, ("bg", "На френски:"), ("fr", "Ma voiture."), ("bg", "На унгарски:"), ("hu", "Az autóm."))
    assert out == "main/bg:На френски:|multi/fr:Ma voiture.|main/bg:На унгарски:|multi/hu:Az autóm.|"


def test_passage_is_synthesized_while_the_audio_before_it_plays() -> None:
    main, en = FakeTTS("main", delay=0.3), FakeTTS("azure", delay=0.0)
    tts = make(main, [Route(en, "v", "en")])
    run(tts, ("ro", "Se spune:"), ("en", "My car."), ("ro", "Atât."))
    first_run = main.calls[0]
    assert en.calls[0].started < first_run.ended  # no gap when the language changes
    assert main.calls[1].started < first_run.ended  # the reply's voice is reopened early too


def test_failed_route_hands_over_to_the_next() -> None:
    main, bad, good = FakeTTS("main"), FakeTTS("qwen", fail_before=True), FakeTTS("azure")
    tts = make(main, [Route(bad, "v", "fr"), Route(good, "fr-FR-DeniseNeural", "fr")])
    assert run(tts, ("fr", "Bonjour.")) == "azure/fr:Bonjour.|"
    assert tts.fallbacks == 1


def test_connect_failure_hands_over_to_the_next() -> None:
    main, bad, good = FakeTTS("main"), FakeTTS("qwen", fail_connect=True), FakeTTS("azure")
    tts = make(main, [Route(bad, "v", "fr"), Route(good, "v", "fr")])
    assert run(tts, ("ro", "Zice:"), ("fr", "Bonjour.")) == "main/ro:Zice:|azure/fr:Bonjour.|"


def test_silent_route_gives_way_after_the_first_audio_deadline() -> None:
    main, slow, good = FakeTTS("main"), FakeTTS("openai", delay=2.0), FakeTTS("azure")
    tts = make(main, [Route(slow, "v", "en"), Route(good, "v", "en")], first_audio_s=0.1)
    t0 = time.monotonic()
    assert run(tts, ("en", "Hello.")) == "azure/en:Hello.|"
    assert time.monotonic() - t0 < 1.0


def test_route_without_audio_counts_as_failed() -> None:
    main, mute, good = FakeTTS("main"), FakeTTS("x", silent=True), FakeTTS("azure")
    tts = make(main, [Route(mute, "v", "en"), Route(good, "v", "en")])
    assert run(tts, ("en", "Hello.")) == "azure/en:Hello.|"


def test_all_routes_fail_the_passage_sounds_as_before() -> None:
    """The last resort is the reply's own voice in the reply language: what was heard before this change."""
    main = FakeTTS("main")
    tts = make(main, [Route(FakeTTS("a", fail_before=True), "v", "en"), Route(FakeTTS("b", fail_connect=True), "v", "en")])
    assert run(tts, ("ro", "Se spune:"), ("en", "My car."), ("ro", "Atât.")) == "main/ro:Se spune:|main/ro:My car.|main/ro:Atât.|"
    assert main.calls[1].request.language == "ro" and main.calls[1].request.voice == "voice"
    assert tts.fallbacks == 1


def test_no_routes_at_all_reads_the_passage_as_before() -> None:
    main = FakeTTS("main")
    assert run(make(main, []), ("en", "Hi.")) == "main/ro:Hi.|"


def test_routing_error_never_costs_the_turn() -> None:
    main = FakeTTS("main")

    def broken(lang):
        raise KeyError(lang)

    tts = MultiLangTTS(Route(main, "v", "ro"), broken)
    assert run(tts, ("en", "Hi.")) == "main/ro:Hi.|"


def test_passage_breaking_off_after_audio_does_not_end_the_reply() -> None:
    main, flaky = FakeTTS("main"), FakeTTS("azure", break_after=True)
    tts = make(main, [Route(flaky, "v", "en")])
    out = run(tts, ("ro", "Zice:"), ("en", "My car."), ("ro", "Gata."))
    assert out == "main/ro:Zice:|azure/en:My car.|main/ro:Gata.|"


def test_reply_voice_failing_is_still_the_turns_error() -> None:
    main = FakeTTS("main", fail_before=True)
    with pytest.raises(ProviderError):
        run(make(main), ("ro", "Salut."))


def test_reply_voice_failing_as_last_resort_is_the_turns_error() -> None:
    main = FakeTTS("main", fail_before=True)
    with pytest.raises(ProviderError):
        run(make(main, [Route(FakeTTS("a", fail_before=True), "v", "en")]), ("en", "Hi."))


def test_reply_starting_with_a_passage_ignores_the_unused_early_connection() -> None:
    main, en = FakeTTS("main", fail_connect=True), FakeTTS("azure")
    tts = make(main, [Route(en, "v", "en")])
    assert run(tts, ("en", "My car.")) == "azure/en:My car.|"


def test_early_connection_failure_is_reported_when_text_comes() -> None:
    with pytest.raises(ProviderError):
        run(make(FakeTTS("main", fail_connect=True)), ("ro", "Salut."))


def test_times_are_spoken_for_the_route_language() -> None:
    main, en = FakeTTS("main"), FakeTTS("azure", fail_before=True)
    tts = make(main, [Route(en, "v", "en")])
    out = run(tts, ("ro", "La 14:30."), ("en", "At 14:30."))
    assert "main/ro:La paisprezece și treizeci." in out  # Romanian words, as before
    assert en.calls[0].texts == ["At 14:30."]  # English voice: digits, read in English
    assert "main/ro:At paisprezece și treizeci." in out  # fallback: exactly as before


def test_usage_is_counted_per_provider() -> None:
    main, en = FakeTTS("qwen_tts"), FakeTTS("azure_tts", reports=True)
    tts = make(main, [Route(en, "v", "en")])
    run(tts, ("ro", "Se spune:"), ("en", "My car."), ("ro", "Atât."))
    assert tts.usage == {("qwen_tts", "fake-model"): len("Se spune:") + len("Atât."), ("azure_tts", "fake-model"): len("My car.")}


def test_failed_route_is_billed_for_what_it_was_sent() -> None:
    main, bad, good = FakeTTS("main"), FakeTTS("openai", fail_before=True, reports=True), FakeTTS("azure", reports=True)
    tts = make(main, [Route(bad, "v", "en"), Route(good, "v", "en")])
    run(tts, ("en", "Hi."))
    assert tts.usage == {("openai", "fake-model"): 3, ("azure", "fake-model"): 3}
