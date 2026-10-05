"""Cost optimisation: on-demand web search with a freshness-aware cache, the end-of-speech silence held
back from the STT, TTS billed on what was really sent, cached prompt tokens priced apart, a stable
system prompt (prompt caching) and trimmed history."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from sqlmodel import select

from app.db.models import SearchCache
from app.db.session import session_scope
from app.device_settings import DeviceSettings
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import SILENCE_SEND_MS, ConversationPipeline, build_messages_from_db
from app.pipeline.turn import TurnContext
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest, ToolCall
from app.providers.tts.base import TTSRequest
from app.providers.tts.http_stream import HTTPStreamingTTS
from app.search import SEARCH_TOOL, WebSearch, cache_key
from tests.test_items import FakeIO, FakeRouter, _account
from tests.test_listen_gate import _listen, ms
from tests.test_vad import silence, tone

# --- web search routing ---------------------------------------------------------------------------------


class SearchingLLM(LLMProvider):
    """Round 1 asks for web_search (twice, to test the one-search limit); round 2 answers."""

    name = "openai"

    def __init__(self, category: str = "weather") -> None:
        self.requests: list[LLMRequest] = []
        self.searches: list[tuple[str, str]] = []
        self.category = category

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            args = json.dumps({"query": "weather Chelmsford today", "category": self.category})
            yield LLMChunk(input_tokens=900, cached_input_tokens=600, output_tokens=20)
            yield LLMChunk(tool_calls=[ToolCall("s1", "web_search", args), ToolCall("s2", "web_search", args)])
        else:
            yield LLMChunk(delta="[[14°C]] It's 14 degrees and cloudy.")
            yield LLMChunk(input_tokens=1000, cached_input_tokens=0, output_tokens=15)

    async def web_search(self, query, location, language, **kw):
        self.searches.append((query, location))
        return "Chelmsford: 14°C, cloudy, light wind.", 800, 40, 1


class SearchRouter(FakeRouter):
    def web_search(self, s, llm):
        return {"search_context_size": "low", "user_location": {"type": "approximate", "city": "London"},
                "cache_ttl_s": {"weather": 900}}

    @staticmethod
    def hosted_search_tool(ws):
        return {k: v for k, v in ws.items() if k in ("search_context_size", "user_location")}


def _turn(acc: int | None, text: str = "what's the weather") -> TurnContext:
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(timezone="Europe/London"), 16000, account_id=acc)
    turn.user_text = text
    return turn


def _pipeline(llm) -> ConversationPipeline:
    from app.items import AssistantTools

    return ConversationPipeline(
        SearchRouter(llm), ChunkerConfig(),
        messages_builder=lambda t, x: [{"role": "system", "content": "sys"}, {"role": "user", "content": x}],
        tools=AssistantTools(),
    )


def _clear_cache() -> None:
    with session_scope() as db:
        for row in db.exec(select(SearchCache)).all():
            db.delete(row)
        db.commit()


def test_search_is_a_tool_not_a_hidden_attachment_and_is_cached() -> None:
    _clear_cache()
    acc = _account()
    llm = SearchingLLM()
    turn = _turn(acc)
    io = FakeIO()
    asyncio.run(_pipeline(llm)._reply(turn, io))

    first = llm.requests[0]
    assert first.web_search is None  # no hosted tool on the conversation request
    assert SEARCH_TOOL["name"] in [t["name"] for t in first.tools]
    assert "web_search only when" in first.messages[0]["content"]
    assert llm.searches == [("weather Chelmsford today", "London")]  # the second call in the turn is refused
    results = [json.loads(m["content"]) for m in llm.requests[1].messages if m.get("role") == "tool"]
    assert results[0]["ok"] and results[0]["cached"] is False and results[0]["age_minutes"] == 0
    assert "already searched" in results[1]["error"]
    assert ("llm_display", {"text": "14°C"}) in io.sent  # a search answer may still show its value
    units: dict = {}
    for u in turn.usage:
        units[(u.model, u.unit)] = units.get((u.model, u.unit), 0) + u.quantity
    assert units[("model-x", "web_search_call")] == 1
    assert units[("model-x", "cached_input_token")] == 600 and units[("model-x", "input_token")] == 900 + 800 + 1000 - 600

    # Another customer asks the same public question: answered from the cache, no search, no tokens.
    llm2 = SearchingLLM()
    turn2 = _turn(_account())
    asyncio.run(_pipeline(llm2)._reply(turn2, FakeIO()))
    assert llm2.searches == []
    hit = [json.loads(m["content"]) for m in llm2.requests[1].messages if m.get("role") == "tool"][0]
    assert hit["cached"] is True
    assert any(u.unit == "cache_hit" for u in turn2.usage)
    assert not any(u.unit == "web_search_call" and u.quantity for u in turn2.usage)


def test_private_categories_are_not_shared_between_accounts() -> None:
    _clear_cache()
    a, b = _account(), _account()
    first, second = SearchingLLM(category="other"), SearchingLLM(category="other")
    asyncio.run(_pipeline(first)._reply(_turn(a), FakeIO()))
    asyncio.run(_pipeline(second)._reply(_turn(b), FakeIO()))
    assert len(first.searches) == len(second.searches) == 1  # "other" answers stay with the account that asked
    with session_scope() as db:
        scopes = sorted(r.scope for r in db.exec(select(SearchCache)).all())
    assert scopes == sorted([f"account:{a}", f"account:{b}"])


async def test_expired_answers_are_searched_again_and_old_ones_say_their_time() -> None:
    _clear_cache()
    calls = []

    async def fake(query, location, language):
        calls.append(query)
        return "Opens 8:00-22:00.", 100, 10, 1

    ws = WebSearch(fake, {"business": 60}, provider="openai", model="m")
    args = json.dumps({"query": "Tesco Chelmsford opening hours", "category": "business"})
    await ws.run(args, account_id=1, device_id="d", tz="Europe/London", language="en", default_location="Chelmsford")
    key = cache_key("shared", "business", "Chelmsford", "en", "Tesco Chelmsford opening hours")
    with session_scope() as db:
        row = db.exec(select(SearchCache).where(SearchCache.key == key)).one()
        row.fetched_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        db.add(row)
        db.commit()
    hit = json.loads((await WebSearch(fake, {"business": 60}).run(
        args, account_id=2, device_id="d", tz="Europe/London", language="en", default_location="Chelmsford")).result)
    assert hit["cached"] and hit["age_minutes"] == 30  # the model is told how old it is
    with session_scope() as db:
        row = db.exec(select(SearchCache).where(SearchCache.key == key)).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.add(row)
        db.commit()
    await WebSearch(fake, {"business": 60}).run(args, account_id=3, device_id="d", tz="Europe/London",
                                                 language="en", default_location="Chelmsford")
    assert len(calls) == 2  # expired: searched again


def test_no_search_tool_without_web_search() -> None:
    from tests.test_items import _TextLLM

    llm = _TextLLM()
    pipeline = ConversationPipeline(
        FakeRouter(llm), ChunkerConfig(),
        messages_builder=lambda t, x: [{"role": "system", "content": "sys"}, {"role": "user", "content": x}],
    )
    asyncio.run(pipeline._reply(_turn(None, "hi"), FakeIO()))
    assert llm.seen_tools is None  # FakeRouter has no web search, and no item tools were given


# --- prompt: stable prefix, trimmed history --------------------------------------------------------------


def test_system_prompt_is_stable_and_history_trimmed(monkeypatch) -> None:
    from app.pipeline import conversation as conv

    long_reply = "word " * 200
    monkeypatch.setattr(conv, "ConversationRepo", lambda db: NS(history=lambda *a: [NS(user_text="q", assistant_text=long_reply, search_note="")]))
    turn = _turn(None, "and now?")
    turn.conversation_id = 1
    a = build_messages_from_db(turn, "and now?")
    b = build_messages_from_db(turn, "and now?")
    assert a[0] == b[0] and "Now:" not in a[0]["content"]  # no clock in the cached prefix
    assert a[-2]["role"] == "system" and a[-2]["content"].startswith("Now:")
    assert len(a[2]["content"]) <= 302 and a[2]["content"].endswith("…")


def test_history_keeps_what_the_search_found(monkeypatch) -> None:
    # The spoken reply dropped "Nations League"; the next question must still know it.
    from app.pipeline import conversation as conv

    past = NS(
        user_text="Last Romania match score?",
        assistant_text="Romania lost 6-0 to Poland on 2 October.",
        search_note="Romania latest match result -> Poland beat Romania 6-0 on 2 October 2026 in the UEFA Nations League.",
    )
    monkeypatch.setattr(conv, "ConversationRepo", lambda db: NS(history=lambda *a: [past]))
    turn = _turn(None, "Where are they in the standings?")
    turn.conversation_id = 1
    msgs = build_messages_from_db(turn, "Where are they in the standings?")
    assert msgs[3] == {"role": "system", "content": "Web search behind that reply: " + past.search_note}


class RetryingLLM(SearchingLLM):
    """Round 1 searches (not found), round 2 retries with a broader query, round 3 answers."""

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        n = len(self.requests)
        if n <= 2:
            q = "Bucharest North to Titu trains Monday 5 October 2026" if n == 1 else "Bucharest Nord Titu train timetable"
            yield LLMChunk(input_tokens=500, cached_input_tokens=0, output_tokens=10)
            yield LLMChunk(tool_calls=[ToolCall(f"s{n}", "web_search", json.dumps({"query": q, "category": "transport"}))])
        else:
            yield LLMChunk(delta="The first train leaves at 04:48.")
            yield LLMChunk(input_tokens=600, cached_input_tokens=0, output_tokens=10)

    async def web_search(self, query, location, language, **kw):
        self.searches.append((query, location))
        return ("NOT_FOUND" if len(self.searches) == 1 else "04:48 -> 06:17, 05:51 -> 07:07."), 700, 20, 1


def test_not_found_search_is_retried_once_with_a_broader_query() -> None:
    _clear_cache()
    llm = RetryingLLM()
    turn = _turn(_account(), "trains to Titu tomorrow")
    asyncio.run(_pipeline(llm)._reply(turn, FakeIO()))
    assert [q for q, _ in llm.searches] == ["Bucharest North to Titu trains Monday 5 October 2026",
                                           "Bucharest Nord Titu train timetable"]
    first = [json.loads(m["content"]) for m in llm.requests[1].messages if m.get("role") == "tool"][0]
    assert first["retry_allowed"] is True
    assert sum(u.quantity for u in turn.usage if u.unit == "web_search_call") == 2  # both attempts billed
    assert turn.search_note.endswith("04:48 -> 06:17, 05:51 -> 07:07.")  # the final result is remembered
    assert "04:48" in turn.assistant_text


def test_search_note_records_query_and_answer() -> None:
    from app.pipeline.conversation import search_note

    args = json.dumps({"query": "Romania UEFA Nations League standings", "category": "sport"})
    assert search_note(args, json.dumps({"ok": True, "answer": "Romania are 4th."})) == (
        "Romania UEFA Nations League standings -> Romania are 4th."
    )
    assert search_note(args, json.dumps({"ok": False, "error": "x"})).endswith("-> nothing reliable found")


# --- STT: end-of-speech silence is not sent -----------------------------------------------------------------


def test_trailing_silence_is_held_back() -> None:
    stt, text, _ = _listen(silence(1000) + tone(1000) + silence(1500))
    assert text == "hello"
    # speech + pre-roll + at most SILENCE_SEND_MS (+ one uplink chunk) of the pause; before, ~700 ms of
    # silence was streamed after every question just to confirm the end.
    assert ms(stt.sent) <= 1000 + 500 + SILENCE_SEND_MS + 100


def test_a_pause_inside_a_sentence_is_still_sent() -> None:
    stt, text, _ = _listen(tone(800) + silence(500) + tone(800) + silence(1500))
    assert text == "hello"
    assert ms(stt.sent) >= 800 + 500 + 800  # the held pause was sent once speech resumed


# --- TTS: billed on requests actually sent ----------------------------------------------------------------


async def test_tts_reports_every_request_including_twins() -> None:
    class SlowFirst(HTTPStreamingTTS):
        attempts = 0

        def check_configured(self):
            pass

        def build_request(self, text, request):
            return {}

        async def _attempt(self, text, request, out):
            if request.on_request:
                request.on_request(len(text))
            SlowFirst.attempts += 1
            if SlowFirst.attempts == 1:
                await asyncio.sleep(1)  # stalls: a twin is sent
            await out.put(b"\x00\x00" * 10)
            from app.providers.tts.http_stream import _DONE

            await out.put(_DONE)

    sent = []
    tts = SlowFirst(hedge_after_s=0.05)

    async def frags():
        yield "Hello there."

    req = TTSRequest(voice="v", language="en", on_request=sent.append)
    async for _ in tts.stream(frags(), req):
        pass
    assert sent == [12, 12]  # the stalled request and its twin are both billed by the provider


def test_chat_title_is_the_persona_name_except_for_the_default() -> None:
    from app.db.models import Persona
    from app.db.repositories import PersonaRepo
    from app.db.session import session_scope

    with session_scope() as db:
        repo = PersonaRepo(db)
        repo.seed()
        default = repo.get(None)
        coach = Persona(name="Coach title test", system_prompt="x")
        db.add(coach)
        db.commit()
        db.refresh(coach)
        assert repo.chat_title(None) == "" and repo.chat_title(default.id) == ""
        assert repo.chat_title(coach.id) == "Coach title test"
        db.delete(coach)
        db.commit()
