"""Search providers: DDGS (metasearch library, no per-request charge, not an official API) and the Brave
Search API (official, paid per request, the fallback). Both return normalised SearchResult lists and raise
SearchError for technical problems; an empty list means "nothing found", not an outage.

The DDGS library is synchronous: it runs in a worker thread with its own timeout plus an outer wait_for, and a
semaphore bounds how many run at once, so a slow search never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from typing import Protocol

import httpx

from app.websearch.types import SearchError, SearchResult

log = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
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
# Categories whose answers age fast: Brave is asked for recent pages only.
_BRAVE_FRESHNESS = {"news": "pw", "sport": "pw", "transport": "pd", "weather": "pd"}


def region_for(tz: str, override: str = "") -> tuple[str, str]:
    """(DDGS region, Brave country) for the watch's time zone; `override` (setting ddgs_region) wins for DDGS."""
    ddgs_region, country = _REGIONS.get(tz, ("wt-wt", ""))
    return (override or ddgs_region), country


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", text or ""))).strip()


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, tz: str, category: str, max_results: int, timeout: float) -> list[SearchResult]: ...


class DDGSSearchProvider:
    name = "ddgs"

    def __init__(self, region_override: str = "", max_concurrency: int = 4) -> None:
        self.region_override = region_override
        self._sem = asyncio.Semaphore(max(1, max_concurrency))

    def _run(self, query: str, region: str, max_results: int, timeout: float) -> list[dict]:
        from ddgs import DDGS

        return DDGS(timeout=max(1, int(timeout))).text(query, region=region, safesearch="moderate", max_results=max_results)

    async def search(self, query: str, *, tz: str, category: str, max_results: int, timeout: float) -> list[SearchResult]:
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

        region, _ = region_for(tz, self.region_override)
        async with self._sem:
            try:
                rows = await asyncio.wait_for(asyncio.to_thread(self._run, query, region, max_results, timeout), timeout)
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
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, *, tz: str, category: str, max_results: int, timeout: float) -> list[SearchResult]:
        if not self.api_key:
            raise SearchError(self.name, "unavailable")
        _, country = region_for(tz)
        params: dict[str, str | int] = {"q": query, "count": min(20, max_results), "search_lang": "en",
                                        "safesearch": "moderate", "text_decorations": 0}
        if country:
            params["country"] = country
        if category in _BRAVE_FRESHNESS:
            params["freshness"] = _BRAVE_FRESHNESS[category]
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport, follow_redirects=False) as c:
                r = await c.get(BRAVE_URL, params=params, headers=headers)
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
            rows = (r.json().get("web") or {}).get("results") or []
        except ValueError as exc:
            raise SearchError(self.name, "network", "bad json") from exc
        out = []
        for i, row in enumerate(rows):
            url = str(row.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            page_age = row.get("page_age")  # ISO date the page states; "age" is only a human string
            out.append(SearchResult(
                "brave", _clean(str(row.get("title") or "")), url, _clean(str(row.get("description") or "")),
                rank=i, published_at=str(page_age)[:10] if page_age else None,
            ))
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
