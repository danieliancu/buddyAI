"""Web search safety / cost rules: NOT_FOUND and cut-off answers are not cached, sport answers are shared."""

import json
import uuid

from app.search import SHARED, WebSearch


def _searcher(answer: str, complete: bool = True):
    calls: list[str] = []

    async def fn(query, location, language):
        calls.append(query)
        return answer, 500, 30, 1, complete

    return WebSearch(fn, provider="openai", model="m"), calls


async def _ask(answer: str, complete: bool = True, query: str | None = None, account: int = 1):
    q = query or f"query {uuid.uuid4().hex}"
    args = json.dumps({"query": q, "category": "other"})
    first, calls = _searcher(answer, complete)
    out = await first.run(args, account_id=account, device_id="d", tz="Europe/London", language="en", default_location="")
    again, calls2 = _searcher(answer, complete)
    await again.run(args, account_id=account, device_id="d", tz="Europe/London", language="en", default_location="")
    return json.loads(out.result), len(calls) + len(calls2), out.usage


async def test_not_found_is_reported_and_never_cached():
    result, searches, usage = await _ask("NOT_FOUND")
    assert result["ok"] is False and "couldn't find" in result["error"]
    assert searches == 2  # asked again: searched again, nothing was cached
    assert any(u.unit == "web_search_call" for u in usage)  # the search is still billed


async def test_cut_off_answer_is_used_but_not_cached():
    result, searches, _ = await _ask("Romania are 4th in group B.", complete=False)
    assert result["ok"] is True and result["answer"] == "Romania are 4th in group B."
    assert searches == 2


async def test_complete_answer_is_cached():
    _, searches, _ = await _ask("It is 14°C.")
    assert searches == 1


async def test_sport_answers_are_shared_between_customers():
    assert "sport" in SHARED
    q = f"Romania UEFA Nations League standings {uuid.uuid4().hex}"
    args = json.dumps({"query": q, "category": "sport"})
    a, calls_a = _searcher("Romania are 4th.")
    await a.run(args, account_id=1, device_id="d1", tz="Europe/London", language="en", default_location="")
    b, calls_b = _searcher("Romania are 4th.")
    out = await b.run(args, account_id=2, device_id="d2", tz="Europe/London", language="en", default_location="")
    assert len(calls_a) == 1 and calls_b == [] and json.loads(out.result)["cached"] is True
