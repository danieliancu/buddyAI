"""Web search: citation stripping, tool selection per watch, OpenAI Responses streaming + usage."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from app.device_settings import DeviceSettings
from app.providers.llm.base import LLMRequest
from app.providers.llm.citations import CitationFilter, strip_citations
from app.providers.llm.openai import OpenAILLM
from app.providers.router import ProviderRouter

REPLY = (
    "Target Zero este la 33 Robjohns Road, Chelmsford, CM1 3AG. Numărul de telefon este 0333 444 0018. "
    "([targetzerotraining.co.uk](https://targetzerotraining.co.uk/venue/?utm_source=openai))"
)
SPOKEN = "Target Zero este la 33 Robjohns Road, Chelmsford, CM1 3AG. Numărul de telefon este 0333 444 0018."


def run_filter(text: str, size: int) -> str:
    f = CitationFilter()
    out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size))
    return " ".join((out + f.flush()).split())  # the pipeline collapses spaces (clean_for_speech)


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 13, 40, 1000])
def test_citation_removed_for_any_split(size: int) -> None:
    assert run_filter(REPLY, size) == SPOKEN


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("Sunt 17 grade (înnorat) în Londra.", "Sunt 17 grade (înnorat) în Londra."),
        ("Vezi [BBC](https://bbc.co.uk) pentru detalii.", "Vezi pentru detalii."),
        ("Detalii pe https://example.com/a?b=c azi.", "Detalii pe azi."),
        ("Deschis până la ora 18 [1].", "Deschis până la ora 18 [1]."),
    ],
)
def test_citation_filter_keeps_normal_text(text: str, spoken: str) -> None:
    assert strip_citations(text) == spoken
    for size in (1, 4, 9):
        assert run_filter(text, size) == spoken


def test_web_search_tool_per_watch() -> None:
    config = {"llm": {"web_search": {"search_context_size": "low"}}}
    router = ProviderRouter(config=config)
    openai, qwen = NS(name="openai"), NS(name="qwen")
    s = DeviceSettings(timezone="America/New_York")
    tool = router.web_search(s, openai)
    assert tool == {
        "search_context_size": "low",
        "user_location": {"type": "approximate", "city": "New York", "timezone": "America/New_York"},
    }
    assert router.web_search(s.model_copy(update={"web_search": False}), openai) is None
    assert router.web_search(s, qwen) is None  # provider without web search
    assert ProviderRouter(config={"llm": {}}).web_search(s, openai) is None  # not in the profile


class FakeStream:
    def __init__(self, events):
        self.events = events

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self.events:
            yield e


def test_openai_responses_stream_strips_citations_and_counts_searches() -> None:
    pieces = [REPLY[i : i + 11] for i in range(0, len(REPLY), 11)]
    done = NS(
        output=[NS(type="web_search_call"), NS(type="message")],
        usage=NS(input_tokens=8549, output_tokens=96),
    )
    events = [NS(type="response.web_search_call.searching")]
    events += [NS(type="response.output_text.delta", delta=p) for p in pieces]
    events += [NS(type="response.completed", response=done)]
    calls = {}

    async def create(**kwargs):
        calls.update(kwargs)
        return FakeStream(events)

    llm = OpenAILLM("sk-test", "https://api.openai.com/v1")
    llm._client = NS(responses=NS(create=create))
    request = LLMRequest(
        messages=[{"role": "user", "content": "adresa?"}],
        model="gpt-6-luna",
        params={"reasoning_effort": "none"},
        web_search={"search_context_size": "low"},
    )

    async def collect():
        return [c async for c in llm.stream(request)]

    chunks = asyncio.run(collect())
    assert "".join(c.delta for c in chunks).strip() == SPOKEN
    assert chunks[-1].input_tokens == 8549 and chunks[-1].web_searches == 1
    assert calls["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert calls["reasoning"] == {"effort": "none"} and "reasoning_effort" not in calls
