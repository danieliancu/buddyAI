"""Shared base for HTTP TTS APIs that return raw PCM for one text fragment per request.

Fragments are synthesized in order; the next fragment's request starts while the current one is
still streaming (prefetch depth 2), so there is no audible gap between fragments.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.providers.base import ProviderError
from app.providers.tts.base import PCMChunk, TTSProvider, TTSRequest

log = logging.getLogger(__name__)
_DONE = object()


class HTTPStreamingTTS(TTSProvider):
    sample_rate = 24000
    label = "TTS"

    def __init__(self, prefetch: int = 2, timeout_s: float = 15.0, hedge_after_s: float | None = 1.5) -> None:
        self.prefetch = prefetch
        self.hedge_after_s = hedge_after_s
        self._client = httpx.AsyncClient(timeout=timeout_s)

    @abstractmethod
    def check_configured(self) -> None:
        """Raise ProviderError when credentials are missing."""

    @abstractmethod
    def build_request(self, text: str, request: TTSRequest) -> dict[str, Any]:
        """kwargs for httpx.AsyncClient.stream("POST", ...): url, headers, content/json."""

    async def _attempt(self, text: str, request: TTSRequest, out: asyncio.Queue) -> None:
        try:
            async with self._client.stream("POST", **self.build_request(text, request)) as r:
                if r.status_code != 200:
                    body = (await r.aread())[:300]
                    raise ProviderError("tts", f"{self.label} HTTP {r.status_code}: {body!r}")
                async for data in r.aiter_bytes(4800):
                    await out.put(data)
            await out.put(_DONE)
        except ProviderError as exc:
            await out.put(exc)
        except httpx.HTTPError as exc:
            await out.put(ProviderError("tts", f"{self.label} request failed: {exc}"))

    async def _fetch(self, text: str, request: TTSRequest, out: asyncio.Queue) -> None:
        """One fragment, hedged: a request that has not produced audio after `hedge_after_s` (or
        failed before any audio) gets a twin; the first to produce audio wins, the other is
        cancelled. Slow starts are how stalled requests show up (gpt-4o-mini-tts: ~1 in 6 took
        5-70 s instead of <1 s), and a twin almost always answers normally."""
        attempts: list[tuple[asyncio.Task, asyncio.Queue]] = []
        getters: dict[asyncio.Task, asyncio.Queue] = {}
        dead: set[int] = set()
        last_error: BaseException | None = None

        def launch() -> None:
            q: asyncio.Queue = asyncio.Queue()
            attempts.append((asyncio.create_task(self._attempt(text, request, q)), q))

        launch()
        try:
            while True:
                for _, q in attempts:
                    if id(q) not in dead and q not in getters.values():
                        getters[asyncio.create_task(q.get())] = q
                can_hedge = self.hedge_after_s is not None and len(attempts) < 2
                done, _ = await asyncio.wait(
                    getters, timeout=self.hedge_after_s if can_hedge else None, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    log.info("%s: no audio after %.1f s, sending a twin request", self.label, self.hedge_after_s)
                    launch()
                    continue
                getter = done.pop()
                winner = getters.pop(getter)
                first = getter.result()
                if isinstance(first, Exception):
                    dead.add(id(winner))
                    last_error = first
                    if len(attempts) < 2 and self.hedge_after_s is not None:
                        launch()  # failed before any audio: one retry
                    if all(id(q) in dead for _, q in attempts):
                        await out.put(last_error)
                        return
                    continue
                break
            for g in getters:
                g.cancel()
            for t, q in attempts:
                if q is not winner:
                    t.cancel()
            item = first
            while True:
                await out.put(item)
                if item is _DONE or isinstance(item, Exception):
                    return
                item = await winner.get()
        finally:
            for g in getters:
                g.cancel()
            for t, _ in attempts:
                t.cancel()

    async def stream(self, text_chunks: AsyncIterator[str], request: TTSRequest) -> AsyncIterator[PCMChunk]:
        self.check_configured()
        pending: asyncio.Queue[asyncio.Queue | None] = asyncio.Queue(maxsize=self.prefetch)
        fetches: list[asyncio.Task] = []

        async def producer() -> None:
            async for text in text_chunks:
                q: asyncio.Queue = asyncio.Queue()
                fetches.append(asyncio.create_task(self._fetch(text, request, q)))
                await pending.put(q)  # blocks while `prefetch` fragments are in flight
            await pending.put(None)

        prod = asyncio.create_task(producer())
        leftover = b""
        try:
            while True:
                q = await pending.get()
                if q is None:
                    break
                while True:
                    item = await q.get()
                    if item is _DONE:
                        break
                    if isinstance(item, Exception):
                        raise item
                    data = leftover + item
                    even = len(data) - (len(data) % 2)  # keep 16-bit alignment
                    leftover = data[even:]
                    if even:
                        yield PCMChunk(data[:even], self.sample_rate)
            if prod.done() and prod.exception():
                raise prod.exception()  # type: ignore[misc]
        finally:
            for t in [prod, *fetches]:
                t.cancel()
            for t in [prod, *fetches]:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
