"""When our own search (ddgs_brave) finds nothing: OpenAI hosted search, in the same turn.

Our sources (Brave, DuckDuckGo, the pages they lead to) miss specific facts that a hosted search finds - a local
swimming gala's results, who took part in a competition. A longer deadline does not help there (measured: the
search stops on its own, out of sources, not out of time). So a question our search cannot answer goes once to the
hosted search; that adds several seconds, so the watch first writes "looking further…" (wait_hint `search_more`, in
the watch's own language - nothing is spoken). The hosted call is billed as before (`web_search_call` + tokens) and
only for these questions; when it finds nothing either, the model says so.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.search import clean_answer

log = logging.getLogger(__name__)

SearchFn = Callable[..., Awaitable[tuple]]


def with_hosted_fallback(ours: SearchFn, hosted: SearchFn, on_fallback: Callable[[], Awaitable[None]],
                         skip_categories: frozenset[str] = frozenset()) -> SearchFn:
    """`ours` / `hosted`: (query, location, language, category=, local_query=) -> (answer, tokens in, tokens out,
    searches, complete, usage). When `ours` finds nothing: `on_fallback()` (the watch's hint), then `hosted`. The
    result carries the usage of both. `skip_categories`: already answered by the hosted search (the hybrid)."""

    async def search(query: str, location: str, language: str, category: str = "other", local_query: str = "") -> tuple:
        out = await ours(query, location, language, category=category, local_query=local_query)
        if category in skip_categories or clean_answer(out[0] or "")[0]:
            return out
        await on_fallback()
        try:
            h = await hosted(query, location, language, category=category, local_query=local_query)
        except Exception as exc:  # noqa: BLE001 - the user still gets "couldn't find it"
            log.warning("hosted search fallback failed: %s", type(exc).__name__)
            return out
        found = bool(clean_answer(h[0] or "")[0])
        log.info("search fallback: own search found nothing, hosted search %s", "answered" if found else "found nothing")
        usage: list[Any] = [*(out[5] if len(out) > 5 and out[5] else []), *(h[5] if len(h) > 5 and h[5] else [])]
        return h[0], out[1] + h[1], out[2] + h[2], out[3] + h[3], h[4] if len(h) > 4 else True, usage

    return search


# "checking…" (wait_hint `checking`): written on the watch when a web search has run this long and nothing was said
# yet. A quick search (cached answer, weather) is answered without it.
CHECKING_DELAY_S = 1.0
