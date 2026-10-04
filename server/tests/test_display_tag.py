"""Short on-screen answers: a leading [[value]] is stripped from the reply and sent as llm_display."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from app.device_settings import DeviceSettings
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.display_tag import DisplayTagFilter, asks_for_value, echoes_question, guess_value, to_display
from app.pipeline.turn import TurnContext
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest, ToolCall
from tests.test_items import FakeIO, FakeRouter

SPOKEN = "În Chelmsford sunt 21 de grade și bate vântul."


def run(text: str, size: int) -> tuple[str, str | None]:
    f = DisplayTagFilter()
    out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size)) + f.flush()
    return out, f.value


@pytest.mark.parametrize("size", [1, 2, 3, 5, 100])
def test_tag_is_stripped_for_any_split(size: int) -> None:
    assert run("[[21°C]] " + SPOKEN, size) == (SPOKEN, "21°C")


@pytest.mark.parametrize(
    "text, value",
    [
        (SPOKEN, None),  # no tag
        ("[Note] " + SPOKEN, None),  # single bracket: normal text
        ("[[this value is far too long to show on the watch screen]] " + SPOKEN, None),  # tag dropped, text kept
    ],
)
def test_text_without_a_usable_tag(text: str, value: str | None) -> None:
    out, got = run(text, 4)
    assert got == value
    assert out.endswith(SPOKEN)


@pytest.mark.parametrize(
    "reply, value",
    [
        ("Four, Daniel.", "4"),
        ("Patru, Daniel.", "4"),
        ("Twenty-one.", "21"),
        ("Douăzeci și unu de grade.", "21"),
        ("It is 21 °C and windy.", "21°C"),
        ("The meeting is at 14:30.", "14:30"),
        ("Costă £3.50.", "£3.50"),
        ("Sure, I can help with that.", None),
        ("I have 2 cats and 3 dogs.", None),  # more than one number: no guess
        ("Un moment, te rog.", None),  # "un" is an article
        ("Four. " + "x" * 200, None),  # long replies are not scanned
    ],
)
def test_guess_value(reply: str, value: str | None) -> None:
    assert guess_value(reply) == value


def test_pipeline_guesses_value_when_tag_is_missing() -> None:
    class Plain(LLMProvider):
        name = "plain"

        async def stream(self, request: LLMRequest):
            yield LLMChunk(delta="Four, Daniel.")
            yield LLMChunk(input_tokens=1, output_tokens=1)

    pipeline = ConversationPipeline(
        FakeRouter(Plain()), ChunkerConfig(), messages_builder=lambda t, x: [{"role": "user", "content": x}]
    )
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000)
    turn.user_text = "two plus two"
    io = FakeIO()
    asyncio.run(pipeline._reply(turn, io))
    assert ("llm_display", {"text": "4"}) in io.sent


@pytest.mark.parametrize(
    "question, expected",
    [
        ("two plus two", True),
        ("Câte grade sunt afară?", True),
        ("Cât costă o pâine?", True),
        ("What time does the bus leave?", True),
        ("Tell me a joke", False),
        ("Arată-mi notițele", False),
        ("What is the capital of France?", False),
        ("It is the highway M25 busy now", False),  # a road name, not a question for a number
        ("What's 15 x 3?", True),
    ],
)
def test_asks_for_value(question: str, expected: bool) -> None:
    assert asks_for_value(question) is expected


def test_names_and_echoes_are_not_values() -> None:
    assert guess_value("I can't verify live M25 traffic right now.") is None  # the actual reply
    assert guess_value("Take the X30 from Chelmsford.") is None
    assert echoes_question("M25", "Is the M25 busy?") and echoes_question("25", "Is the M25 busy?")
    assert not echoes_question("4", "two plus two")


def test_m25_question_shows_nothing() -> None:
    class Traffic(LLMProvider):
        name = "traffic"

        async def stream(self, request: LLMRequest):
            yield LLMChunk(delta="[[M25]] I can't verify live M25 traffic right now.")
            yield LLMChunk(input_tokens=1, output_tokens=1)

    assert not any(t == "llm_display" for t, _ in _reply_with(Traffic(), "Is the highway M25 busy now").sent)


def _reply_with(llm: LLMProvider, question: str, tools=None) -> FakeIO:
    pipeline = ConversationPipeline(
        FakeRouter(llm), ChunkerConfig(), messages_builder=lambda t, x: [{"role": "user", "content": x}], tools=tools
    )
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000, account_id=1 if tools else None)
    turn.user_text = question
    io = FakeIO()
    asyncio.run(pipeline._reply(turn, io))
    return io


def test_no_guess_for_a_joke() -> None:
    class Joke(LLMProvider):
        name = "joke"

        async def stream(self, request: LLMRequest):
            yield LLMChunk(delta="Why did one scarecrow win? He was outstanding.")
            yield LLMChunk(input_tokens=1, output_tokens=1)

    assert not any(t == "llm_display" for t, _ in _reply_with(Joke(), "Tell me a joke").sent)


def test_no_display_value_after_notes_or_reminders() -> None:
    class ToolThenTag(LLMProvider):
        name = "tool-then-tag"

        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request: LLMRequest):
            self.calls += 1
            if self.calls == 1:
                yield LLMChunk(tool_calls=[ToolCall("c1", "item_list", '{"kind": "note"}')])
            else:
                yield LLMChunk(delta="[[1]] You have one note.")
            yield LLMChunk(input_tokens=1, output_tokens=1)

    class NoTools:
        def definitions(self, account_id):
            return [{"name": "item_list", "parameters": {}}]

        def rules(self, account_id):
            return []

        def execute(self, account_id, tz, name, arguments, device_id="", call=None):
            return NS(result='{"ok": true}', changed=False, open=None, settings_changed=False, awaits_answer=False,
                      open_uid=None)

    io = _reply_with(ToolThenTag(), "how many notes do I have?", tools=NoTools())
    assert not any(t == "llm_display" for t, _ in io.sent)


@pytest.mark.parametrize(
    "value, shown",
    [
        ("E = mc^2", "E = mc²"),
        ("H_2O", "H₂O"),
        ("x^{10}", "x¹⁰"),
        (r"\sqrt{16} = 4", "√(16) = 4"),
        (r"a \leq b", "a ≤ b"),
        (r"\frac{1}{2}", "1/2"),
        (r"\int_0^1 x\,dx", "∫₀¹ x dx"),
        ("a² + b² = c²", "a² + b² = c²"),  # already Unicode: unchanged
        ("21°C", "21°C"),
    ],
)
def test_formulas_become_unicode(value: str, shown: str) -> None:
    assert to_display(value) == shown


def test_formula_tag_in_the_stream() -> None:
    assert run("[[E = mc^2]] Energy equals mass times c squared.", 3) == (
        "Energy equals mass times c squared.",
        "E = mc²",
    )


def test_unterminated_tag_is_passed_through() -> None:
    assert run("[[21°C", 2) == ("[[21°C", None)


class _TaggedLLM(LLMProvider):
    name = "tagged"

    async def stream(self, request: LLMRequest):
        for piece in ["[[", "21°", "C]]", " In Chelmsford", " it's 21 degrees."]:
            yield LLMChunk(delta=piece)
        yield LLMChunk(input_tokens=10, output_tokens=5)


def test_pipeline_sends_display_value_and_speaks_the_rest() -> None:
    pipeline = ConversationPipeline(
        FakeRouter(_TaggedLLM()), ChunkerConfig(), messages_builder=lambda t, x: [{"role": "user", "content": x}]
    )
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000)
    turn.user_text = "temperature?"
    io = FakeIO()
    asyncio.run(pipeline._reply(turn, io))
    assert ("llm_display", {"text": "21°C"}) in io.sent
    assert turn.assistant_text == "In Chelmsford it's 21 degrees."
    captions = "".join(f["delta"] for t, f in io.sent if t == "llm_text")
    assert "[[" not in captions


@pytest.mark.parametrize("size", [1, 2, 3, 7, 100])
def test_tag_inside_the_reply_loses_its_brackets(size: int) -> None:
    text = "În Southend-on-Sea, următoarea maree înaltă este la [[17:39]] azi."
    assert run(text, size) == ("În Southend-on-Sea, următoarea maree înaltă este la 17:39 azi.", "17:39")


@pytest.mark.parametrize("size", [1, 4, 100])
def test_leading_tag_wins_and_later_tags_lose_brackets(size: int) -> None:
    assert run("[[17:39]] High tide at [[17:39]], low at [[23:50]].", size) == (
        "High tide at 17:39, low at 23:50.",
        "17:39",
    )


@pytest.mark.parametrize("size", [1, 100])
def test_single_brackets_inside_the_reply_stay(size: int) -> None:
    assert run("Say [yes] or [no] [", size) == ("Say [yes] or [no] [", None)
