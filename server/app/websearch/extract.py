"""Page text for a search result: download (fetch.py), Trafilatura (main text without menus, ads and
boilerplate), then only the passages that match the question, so the answering model gets a few hundred
words per source rather than whole pages.

A page that cannot be downloaded or extracted keeps its title + snippet (extraction "snippet": weaker
evidence). The publication date comes only from the page's own metadata; the retrieval time is never used
as one.

Sites that refuse us (401 / 403 / 429 / 451) are skipped for BLOCKED_SKIP_S, and sites that time out twice in a
row for SLOW_SKIP_S (`skip`): their snippet is still used, but no time is spent waiting for them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from app.websearch.fetch import FetchRefused, PageFetcher
from app.websearch.types import SearchResult

log = logging.getLogger(__name__)

BLOCKED_SKIP_S = 6 * 3600.0
SLOW_SKIP_S = 15 * 60.0
SLOW_STRIKES = 2  # timeouts in a row before a site is skipped
SITE_MEMORY = 2000
_BLOCKED = {"http 401", "http 403", "http 429", "http 451"}

_WORD = re.compile(r"[\w°%£$€]+", re.UNICODE)
_STOP = frozenset(
    "a an the of in on at to for and or is are was were be by with from what when where who how which latest next "
    "today now current this that it its as near me".split()
)


def terms(text: str) -> set[str]:
    return {w for w in (m.group(0).casefold() for m in _WORD.finditer(text)) if w not in _STOP and len(w) > 1}


def select_passages(text: str, query: str, max_chars: int) -> str:
    """The paragraphs that share the most terms with the query (numbers and dates count double), in page
    order, up to max_chars."""
    want = terms(query)
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if len(p.strip()) > 25]
    if not paras:
        return ""
    scored = []
    for i, p in enumerate(paras):
        have = terms(p)
        hit = want & have
        score = len(hit) + sum(1 for w in hit if any(ch.isdigit() for ch in w)) + 0.3 * sum(ch.isdigit() for ch in p[:200]) / 10
        scored.append((score, i, p))
    picked: list[tuple[int, str]] = []
    used = 0
    for score, i, p in sorted(scored, key=lambda s: (-s[0], s[1])):
        if score <= 0 and picked:
            break
        p = p[: max_chars - used] if used + len(p) > max_chars else p
        if len(p) < 25:
            break
        picked.append((i, p))
        used += len(p) + 1
        if used >= max_chars:
            break
    return "\n".join(p for _, p in sorted(picked))


# Scripts, styles, inline SVG and comments carry no readable text but are most of a modern page's weight
# (and of the parse time): dropped before parsing.
_NOISE = re.compile(rb"(?is)<script\b.*?</script\s*>|<style\b.*?</style\s*>|<svg\b.*?</svg\s*>|<!--.*?-->|<noscript\b.*?</noscript\s*>")

PARSE_MAX_BYTES = 400_000  # parse time grows with the page; an article's text sits well within this


def _trafilatura(html: bytes, url: str) -> tuple[str, str | None]:
    import trafilatura
    from htmldate import find_date

    page = _NOISE.sub(b" ", html)[:PARSE_MAX_BYTES]
    # fast: the main extractor only (no fallback extractors), and no metadata pass (author, site name, tags:
    # seconds on a large page) - only the page's own publication date, from htmldate directly
    text = trafilatura.extract(page, url=url, fast=True, include_comments=False, include_tables=True,
                               favor_precision=True, output_format="txt") or ""
    try:
        date = find_date(page, url=url, extensive_search=False, original_date=True)
    except Exception:  # noqa: BLE001 - no date is fine
        date = None
    return text, (str(date)[:10] if date else None)


class ContentExtractor:
    def __init__(self, fetcher: PageFetcher, timeout_s: float = 3.0, clock=time.monotonic) -> None:
        self.fetcher = fetcher
        self.timeout_s = timeout_s
        self.clock = clock
        self._skip_until: dict[str, float] = {}
        self._slow: dict[str, int] = {}  # timeouts in a row per site

    def skip(self, domain: str) -> bool:
        """Whether a site recently refused us or kept timing out (not worth waiting for)."""
        until = self._skip_until.get(domain)
        if until is None:
            return False
        if until > self.clock():
            return True
        del self._skip_until[domain]
        return False

    def _remember(self, domain: str, seconds: float) -> None:
        if len(self._skip_until) >= SITE_MEMORY:
            self._skip_until.pop(next(iter(self._skip_until)))
        self._skip_until[domain] = self.clock() + seconds

    async def extract(self, result: SearchResult, query: str, max_chars: int, timeout: float | None = None) -> SearchResult:
        """Fills result.text / extraction / published_at in place (and returns it). Never raises except
        CancelledError."""
        budget = min(self.timeout_s, timeout) if timeout is not None else self.timeout_s
        if budget <= 0.2:
            return result
        domain = result.domain
        try:
            async with asyncio.timeout(budget):
                final_url, html = await self.fetcher.fetch(result.url, budget)
                text, date = await asyncio.to_thread(_trafilatura, html, final_url)
        except (FetchRefused, httpx.HTTPError, TimeoutError, asyncio.TimeoutError, ValueError, OSError) as exc:
            log.info("extract %s: %s", domain, exc if isinstance(exc, FetchRefused) else type(exc).__name__)
            result.extraction = "snippet"
            if isinstance(exc, FetchRefused) and str(exc) in _BLOCKED:
                self._remember(domain, BLOCKED_SKIP_S)
            elif isinstance(exc, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)) \
                    and budget >= 0.8 * self.timeout_s:
                # only a timeout with the full allowance counts (a short budget is our deadline, not the site)
                self._slow[domain] = self._slow.get(domain, 0) + 1
                if self._slow[domain] >= SLOW_STRIKES:
                    self._slow.pop(domain)
                    self._remember(domain, SLOW_SKIP_S)
                elif len(self._slow) > SITE_MEMORY:
                    self._slow.clear()
            return result
        except Exception as exc:  # noqa: BLE001 - a parser bug on one page must not end the search
            log.warning("extract %s failed: %s", domain, type(exc).__name__)
            result.extraction = "snippet"
            return result
        self._slow.pop(domain, None)
        passages = select_passages(text, query, max_chars)
        if not passages:
            result.extraction = "snippet"
            return result
        result.text, result.extraction = passages, "full"
        if date and not result.published_at:
            result.published_at = date
        return result
