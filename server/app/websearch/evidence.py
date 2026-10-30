"""Evidence checks before anything reaches the answering model: relevance to the query, near-duplicates
(one site or one syndicated story must not dominate), a reliability hint per domain, freshness for fast-moving
categories, and a compact package marked as untrusted data.

Web pages are untrusted: their text is only ever data inside <untrusted_web_content>; markers that could
close that block or pose as chat roles / tool calls are neutralised. The answering model has no tools.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.websearch.extract import terms
from app.websearch.types import SearchResult

MAX_PER_DOMAIN = 2
NEAR_DUPLICATE = 0.8  # word-shingle similarity of two snippets above which the later one is dropped
MIN_COVERAGE = 0.4  # share of the query's terms a result must contain to count as relevant

# Authoritative sources (public bodies, operators, governing bodies, wire services) and weak ones (forums,
# social media, content farms). Everything else is neutral. Only a ranking hint, never a filter.
_STRONG = (
    ".gov.uk", ".gov", ".gov.ro", ".europa.eu", ".nhs.uk", ".ac.uk", ".police.uk", "metoffice.gov.uk",
    "bbc.co.uk", "bbc.com", "tfl.gov.uk", "nationalrail.co.uk", "uefa.com", "fifa.com", "premierleague.com",
    "reuters.com", "apnews.com", "met.no", "meteoromania.ro", "ihf.info", "eurohandball.com", "worldathletics.org",
)
_WEAK = (
    "reddit.com", "quora.com", "pinterest.com", "facebook.com", "tiktok.com", "instagram.com", "x.com",
    "twitter.com", "youtube.com", "answers.com", "medium.com",
)
# Days after which a dated page is weaker evidence for a category (only when the page states its date).
_FRESH_DAYS = {"weather": 2, "transport": 2, "news": 7, "sport": 21}

_ROLE = re.compile(r"(?im)^\s*(system|assistant|user|developer|tool)\s*:")
_MARKUP = re.compile(r"(?i)</?\s*untrusted_web_content\s*>|<\|[^|>]{0,40}\|>|\[/?(?:INST|SYS)\]")


def reliability(domain: str) -> float:
    d = "." + domain
    if any(d.endswith(s if s.startswith(".") else "." + s) for s in _STRONG):
        return 0.9
    if any(d.endswith("." + s) for s in _WEAK):
        return 0.2
    return 0.5


def coverage(query: str, r: SearchResult) -> float:
    want = terms(query)
    if not want:
        return 1.0
    have = terms(" ".join((r.title, r.snippet, r.text, r.domain.replace(".", " "))))
    return len(want & have) / len(want)


def _shingles(text: str) -> set[tuple[str, ...]]:
    w = [m.casefold() for m in re.findall(r"\w+", text)]
    return {tuple(w[i:i + 3]) for i in range(max(1, len(w) - 2))} if w else set()


def _similar(a: str, b: str) -> bool:
    sa, sb = _shingles(a), _shingles(b)
    return bool(sa and sb) and len(sa & sb) / min(len(sa), len(sb)) >= NEAR_DUPLICATE


def norm_url(url: str) -> str:
    p = urlsplit(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}"


def _age_days(published: str | None, today: date) -> int | None:
    if not published:
        return None
    try:
        return (today - date.fromisoformat(published[:10])).days
    except ValueError:
        return None


def rank(query: str, results: list[SearchResult], category: str, today: date) -> list[SearchResult]:
    """Relevant results, best first, without duplicates; scores are set on the results."""
    seen_urls: set[str] = set()
    per_domain: dict[str, int] = {}
    kept: list[SearchResult] = []
    # most reliable first, so a duplicate keeps the authoritative copy (an aggregator's copy is dropped)
    for r in sorted(results, key=lambda r: (-reliability(r.domain), r.rank)):
        key = norm_url(r.url)
        if key in seen_urls or per_domain.get(r.domain, 0) >= MAX_PER_DOMAIN:
            continue
        if any(_similar(r.snippet, k.snippet) for k in kept if r.snippet and k.snippet):
            continue
        cov = coverage(query, r)
        if cov < MIN_COVERAGE:
            continue
        r.reliability = reliability(r.domain)
        age = _age_days(r.published_at, today)
        stale = age is not None and category in _FRESH_DAYS and age > _FRESH_DAYS[category]
        future = age is not None and age < -2  # a "published" date in the future is not to be trusted
        r.relevance = cov + 0.4 * r.reliability - 0.03 * r.rank - (0.5 if stale else 0) - (0.3 if future else 0)
        seen_urls.add(key)
        per_domain[r.domain] = per_domain.get(r.domain, 0) + 1
        kept.append(r)
    return sorted(kept, key=lambda r: -r.relevance)


def sanitise(text: str) -> str:
    text = _MARKUP.sub(" ", text)
    return _ROLE.sub(lambda m: f"{m.group(1)} -", text)


def package(results: list[SearchResult], tz: str) -> str:
    """The evidence block for the answering model."""
    try:
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        zone = ZoneInfo("UTC")
    lines = ["<untrusted_web_content>"]
    for n, r in enumerate(results, 1):
        retrieved: datetime = r.retrieved_at.astimezone(zone)
        lines.append(
            f"[{n}] {sanitise(r.title)[:160]} - {r.domain} - published: {r.published_at or 'unknown'} - "
            f"retrieved: {retrieved:%Y-%m-%d %H:%M} - {'page text' if r.extraction == 'full' else 'search snippet only'}"
        )
        body = r.text if r.extraction == "full" and r.text else r.snippet
        lines.append(sanitise(body).strip())
        lines.append("")
    lines.append("</untrusted_web_content>")
    return "\n".join(lines)
