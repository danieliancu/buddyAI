"""Web search on demand, with a freshness-aware cache.

The conversation model does not carry the provider's hosted search tool on every request any more (it
added thousands of hidden input tokens to every turn and let the model search for things it knows).
It gets a small `web_search` function instead and decides itself when current information is needed.
A call runs one compact search request (the provider's hosted web search, short factual answer) and the
answer is cached:

- key: scope | category | location | language | normalised query (sha256)
- lifetime per category (config llm.web_search.cache_ttl_s): weather ~15 min, transport ~2 min,
  sport ~10 min, news ~30 min, businesses ~24 h, anything else ~1 h
- scope: weather / transport / sport / business / news answers are public facts and shared between customers;
  anything else stays with the account (or the watch) that asked
- the tool result always says when it was fetched, and the model is told to say "as of HH:MM" for
  anything older than a few minutes, so cached data is never presented as live

One search per turn, plus one more attempt only when the first found nothing: the model may retry once
with a broader or different wording (the same query often succeeds on a second try, and over-specific
queries - a date the user did not say - fail). After an answer, or after two attempts, a further call gets
"already searched". Inside each request the provider may run at most MAX_PROVIDER_SEARCHES searches. The
search model answers NOT_FOUND when the results do not match the query (another competition, season or place)
instead of answering something else; such answers, partial answers and answers cut off by the length limit
are never cached. "Not found" itself is never cached either: a repeat may well find it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import col, delete, select

from app.db.models import SearchCache
from app.db.session import session_scope
from app.providers.base import UsageItem

log = logging.getLogger(__name__)

CATEGORIES = ("weather", "transport", "sport", "business", "news", "other")
SHARED = {"weather", "transport", "sport", "business", "news"}
DEFAULT_TTL_S = {"weather": 900, "transport": 120, "sport": 600, "business": 86400, "news": 1800, "other": 3600}
MAX_PROVIDER_SEARCHES = 2  # searches the provider may run for one query (each one is billed)
MAX_ATTEMPTS = 2  # search calls per turn: the second one only after the first found nothing
NOT_FOUND = "NOT_FOUND"
STALE_AFTER_MIN = 5  # older answers must be introduced as "as of HH:MM"

SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": "Look up CURRENT or LOCAL facts on the web: weather and forecasts, live traffic and "
    "transport, opening hours / phone / address of a business, news, today's prices, sports results and "
    "standings. Never for general knowledge, definitions, maths, dates you can work out, translations, "
    "advice, small talk, notes or reminders: answer those yourself. Never when the answer is already in this "
    "conversation (an earlier reply or search): reuse it. At most one call per question.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Short canonical English search phrase without personal details that is "
                "complete on its own and names the specific fact wanted: add what the conversation implies - "
                "the place, organisation, event, team, period or date - and never vague words like 'current' "
                "or 'the standings' alone. Never add a month, year or date the user did not imply: for the "
                "last / next one write 'latest' / 'next'. E.g. 'weather Chelmsford today', 'Tesco Chelmsford "
                "opening hours', 'Romania national handball team latest match result', "
                "'Romania position UEFA Nations League 2026-27 group' (after a question about Romania's Nations "
                "League match)",
            },
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "location": {"type": "string", "description": "Town or area the question is about (default: the user's)"},
        },
        "required": ["query", "category"],
    },
}

SEARCH_RULE = (
    "Use web_search only when the answer depends on current or local information you cannot know and it is "
    "not already in this conversation; otherwise answer directly. Say only the answer, never sources or links. "
    f"If a search result's age_minutes is above {STALE_AFTER_MIN}, say the time it is from (e.g. 'as of 14:05'). "
    "If the result gives a basis (e.g. 'after three matches', 'as of 2 October'), keep it in a few words. "
    "If a search finds nothing reliable you may search once more with a broader or different wording; if that "
    "finds nothing either, say briefly that you couldn't find it - never guess."
)

SEARCH_INSTRUCTIONS = (
    "Search the web and answer ONLY the specific fact the query asks for, in at most two short sentences with "
    "the concrete values (numbers, times, names, addresses). Leave out everything around it (other rows of a "
    "table, other items of a list, background) - details you were not asked for are where mistakes creep in. "
    "Prefer official and reliable sources (public bodies, governing bodies, operators, the business's own site). "
    "When the fact is partial, worked out from several results or dated, add its basis in a few words (e.g. "
    "'after three matches', 'as of 2 October', 'according to the operator'). For the latest / last / next "
    "something (match, result, event, departure), give the most recent or next one you find, whatever its "
    "date, and say the date. If the query fits more than one thing (e.g. men's and women's team), answer for "
    "the main one (the senior team) and say which. Reply only "
    f"{NOT_FOUND} if the results do not contain that fact or are about something else (another place, "
    "event or thing, or another period than one the query names). Never ask a question back and never say you "
    f"cannot verify something: reply only {NOT_FOUND} instead. No links, no citations, no advice."
)


RETRY_HINT = (
    "nothing reliable found. You may search ONCE more with a broader or different wording: drop dates, months "
    "or details the user did not say, or use other words for the same fact. Do not retry for private personal "
    "details or for prices a shop does not publish - then tell the user you couldn't find it."
)


def clean_answer(answer: str) -> tuple[str, bool]:
    """(usable answer, partial). The search model sometimes marks only some items NOT_FOUND (a list of
    prices): those lines are dropped; nothing left = not found. partial answers are used but never cached."""
    text = answer.strip()
    if NOT_FOUND not in text.upper():
        return text, False
    kept = [ln for ln in text.splitlines() if NOT_FOUND not in ln.upper()]
    rest = "\n".join(kept).strip()
    if sum(ch.isalnum() for ch in rest) < 8:
        return "", False
    return rest, True


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).casefold()
    t = re.sub(r"[^\w\s°%£$€:.-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class SearchOutcome:
    result: str  # JSON text for the model
    usage: list[UsageItem]
    cache_hit: bool


def cache_key(scope: str, category: str, location: str, language: str, query: str) -> str:
    raw = "|".join((scope, category, normalise(location), language.lower(), normalise(query)))
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class WebSearch:
    """Per-turn search executor (one search per turn, one more attempt after "not found"). `search_fn` runs
    the real search: async (query, location, language) -> (answer, input_tokens, output_tokens, searches)."""

    def __init__(self, search_fn, ttl_s: dict[str, int] | None = None, provider: str = "", model: str = "") -> None:
        self.search_fn = search_fn
        self.ttl_s = {**DEFAULT_TTL_S, **(ttl_s or {})}
        self.provider = provider
        self.model = model
        self.attempts = 0
        self.found = False  # an answer was given: no further search in this turn

    async def run(self, arguments: str, *, account_id: int | None, device_id: str, tz: str, language: str,
                  default_location: str) -> SearchOutcome:
        if self.found or self.attempts >= MAX_ATTEMPTS:
            return SearchOutcome(json.dumps({"ok": False, "error": "already searched for this question; answer with what you have"}), [], False)
        self.attempts += 1
        try:
            a = json.loads(arguments or "{}")
        except ValueError:
            a = {}
        query = str(a.get("query") or "").strip()[:300]
        if not query:
            return SearchOutcome(json.dumps({"ok": False, "error": "empty query"}), [], False)
        category = a.get("category") if a.get("category") in CATEGORIES else "other"
        location = str(a.get("location") or default_location or "").strip()[:80]
        scope = "shared" if category in SHARED else (f"account:{account_id}" if account_id is not None else f"device:{device_id}")
        key = cache_key(scope, category, location, language, query)
        now = datetime.now(timezone.utc)

        with session_scope() as db:
            row = db.exec(select(SearchCache).where(SearchCache.key == key)).first()
            if row is not None and _aware(row.expires_at) > now:
                row.hits += 1
                db.add(row)
                db.commit()
                self.found = True
                return SearchOutcome(
                    self._result(row.answer, _aware(row.fetched_at), now, tz, cached=True),
                    [UsageItem("search", "buddyai", "search_cache", "cache_hit", 1)],
                    True,
                )

        answer, tokens_in, tokens_out, searches, *more = await self.search_fn(query, location, language)
        complete = bool(more[0]) if more else True  # False: cut off by the length limit
        answer, partial = clean_answer(answer)
        if partial:
            complete = False  # what was found is used, never cached
        if not answer:
            complete = False
            log.info("web_search(%s) -> not found (attempt %s)", query[:120], self.attempts)
        usage = [
            UsageItem("llm", self.provider, self.model, "input_token", tokens_in),
            UsageItem("llm", self.provider, self.model, "output_token", tokens_out),
            UsageItem("llm", self.provider, self.model, "web_search_call", max(1, searches) if answer else searches),
        ]
        if answer and complete:
            ttl = timedelta(seconds=int(self.ttl_s.get(category, DEFAULT_TTL_S["other"])))
            with session_scope() as db:
                db.exec(delete(SearchCache).where(col(SearchCache.expires_at) < now - timedelta(days=1)))  # type: ignore[call-overload]
                row = db.exec(select(SearchCache).where(SearchCache.key == key)).first() or SearchCache(key=key, scope=scope, category=category, expires_at=now)
                row.location, row.language, row.query = location, language[:8], query
                row.answer, row.fetched_at, row.expires_at = answer, now, now + ttl
                db.add(row)
                db.commit()
        if answer:
            self.found = True
        retry = not answer and self.attempts < MAX_ATTEMPTS
        return SearchOutcome(self._result(answer, now, now, tz, cached=False, retry=retry), usage, False)

    @staticmethod
    def _result(answer: str, fetched: datetime, now: datetime, tz: str, cached: bool, retry: bool = False) -> str:
        if not answer and retry:
            return json.dumps({"ok": False, "retry_allowed": True, "error": RETRY_HINT})
        if not answer:
            return json.dumps({"ok": False, "error": "nothing reliable found: tell the user you couldn't find it"})
        local = fetched.astimezone(ZoneInfo(tz))
        return json.dumps(
            {
                "ok": True,
                "answer": answer,
                "fetched_at": f"{local:%H:%M}",
                "age_minutes": int((now - fetched).total_seconds() // 60),
                "cached": cached,
            },
            ensure_ascii=False,
        )
