"""Search providers: DDGS (metasearch library, no per-request charge, not an official API) and the Brave
Search API (official, paid per request, the fallback). Both return normalised SearchResult lists and raise
SearchError for technical problems; an empty list means "nothing found", not an outage.

The DDGS library is synchronous: it runs in a worker thread with its own timeout plus an outer wait_for, and a
semaphore bounds how many run at once, so a slow search never blocks the event loop. Its engines are named
(ddgs_backends): "auto" always asks Wikipedia and Grokipedia first and waits out the slow engines (3-4 s, measured).

Brave is asked for extra snippets (up to 5 more excerpts per result: often the answer itself, without reading
the page), and its local results (opening hours) and infobox become results too. For sport, the response may
carry a rich-data key: `rich` turns it into structured standings / games / race calendar (one more request).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from app.websearch.types import Results, SearchError, SearchResult

log = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_RICH_URL = "https://api.search.brave.com/res/v1/web/rich"
SNIPPET_MAX = 700  # description + extra snippets per Brave result
_TAGS = re.compile(r"<[^>]+>")

# The watch's time zone -> (DDGS region, Brave country). Anything else: no region ("wt-wt" / no country).
_REGIONS = {
    "Europe/London": ("uk-en", "GB"), "Europe/Dublin": ("ie-en", "IE"), "Europe/Bucharest": ("ro-ro", "RO"),
    "Europe/Paris": ("fr-fr", "FR"), "Europe/Berlin": ("de-de", "DE"), "Europe/Madrid": ("es-es", "ES"),
    "Europe/Rome": ("it-it", "IT"), "Europe/Amsterdam": ("nl-nl", "NL"), "Europe/Brussels": ("be-fr", "BE"),
    "Europe/Vienna": ("at-de", "AT"), "Europe/Warsaw": ("pl-pl", "PL"), "Europe/Lisbon": ("pt-pt", "PT"),
    "Europe/Stockholm": ("se-sv", "SE"), "Europe/Copenhagen": ("dk-da", "DK"), "Europe/Helsinki": ("fi-fi", "FI"),
    "Europe/Athens": ("gr-el", "GR"), "Europe/Prague": ("cz-cs", "CZ"), "Europe/Budapest": ("hu-hu", "HU"),
}
# Categories whose answers age fast: recent pages only (sport: a national team plays about monthly).
_BRAVE_FRESHNESS = {"news": "pw", "sport": "pm", "transport": "pd", "weather": "pd"}
_DDGS_TIMELIMIT = {"news": "w", "sport": "m", "transport": "d", "weather": "d"}


def region_for(tz: str, override: str = "") -> tuple[str, str]:
    """(DDGS region, Brave country) for the watch's time zone; `override` (setting ddgs_region) wins for DDGS."""
    ddgs_region, country = _REGIONS.get(tz, ("wt-wt", ""))
    return (override or ddgs_region), country


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", text or ""))).strip()


class SharedClient:
    """One keep-alive httpx.AsyncClient per event loop (a client cannot outlive its loop), for fixed API hosts
    only - never for result URLs (fetch.py pins those to a vetted address)."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def get(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client, self._loop = httpx.AsyncClient(**self.kwargs), loop
        return self._client


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, tz: str, category: str, max_results: int, timeout: float,
                     local: bool = True) -> list[SearchResult]: ...


class DDGSSearchProvider:
    name = "ddgs"

    def __init__(self, region_override: str = "", max_concurrency: int = 4, backends: str = "duckduckgo,yahoo") -> None:
        self.region_override = region_override
        self.backends = backends or "auto"
        self._sem = asyncio.Semaphore(max(1, max_concurrency))

    def _run(self, query: str, region: str, max_results: int, timeout: float, timelimit: str | None) -> list[dict]:
        from ddgs import DDGS

        return DDGS(timeout=max(1, int(timeout))).text(query, region=region, safesearch="moderate", max_results=max_results,
                                                       timelimit=timelimit, backend=self.backends)

    async def search(self, query: str, *, tz: str, category: str, max_results: int, timeout: float,
                     local: bool = True) -> list[SearchResult]:
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

        region = region_for(tz, self.region_override)[0] if local else "wt-wt"  # a place abroad: no region
        timelimit = _DDGS_TIMELIMIT.get(category)
        async with self._sem:
            try:
                rows = await asyncio.wait_for(asyncio.to_thread(self._run, query, region, max_results, timeout, timelimit),
                                              timeout)
            except (asyncio.TimeoutError, TimeoutException) as exc:
                raise SearchError(self.name, "timeout") from exc
            except RatelimitException as exc:
                raise SearchError(self.name, "rate_limited") from exc
            except DDGSException as exc:
                # "No results found" is how the library reports an empty page: not an outage
                if "no results" in str(exc).lower():
                    return []
                raise SearchError(self.name, "blocked", type(exc).__name__) from exc
            except (OSError, httpx.HTTPError) as exc:
                raise SearchError(self.name, "network", type(exc).__name__) from exc
        out = []
        for i, r in enumerate(rows or []):
            url = str(r.get("href") or r.get("url") or "")
            if url.startswith(("http://", "https://")):
                out.append(SearchResult("ddgs", _clean(str(r.get("title") or "")), url, _clean(str(r.get("body") or "")), rank=i))
        return out


class BraveSearchProvider:
    """Brave Search API (web search endpoint only - no paid AI answers). The key goes in the
    X-Subscription-Token header, never in a URL or a log line."""

    name = "brave"

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self._client = SharedClient(transport=transport, follow_redirects=False)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def _get(self, url: str, params: dict, timeout: float) -> dict:
        if not self.api_key:
            raise SearchError(self.name, "unavailable")
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        try:
            r = await self._client.get().get(url, params=params, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise SearchError(self.name, "timeout") from exc
        except httpx.HTTPError as exc:
            raise SearchError(self.name, "network", type(exc).__name__) from exc
        if r.status_code == 429:
            raise SearchError(self.name, "rate_limited")
        if r.status_code in (401, 403):
            raise SearchError(self.name, "blocked", f"http {r.status_code}")
        if r.status_code >= 400:
            raise SearchError(self.name, "network", f"http {r.status_code}")
        try:
            data = r.json()
        except ValueError as exc:
            raise SearchError(self.name, "network", "bad json") from exc
        return data if isinstance(data, dict) else {}

    async def search(self, query: str, *, tz: str, category: str, max_results: int, timeout: float,
                     local: bool = True) -> Results:
        country = region_for(tz)[1] if local else ""  # a place abroad: no country preference
        params: dict[str, str | int] = {"q": query, "count": min(20, max_results), "search_lang": "en",
                                        "safesearch": "moderate", "text_decorations": 0, "extra_snippets": "true"}
        if country:
            params["country"] = country
        if category in _BRAVE_FRESHNESS:
            params["freshness"] = _BRAVE_FRESHNESS[category]
        if category == "sport":
            params["enable_rich_callback"] = 1
        data = await self._get(BRAVE_URL, params, timeout)
        out = Results(_locations(data) + _infobox(data))
        for i, row in enumerate((data.get("web") or {}).get("results") or []):
            url = str(row.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            page_age = row.get("page_age")  # ISO date the page states; "age" is only a human string
            parts = [_clean(str(row.get("description") or ""))]
            for extra in row.get("extra_snippets") or []:
                text = _clean(str(extra))
                if text and text not in parts:
                    parts.append(text)
            out.append(SearchResult(
                "brave", _clean(str(row.get("title") or "")), url, " … ".join(p for p in parts if p)[:SNIPPET_MAX],
                rank=i, published_at=str(page_age)[:10] if page_age else None,
            ))
        hint = (data.get("rich") or {}).get("hint") or {}
        out.rich_key = str(hint.get("callback_key") or "") if category == "sport" else ""
        return out

    async def rich(self, callback_key: str, *, tz: str, timeout: float) -> list[SearchResult]:
        """Structured sport data for a rich-data key from `search` (one more Brave request)."""
        data = await self._get(BRAVE_RICH_URL, {"callback_key": callback_key}, timeout)
        return rich_results(data, tz)


def _locations(data: dict) -> list[SearchResult]:
    """Brave's local results: name, address, opening hours by day, phone."""
    out = []
    for row in ((data.get("locations") or {}).get("results") or [])[:3]:
        url = str(row.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        parts = []
        address = (row.get("postal_address") or {}).get("displayAddress")
        if address:
            parts.append(f"Address: {_clean(str(address))}")
        days = []
        for day in (row.get("opening_hours") or {}).get("days") or []:
            spans = [f"{s.get('opens')}-{s.get('closes')}" for s in day if isinstance(s, dict) and s.get("opens")]
            if spans and isinstance(day[0], dict):
                days.append(f"{day[0].get('abbr_name') or day[0].get('full_name')} {', '.join(spans)}")
        if days:
            parts.append(f"Opening hours: {'; '.join(days)}")
        phone = (row.get("contact") or {}).get("telephone")
        if phone:
            parts.append(f"Phone: {_clean(str(phone))}")
        if parts:
            out.append(SearchResult("brave", _clean(str(row.get("title") or "")), url, ". ".join(parts), rank=0))
    return out


def _infobox(data: dict) -> list[SearchResult]:
    out = []
    for row in ((data.get("infobox") or {}).get("results") or [])[:1]:
        url = str(row.get("url") or "")
        attrs = [f"{_clean(str(a[0]))}: {_clean(str(a[1]))}" for a in row.get("attributes") or []
                 if isinstance(a, list) and len(a) == 2 and a[0] and a[1]]
        text = ". ".join(p for p in [_clean(str(row.get("long_desc") or row.get("description") or "")), *attrs] if p)
        if url.startswith(("http://", "https://")) and len(text) >= 40:
            out.append(SearchResult("brave", _clean(str(row.get("title") or "")), url, text[:SNIPPET_MAX], rank=0))
    return out


_FINISHED = {"FT", "AET", "PEN", "AWD", "WO"}
_UPCOMING = {"NS", "TBD", "PST"}


def rich_results(data: dict, tz: str) -> list[SearchResult]:
    """Football standings and games, and the Formula 1 calendar, as text results (other verticals: none)."""
    try:
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        zone = ZoneInfo("UTC")
    now = time.time()

    def when(ts: Any) -> str:
        try:
            return datetime.fromtimestamp(float(ts), timezone.utc).astimezone(zone).strftime("%a %d %b %Y %H:%M")
        except (TypeError, ValueError, OverflowError, OSError):
            return "date unknown"

    out = []
    for item in data.get("results") or []:
        provider = (item.get("provider") or {})
        url = str(provider.get("url") or "")
        if not url.startswith(("http://", "https://")):
            url = "https://api-sports.io/"
        source = _clean(str(provider.get("name") or "sports data"))
        sport = next((v for v in item.values() if isinstance(v, dict) and isinstance(v.get("content"), dict)), None)
        if sport:
            c = sport["content"]
            league = _clean(str((c.get("league") or {}).get("name") or ""))
            team = _clean(str((c.get("team") or {}).get("name") or ""))
            comps = {x.get("id"): _clean(str(x.get("name") or "")) for x in c.get("competitions") or [] if isinstance(x, dict)}
            lines = []
            games = sorted((g for g in c.get("games") or [] if isinstance(g, dict)), key=lambda g: g.get("start_time") or 0)

            def game(g: dict) -> str:
                home = _clean(str(((g.get("teams") or {}).get("home") or {}).get("name") or "?"))
                away = _clean(str(((g.get("teams") or {}).get("away") or {}).get("name") or "?"))
                score = g.get("score") or {}
                code = str((g.get("status") or {}).get("code") or "")
                vs = f"{home} {score.get('home')}-{score.get('away')} {away}" if score and code not in _UPCOMING else f"{home} v {away}"
                comp = comps.get(g.get("competition_id")) or league
                state = "full time" if code in _FINISHED else "not started" if code in _UPCOMING else f"status {code}"
                return f"{when(g.get('start_time'))}: {vs} ({comp}, {_clean(str(g.get('round') or ''))}, {state})"

            done = [g for g in games if str((g.get("status") or {}).get("code")) not in _UPCOMING and (g.get("start_time") or 0) <= now]
            ahead = [g for g in games if str((g.get("status") or {}).get("code")) in _UPCOMING]
            if done:
                lines.append("Latest results: " + "; ".join(game(g) for g in done[-3:][::-1]))
            if ahead:
                lines.append("Next games: " + "; ".join(game(g) for g in ahead[:3]))
            for group in (c.get("group_standings") or [])[:1]:
                rows = [s for s in group.get("standings") or [] if isinstance(s, dict)]
                keep = rows[:5] + [s for s in rows[5:] if team and _clean(str((s.get("team") or {}).get("name"))) == team]
                table = "; ".join(
                    f"{s.get('rank')}. {_clean(str((s.get('team') or {}).get('name') or '?'))} {s.get('points')} pts "
                    f"(played {s.get('matches_played')}, W{s.get('matches_won')} D{s.get('matches_drawn')} "
                    f"L{s.get('matches_lost')}, goals {s.get('goals_for')}-{s.get('goals_against')})" for s in keep)
                if table:
                    lines.append(f"{_clean(str(group.get('group_name') or league))} table: {table}")
            if lines:
                lines.append("Structured sports data: it may not list every competition (e.g. cup games).")
                title = f"{team + ' match results and fixtures, ' if team else ''}{league} games and table".strip()
                text = "\n".join(lines)
                out.append(SearchResult("brave", f"{title} ({source})", url, text[:SNIPPET_MAX], rank=0,
                                        text=text, extraction="full"))
        f1 = item.get("formula1")
        if isinstance(f1, dict):
            races = []
            for r in (f1.get("calendar") or {}).get("races") or []:
                try:
                    ts = datetime.fromisoformat(str(r.get("date"))).timestamp()
                except ValueError:
                    continue
                races.append((ts, _clean(str(r.get("name") or "?"))))
            races.sort()
            past = [f"{name} ({when(ts)})" for ts, name in races if ts <= now][-2:]
            ahead = [f"{name} ({when(ts)})" for ts, name in races if ts > now][:3]
            lines = ([f"Latest races: {'; '.join(past[::-1])}"] if past else []) + \
                    ([f"Next races: {'; '.join(ahead)}"] if ahead else [])
            if lines:
                text = "\n".join(lines)
                out.append(SearchResult("brave", f"Formula 1 race calendar ({source})", url, text[:SNIPPET_MAX], rank=0,
                                        text=text, extraction="full"))
    return out


class CircuitBreaker:
    """Opens after `threshold` consecutive technical failures, stays open for `cooldown_s`, then lets one trial
    request through (half-open): success closes it, failure opens it again. Irrelevant results never count."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 120.0, clock=time.monotonic) -> None:
        self.threshold, self.cooldown_s, self.clock = max(1, threshold), cooldown_s, clock
        self.failures = 0
        self.opened_at: float | None = None
        self._trial = False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self.opened_at >= self.cooldown_s else "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._trial:
            self._trial = True
            return True
        return False

    def release(self) -> None:
        """A half-open trial that was taken but not used (nothing left to search): the next request may try."""
        self._trial = False

    def success(self) -> None:
        if self.opened_at is not None:
            log.info("search circuit closed again")
        self.failures, self.opened_at, self._trial = 0, None, False

    def failure(self) -> None:
        self.failures += 1
        if self._trial or self.failures >= self.threshold:
            if self.opened_at is None or self._trial:
                log.warning("search circuit open for %ss after %s failures", self.cooldown_s, self.failures)
            self.opened_at, self._trial = self.clock(), False
