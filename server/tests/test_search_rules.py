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

# --- one more attempt, only after "not found" -------------------------------------------------------------


def _scripted(*answers: str):
    calls: list[str] = []
    it = iter(answers)

    async def fn(query, location, language):
        calls.append(query)
        return next(it), 500, 30, 1, True

    return WebSearch(fn, provider="openai", model="m"), calls


async def _run(ws, query: str) -> dict:
    args = json.dumps({"query": f"{query} {uuid.uuid4().hex}", "category": "other"})
    out = await ws.run(args, account_id=1, device_id="d", tz="Europe/London", language="en", default_location="")
    return json.loads(out.result)


async def test_not_found_allows_exactly_one_more_attempt():
    ws, calls = _scripted("NOT_FOUND", "High tide 19:00, 4.95 m.")
    first = await _run(ws, "tide times today")
    assert first["ok"] is False and first["retry_allowed"] is True and "ONCE more" in first["error"]
    second = await _run(ws, "Southend high tide today")
    assert second["ok"] is True and second["answer"].startswith("High tide")
    third = await _run(ws, "anything")
    assert "already searched" in third["error"] and len(calls) == 2


async def test_second_not_found_is_final():
    ws, calls = _scripted("NOT_FOUND", "NOT_FOUND")
    await _run(ws, "q1")
    second = await _run(ws, "q2")
    assert second["ok"] is False and "retry_allowed" not in second and "couldn't find" in second["error"]
    assert "already searched" in (await _run(ws, "q3"))["error"] and len(calls) == 2


async def test_found_answer_gets_no_second_search():
    ws, calls = _scripted("It is 14°C.", "unused")
    assert (await _run(ws, "weather"))["ok"] is True
    assert "already searched" in (await _run(ws, "weather again"))["error"] and len(calls) == 1


async def test_not_found_marked_items_are_not_an_answer():
    ws, _ = _scripted("- 1 pint milk: **NOT_FOUND**\n- Nutella: **NOT_FOUND** (see in store)\n- 12 eggs: **NOT_FOUND**")
    result = await _run(ws, "Lidl prices")
    assert result["ok"] is False and result["retry_allowed"] is True


async def test_partial_answer_drops_not_found_lines_and_is_not_cached():
    answer = "- 1 pint milk: 95p\n- Nutella: NOT_FOUND"
    result, searches, _ = await _ask(answer)
    assert result["ok"] is True and result["answer"] == "- 1 pint milk: 95p"
    assert searches == 2  # never cached
