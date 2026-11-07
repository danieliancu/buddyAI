"""Our search finds nothing -> "looking further…" on the watch -> OpenAI hosted search, in the same turn."""

from __future__ import annotations

import asyncio

from app.providers.base import UsageItem
from app.search import NOT_FOUND
from app.search_fallback import with_hosted_fallback


def fn(answer: str, unit: str, log: list):
    async def search(query, location, language, category="other", local_query=""):
        log.append((unit, query, category))
        return answer, 10, 5, 1, True, [UsageItem("search", unit, "m", "x", 1)]

    return search


def run(ours_answer: str, hosted_answer: str = "17th in the 200m IM", category: str = "sport",
        skip: frozenset[str] = frozenset(), hosted_error: bool = False):
    log: list = []

    async def said():
        log.append(("said",))

    hosted = fn(hosted_answer, "hosted", log)
    if hosted_error:
        async def hosted(*a, **kw):  # noqa: F811
            log.append(("hosted", "boom"))
            raise TimeoutError

    search = with_hosted_fallback(fn(ours_answer, "ours", log), hosted, said, skip)
    out = asyncio.run(search("Patrick Iancu swimming Basildon results", "Chelmsford", "ro", category=category))
    return out, [e[0] for e in log]


def test_our_answer_is_used_without_the_fallback() -> None:
    out, steps = run("Locul 3 la 100 m liber")
    assert out[0] == "Locul 3 la 100 m liber" and steps == ["ours"]


def test_not_found_says_the_phrase_then_asks_the_hosted_search() -> None:
    out, steps = run(NOT_FOUND)
    assert steps == ["ours", "said", "hosted"]  # the watch's hint comes before the slow call
    assert out[0] == "17th in the 200m IM"
    assert out[1:4] == (20, 10, 2)  # tokens and searches of both
    assert [u.provider for u in out[5]] == ["ours", "hosted"]  # both are billed


def test_an_empty_or_partial_not_found_is_not_an_answer() -> None:
    assert run("")[1] == ["ours", "said", "hosted"]
    assert run("NOT_FOUND - no result for that gala")[1] == ["ours", "said", "hosted"]


def test_hosted_finds_nothing_either() -> None:
    out, _ = run(NOT_FOUND, hosted_answer=NOT_FOUND)
    assert out[0] == NOT_FOUND  # the model then says it couldn't find it


def test_categories_already_on_the_hosted_search_are_not_asked_twice() -> None:
    out, steps = run(NOT_FOUND, category="transport", skip=frozenset({"transport"}))
    assert steps == ["ours"] and out[0] == NOT_FOUND


def test_hosted_failure_keeps_our_not_found() -> None:
    out, steps = run(NOT_FOUND, hosted_error=True)
    assert steps == ["ours", "said", "hosted"] and out[0] == NOT_FOUND


# --- "checking…" on the watch when a search takes a while: written, never spoken ------------------------------


def _search_turn(delay_s: float) -> tuple[str, list[str]]:
    from tests.test_cost_optimisation import SearchingLLM, _clear_cache, _pipeline, _turn
    from tests.test_items import FakeIO, _account

    class Slow(SearchingLLM):
        async def web_search(self, query, location, language, **kw):
            await asyncio.sleep(delay_s)
            return await super().web_search(query, location, language, **kw)

    _clear_cache()
    io = FakeIO()
    asyncio.run(_pipeline(Slow())._reply(_turn(_account()), io))
    text = "".join(f["delta"] for t, f in io.sent if t == "llm_text")
    return text, [f["kind"] for t, f in io.sent if t == "wait_hint"]


def test_a_slow_search_shows_checking_on_the_watch_and_says_nothing() -> None:
    from app.search_fallback import CHECKING_DELAY_S

    text, hints = _search_turn(CHECKING_DELAY_S + 0.6)
    assert hints == ["checking"]
    assert "14 degrees" in text
    assert "check" not in text.casefold()  # nothing of it in the reply: not spoken, not in the history


def test_a_quick_search_is_answered_without_the_hint() -> None:
    text, hints = _search_turn(0.0)
    assert hints == [] and "14 degrees" in text