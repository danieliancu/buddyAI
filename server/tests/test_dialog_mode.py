"""Dialog mode (the watch listens again after every reply): turned on / off by voice in any language via
[[dialog:on]] / [[dialog:off]], a shorter wait for the next words, and turn_end "dialog" over the protocol."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.device_settings import DeviceSettings
from app.pipeline import conversation as conv_mod
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import DIALOG_WAIT_S, ConversationPipeline
from app.pipeline.display_tag import DIALOG_RULE, DisplayTagFilter
from app.pipeline.turn import TurnContext
from app.providers import mock
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest
from app.providers.router import ProviderRouter
from tests.test_e2e import server  # noqa: F401  (pytest fixture: a real server on a free port)
from tests.test_items import FakeIO, FakeRouter, _account

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_watch import FakeWatch, load_wav_16k  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"


def run(text: str, size: int) -> tuple[DisplayTagFilter, str]:
    f = DisplayTagFilter()
    out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size)) + f.flush()
    return f, out


@pytest.mark.parametrize("size", [1, 2, 4, 100])
def test_dialog_tags_are_removed_and_captured(size: int) -> None:
    f, out = run("[[dialog:off]] Cu plăcere, pa!", size)
    assert f.dialog == "off" and out == "Cu plăcere, pa!" and f.value is None
    f, out = run("Am pornit modul dialog. [[dialog:on]]", size)
    assert f.dialog == "on" and out.rstrip() == "Am pornit modul dialog."
    f, out = run("[[ Dialog : ON ]] Bine.", size)
    assert f.dialog == "on" and out == "Bine."


def test_dialog_tag_with_a_value() -> None:
    f, out = run("[[13:00]] Este ora 13. [[dialog:off]]", 3)
    assert f.value == "13:00" and f.dialog == "off" and out.rstrip() == "Este ora 13."


def test_no_tag_no_dialog() -> None:
    assert run("Un dialog în teatru e o conversație.", 5)[0].dialog is None


def test_the_rule_names_both_tags() -> None:
    assert "[[dialog:on]]" in DIALOG_RULE and "[[dialog:off]]" in DIALOG_RULE


class Words(LLMProvider):
    name = "words"

    def __init__(self, text: str) -> None:
        self.text = text

    async def stream(self, request: LLMRequest):
        for i in range(0, len(self.text), 4):
            yield LLMChunk(delta=self.text[i : i + 4])
        yield LLMChunk(input_tokens=1, output_tokens=1)


def reply(text: str, dialog: bool) -> tuple[TurnContext, FakeIO]:
    pipeline = ConversationPipeline(
        FakeRouter(Words(text)), ChunkerConfig(), messages_builder=lambda t, x: [{"role": "user", "content": x}]
    )
    turn = TurnContext(1, "s", "dev", "ro", DeviceSettings(), 16000)
    turn.user_text, turn.dialog = "gata", dialog
    io = FakeIO()
    asyncio.run(pipeline._reply(turn, io))
    return turn, io


def test_voice_turns_dialog_off_and_the_tag_is_never_shown() -> None:
    turn, io = reply("Cu plăcere, o zi bună! [[dialog:off]]", dialog=True)
    assert turn.dialog_set == "off"
    caption = "".join(f["delta"] for t, f in io.sent if t == "llm_text")
    assert "dialog" not in caption and "[[" not in caption
    assert "dialog" not in turn.assistant_text


def test_voice_turns_dialog_on() -> None:
    assert reply("Am pornit modul dialog. [[dialog:on]]", dialog=False)[0].dialog_set == "on"


def test_a_tag_that_changes_nothing_is_dropped() -> None:
    assert reply("Este ora 13. [[dialog:on]]", dialog=True)[0].dialog_set is None  # already on
    assert reply("Bine. [[dialog:off]]", dialog=False)[0].dialog_set is None  # already off


def test_only_the_tag_is_still_an_answer() -> None:
    assert reply("[[dialog:off]]", dialog=True)[0].dialog_set == "off"


def test_dialog_turns_wait_less_for_the_next_words(monkeypatch) -> None:
    seen: list[int] = []
    real = conv_mod.EndpointDetector

    def spy(*a, **kw):
        seen.append(kw["no_speech_timeout_ms"])
        return real(*a, **kw)

    monkeypatch.setattr(conv_mod, "EndpointDetector", spy)

    class Router(FakeRouter):
        def stt(self, language):
            return mock.MockSTT()

    for dialog in (False, True):
        pipeline = ConversationPipeline(Router(Words("x")), ChunkerConfig())
        turn = TurnContext(1, "s", "dev", "ro", DeviceSettings(wait_for_speech_s=20), 16000)
        turn.dialog = dialog
        turn.audio_in.put_nowait(None)  # the uplink ends at once: nothing said
        asyncio.run(pipeline.run(turn, FakeIO()))
    assert seen == [20_000, DIALOG_WAIT_S * 1000]


def _quick_wait(monkeypatch, ms: int) -> None:
    real = conv_mod.EndpointDetector
    monkeypatch.setattr(conv_mod, "EndpointDetector", lambda *a, **kw: real(*a, **{**kw, "no_speech_timeout_ms": ms}))


class SttRouter(FakeRouter):
    def stt(self, language):
        return mock.MockSTT()


def test_gated_watch_silence_counts_on_the_clock(monkeypatch) -> None:
    """A gated watch sends nothing before the first word: the wait for speech still ends on time (no_speech),
    long before a lost link would be assumed (UPLINK_LOST_S)."""
    import time

    _quick_wait(monkeypatch, 600)
    pipeline = ConversationPipeline(SttRouter(Words("x")), ChunkerConfig())
    turn = TurnContext(1, "s", "dev", "ro", DeviceSettings(), 16000)
    turn.uplink_gated = True
    t0 = time.monotonic()
    result = asyncio.run(pipeline.run(turn, FakeIO()))
    assert result.status == "no_speech"
    assert time.monotonic() - t0 < 3.0  # not UPLINK_STALL_S / UPLINK_LOST_S


def test_gated_watch_speech_after_silence_is_heard(monkeypatch) -> None:
    """Silence first (nothing sent), then the voice with its pre-roll: the question is transcribed."""
    from tests.test_vad import tone

    from app.pipeline.vad import EnergyProbability

    _quick_wait(monkeypatch, 5000)
    pipeline = ConversationPipeline(SttRouter(Words("Bună.")), ChunkerConfig(), vad_factory=EnergyProbability)
    turn = TurnContext(1, "s", "dev", "ro", DeviceSettings(), 16000)
    turn.uplink_gated = True
    mock.MOCK_TRANSCRIPTS["ro"] = "Salut"

    async def go():
        async def feed():
            await asyncio.sleep(1.2)  # the watch keeps this silence to itself
            speech = tone(1200)
            for i in range(0, len(speech), 1920):
                turn.audio_in.put_nowait((speech[i : i + 1920], 0.0))
            silence = bytes(1920)
            for _ in range(40):
                turn.audio_in.put_nowait((silence, 0.0))
            turn.audio_in.put_nowait(None)

        task = asyncio.create_task(feed())
        result = await pipeline.run(turn, FakeIO())
        await task
        return result

    assert asyncio.run(go()).status == "completed"
    assert turn.user_text


# --- over the real protocol ----------------------------------------------------------------------------


class Script(LLMProvider):
    name = "script"

    def __init__(self) -> None:
        self.steps: list[str] = []
        self.requests: list[LLMRequest] = []

    async def stream(self, request):
        self.requests.append(request)
        yield LLMChunk(input_tokens=10, output_tokens=5)
        yield LLMChunk(delta=self.steps.pop(0) if self.steps else "OK.")


async def _scenario(host: str, script: Script, acc: int) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        r = await http.get("/api/auth/status")
        if r.json()["needs_setup"]:
            await http.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
        else:
            await http.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        w = FakeWatch(f"ws://{host}/ws/device", "e2e-dialog")
        await w.connect()
        await w.send("hello", device_id="e2e-dialog", fw_version="t", hw_model="t", pairing_code="828282")
        await w.expect("pairing_pending")
        assert (await http.post("/api/devices/pair", json={"code": "828282", "name": "t", "account_id": acc})).status_code == 200
        token = (await w.expect("paired"))["device_token"]
        await w.close()
    w = FakeWatch(f"ws://{host}/ws/device", "e2e-dialog")
    await w.connect()
    await w.hello_token(token)
    pcm = load_wav_16k(SAMPLES / "en_1.wav")

    async def turn(said: str, dialog: bool) -> dict:
        mock.MOCK_TRANSCRIPTS["ro"] = said
        while not w.inbox.empty():
            w.inbox.get_nowait()
        tl = await w.ask(pcm, "ro", realtime=False, dialog=dialog)
        await asyncio.sleep(0.2)
        msgs = []
        while not w.inbox.empty():
            msgs.append(w.inbox.get_nowait())
        assert tl.status == "completed", tl.error
        return next(m for m in msgs if m["type"] == "turn_end")

    script.steps = ["Am pornit modul dialog. [[dialog:on]]"]
    assert (await turn("Pornește modul dialog", dialog=False)).get("dialog") == "on"
    script.steps = ["Este ora 13."]
    assert "dialog" not in await turn("Cât e ceasul?", dialog=True)  # an ordinary question changes nothing
    notes = [m["content"] for m in script.requests[-1].messages if m["role"] == "system"]
    assert any("Dialog mode is on" in n for n in notes)
    script.steps = ["Cu plăcere, pa! [[dialog:off]]"]
    assert (await turn("Gata, mersi", dialog=True)).get("dialog") == "off"
    await w.close()


def test_dialog_on_and_off_by_voice_over_the_protocol(server, monkeypatch) -> None:  # noqa: F811
    acc = _account()
    script = Script()
    monkeypatch.setattr(ProviderRouter, "llm", lambda self, s: (script, "script-model"))
    saved = dict(mock.MOCK_TRANSCRIPTS)
    try:
        asyncio.run(_scenario(server, script, acc))
    finally:
        mock.MOCK_TRANSCRIPTS.clear()
        mock.MOCK_TRANSCRIPTS.update(saved)
