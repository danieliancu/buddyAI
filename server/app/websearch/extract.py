"""Page text for a search result: download (fetch.py), Trafilatura (main text without menus, ads and
boilerplate), then only the passages that match the question, so the answering model gets a few hundred
words per source rather than whole pages.

A page that cannot be downloaded or extracted keeps its title + snippet (extraction "snippet": weaker
evidence). The publication date comes only from the page's own metadata; the retrieval time is never used
as one.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from app.websearch.fetch import FetchRefused, PageFetcher
from app.websearch.types import SearchResult

log = logging.getLogger(__name__)

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


def _trafilatura(html: bytes, url: str) -> tuple[str, str | None]:
    import trafilatura

    doc = trafilatura.bare_extraction(
        html, url=url, include_comments=False, include_tables=True, favor_precision=True, with_metadata=True,
        date_extraction_params={"extensive_search": False, "original_date": True},
    )
    if not doc:
        return "", None
    d = doc.as_dict() if hasattr(doc, "as_dict") else dict(doc)
    return str(d.get("text") or ""), (str(d["date"])[:10] if d.get("date") else None)


class ContentExtractor:
    def __init__(self, fetcher: PageFetcher, timeout_s: float = 3.0) -> None:
        self.fetcher = fetcher
        self.timeout_s = timeout_s

    async def extract(self, result: SearchResult, query: str, max_chars: int, timeout: float | None = None) -> SearchResult:
        """Fills result.text / extraction / published_at in place (and returns it). Never raises except
        CancelledError."""
        budget = min(self.timeout_s, timeout) if timeout is not None else self.timeout_s
        if budget <= 0.2:
            return result
        try:
            async with asyncio.timeout(budget):
                final_url, html = await self.fetcher.fetch(result.url, budget)
                text, date = await asyncio.to_thread(_trafilatura, html, final_url)
        except (FetchRefused, httpx.HTTPError, TimeoutError, asyncio.TimeoutError, ValueError, OSError) as exc:
            log.info("extract %s: %s", result.domain, exc if isinstance(exc, FetchRefused) else type(exc).__name__)
            result.extraction = "snippet"
            return result
        except Exception as exc:  # noqa: BLE001 - a parser bug on one page must not end the search
            log.warning("extract %s failed: %s", result.domain, type(exc).__name__)
            result.extraction = "snippet"
            return result
        passages = select_passages(text, query, max_chars)
        if not passages:
            result.extraction = "snippet"
            return result
        result.text, result.extraction = passages, "full"
        if date and not result.published_at:
            result.published_at = date
        return result
