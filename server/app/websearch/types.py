"""Shared types of the search backend: one normalised result shape for every provider, and provider errors
kept apart from "nothing relevant" (an empty or off-topic result list is not an outage)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

# SearchError kinds. timeout / rate_limited / blocked / network count as technical failures (circuit breaker);
# budget = Brave's monthly budget is used up; unavailable = not configured.
TECHNICAL = {"timeout", "rate_limited", "blocked", "network"}


class SearchError(Exception):
    def __init__(self, provider: str, kind: str, detail: str = "") -> None:
        super().__init__(f"{provider}: {kind}{f' ({detail})' if detail else ''}")
        self.provider, self.kind, self.detail = provider, kind, detail

    @property
    def technical(self) -> bool:
        return self.kind in TECHNICAL


@dataclass
class SearchResult:
    provider: str  # ddgs | brave
    title: str
    url: str
    snippet: str
    rank: int = 0
    published_at: str | None = None  # only a date the source states, never the retrieval time
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    text: str = ""  # selected passages of the page (extract.py)
    extraction: str = "snippet"  # full (page text) | snippet (title + snippet only) | failed
    relevance: float = 0.0
    reliability: float = 0.5  # 0..1, a hint from the domain (evidence.py)

    @property
    def domain(self) -> str:
        host = (urlsplit(self.url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
