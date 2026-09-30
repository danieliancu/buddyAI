"""Web search on demand, with a freshness-aware cache.

The conversation model does not carry the provider's hosted search tool on every request any more (it
added thousands of hidden input tokens to every turn and let the model search for things it knows).
It gets a small `web_search` function instead and decides itself when current information is needed.
A call runs one compact search request (the provider's hosted web search, short factual answer) and the
answer is cached:

- key: scope | category | location | language | normalised query (sha256)
- lifetime per category (config llm.web_search.cache_ttl_s): weather ~15 min, transport ~2 min,
  news ~30 min, businesses ~24 h, anything else ~1 h
- scope: weather / transport / business / news answers are public facts and shared between customers;
  anything else stays with the account (or the watch) that asked
- the tool result always says when it was fetched, and the model is told to say "as of HH:MM" for
  anything older than a few minutes, so cached data is never presented as live

One search per turn: a second call gets "already searched".
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

CATEGORIES = ("weather", "transport", "business", "news", "other")
SHARED = {"weather", "transport", "business", "news"}
DEFAULT_TTL_S = {"weather": 900, "transport": 120, "business": 86400, "news": 1800, "other": 3600}
STALE_AFTER_MIN = 5  # older answers must be introduced as "as of HH:MM"

SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": "Look up CURRENT or LOCAL facts on the web: weather and forecasts, live traffic and "
    "transport, opening hours / phone / address of a business, news, today's prices or results. Never for "
    "general knowledge, definitions, maths, dates you can work out, translations, advice, small talk, notes "
    "or reminders: answer those yourself. At most one call per question.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Short canonical English search phrase without personal details, e.g. "
                "'weather Chelmsford today', 'Tesco Chelmsford opening hours'",
            },
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "location": {"type": "string", "description": "Town or area the question is about (default: the user's)"},
        },
        "required": ["query", "category"],
    },
}

SEARCH_RULE = (
    "Use web_search only when the answer depends on current or local information you cannot know; otherwise "
    "answer directly. Say only the answer, never sources or links. If a search result's age_minutes is above "
    f"{STALE_AFTER_MIN}, say the time it is from (e.g. 'as of 14:05')."
)

SEARCH_INSTRUCTIONS = (
    "Search the web and answer the query in at most three short factual sentences with the concrete values "
    "(numbers, times, names, addresses). No links, no citations, no advice."
)


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
    """Per-turn search executor (one search per turn). `search_fn` runs the real search:
    async (query, location, language) -> (answer, input_tokens, output_tokens, searches)."""

    def __init__(self, search_fn, ttl_s: dict[str, int] | None = None, provider: str = "", model: str = "") -> None:
        self.search_fn = search_fn
        self.ttl_s = {**DEFAULT_TTL_S, **(ttl_s or {})}
        self.provider = provider
        self.model = model
        self.used = False

    async def run(self, arguments: str, *, account_id: int | None, device_id: str, tz: str, language: str,
                  default_location: str) -> SearchOutcome:
        if self.used:
            return SearchOutcome(json.dumps({"ok": False, "error": "already searched for this question; answer with what you have"}), [], False)
        self.used = True
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
                return SearchOutcome(
                    self._result(row.answer, _aware(row.fetched_at), now, tz, cached=True),
                    [UsageItem("search", "buddyai", "search_cache", "cache_hit", 1)],
                    True,
                )

        answer, tokens_in, tokens_out, searches = await self.search_fn(query, location, language)
        usage = [
            UsageItem("llm", self.provider, self.model, "input_token", tokens_in),
            UsageItem("llm", self.provider, self.model, "output_token", tokens_out),
            UsageItem("llm", self.provider, self.model, "web_search_call", max(1, searches) if answer else searches),
        ]
        if answer:
            ttl = timedelta(seconds=int(self.ttl_s.get(category, DEFAULT_TTL_S["other"])))
            with session_scope() as db:
                db.exec(delete(SearchCache).where(col(SearchCache.expires_at) < now - timedelta(days=1)))  # type: ignore[call-overload]
                row = db.exec(select(SearchCache).where(SearchCache.key == key)).first() or SearchCache(key=key, scope=scope, category=category, expires_at=now)
                row.location, row.language, row.query = location, language[:8], query
                row.answer, row.fetched_at, row.expires_at = answer, now, now + ttl
                db.add(row)
                db.commit()
        return SearchOutcome(self._result(answer, now, now, tz, cached=False), usage, False)

    @staticmethod
    def _result(answer: str, fetched: datetime, now: datetime, tz: str, cached: bool) -> str:
        if not answer:
            return json.dumps({"ok": False, "error": "no result"})
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
