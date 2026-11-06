"""MultiLangTTS: a reply read in its language, with passages in other languages read in theirs.

Input: (language, text) fragments from the chunker. Output: PCM in reply order.

- Text in the reply language goes to the reply's voice as ONE stream per run of fragments. A reply
  without foreign passages is exactly one stream() call with the same fragments as before this module
  existed (same connection, same prefetch, same first-audio time). The first run is opened at once,
  before any text, as the voice was before (Qwen connects while the model is still thinking).
- Each foreign fragment is a job of its own that starts as soon as the fragment arrives, while the audio
  before it is still playing, so a change of language adds no gap.
- A foreign fragment tries its routes in order (router.tts_passage_routes); a route that fails or gives
  no audio within `first_audio_s` hands over to the next one. The last route is always the reply's own
  voice in the reply language: what was heard before this module existed. A foreign fragment never ends
  the turn: if its audio breaks off after it started, the reply simply goes on.
- Characters are counted per provider (`usage`), so each provider used in a turn is billed for its part.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from app.pipeline.spoken import for_speech
from app.providers.tts.base import PCMChunk, TTSProvider, TTSRequest

log = logging.getLogger(__name__)

_END = object()
FIRST_AUDIO_S = 3.0  # a foreign route silent this long gives way to the next (HTTP hedging acts at 1.5 s)


@dataclass(frozen=True)
class Route:
    provider: TTSProvider
    voice: str
    language: str  # the language the provider is told to speak
    instructions: str = ""


class MultiLangTTS:
    def __init__(
        self,
        main: Route,
        passage_routes: Callable[[str], list[Route]],
        speech_rate: float = 1.0,
        first_audio_s: float = FIRST_AUDIO_S,
        on_request: Callable[[int], None] | None = None,
    ) -> None:
        self.main = main
        self.passage_routes = passage_routes
        self.speech_rate = speech_rate
        self.first_audio_s = first_audio_s
        self.on_request = on_request  # every synthesis request of every provider (latency marks, tests)
        self.usage: dict[tuple[str, str], int] = {}  # (provider name, model) -> characters
        self.fallbacks = 0  # foreign fragments that were not read by their first route

    # --- accounting ---------------------------------------------------------------------

    def _count(self, provider: TTSProvider, chars: int) -> None:
        key = (provider.name, getattr(provider, "model", ""))
        self.usage[key] = self.usage.get(key, 0) + chars
        if self.on_request is not None:
            self.on_request(chars)

    def _request(self, route: Route) -> TTSRequest:
        reports = getattr(route.provider, "reports_requests", False)
        return TTSRequest(
            voice=route.voice, language=route.language, speech_rate=self.speech_rate, instructions=route.instructions,
            on_request=(lambda n: self._count(route.provider, n)) if reports else None,
        )

    def _spoken(self, route: Route, text: str) -> str:
        """Text as this route says it; providers that do not report requests are counted on what they get."""
        spoken = for_speech(text, route.language)
        if not getattr(route.provider, "reports_requests", False):
            self._count(route.provider, len(spoken))
        return spoken

    # --- jobs -----------------------------------------------------------------------------

    async def _main_run(self, texts: asyncio.Queue, out: asyncio.Queue) -> None:
        """A run of reply-language fragments through the reply's voice (errors as before: the turn fails)."""
        route = self.main
        sent = 0

        async def chunks() -> AsyncIterator[str]:
            nonlocal sent
            while (t := await texts.get()) is not None:
                sent += 1
                yield self._spoken(route, t)

        try:
            async for pcm in route.provider.stream(chunks(), self._request(route)):
                await out.put(pcm)
            await out.put(_END)
        except Exception as exc:  # noqa: BLE001 - handed to the player
            if sent == 0:
                # Opened ahead of any text: if the run turns out empty (the reply starts with a foreign passage)
                # its error does not matter; if text comes, the error is the reply's error.
                while (t := await texts.get()) is not None:
                    await out.put(exc)
                    return
                await out.put(_END)
                return
            await out.put(exc)

    async def _one(self, route: Route, text: str, q: asyncio.Queue) -> None:
        async def chunks() -> AsyncIterator[str]:
            yield self._spoken(route, text)

        try:
            async for pcm in route.provider.stream(chunks(), self._request(route)):
                await q.put(pcm)
            await q.put(_END)
        except Exception as exc:  # noqa: BLE001
            await q.put(exc)

    async def _passage(self, language: str, text: str, out: asyncio.Queue) -> None:
        """One foreign fragment through its routes; the reply's own voice is the last one."""
        try:
            routes = list(self.passage_routes(language))
        except Exception:  # noqa: BLE001 - a routing bug must not cost the turn
            log.exception("tts: no routes for %s", language)
            routes = []
        routes.append(Route(self.main.provider, self.main.voice, self.main.language, self.main.instructions))
        for i, route in enumerate(routes):
            last = i == len(routes) - 1
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.create_task(self._one(route, text, q))
            try:
                try:
                    first = await (q.get() if last else asyncio.wait_for(q.get(), self.first_audio_s))
                except asyncio.TimeoutError:
                    log.warning("tts passage %s: %s gave no audio in %.1f s, next route", language,
                                route.provider.name, self.first_audio_s)
                    continue
                if isinstance(first, Exception) or first is _END:
                    if last:
                        # the reply's own voice failed: as before this module, the turn's error
                        await out.put(first if isinstance(first, Exception) else _END)
                        return
                    log.warning("tts passage %s: %s failed (%s), next route", language, route.provider.name,
                                first if isinstance(first, Exception) else "no audio")
                    continue
                if i:
                    self.fallbacks += 1
                await out.put(first)
                while (item := await q.get()) is not _END:
                    if isinstance(item, Exception):
                        log.warning("tts passage %s: %s broke off (%s)", language, route.provider.name, item)
                        break  # what was heard stays heard; the reply goes on
                    await out.put(item)
                await out.put(_END)
                return
            finally:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    # --- the stream ---------------------------------------------------------------------

    async def stream(self, fragments: AsyncIterator[tuple[str, str]]) -> AsyncIterator[PCMChunk]:
        jobs: asyncio.Queue[asyncio.Queue | None] = asyncio.Queue()
        tasks: list[asyncio.Task] = []
        failure: list[BaseException] = []

        def start(coro_factory) -> None:
            out: asyncio.Queue = asyncio.Queue()
            tasks.append(asyncio.create_task(coro_factory(out)))
            jobs.put_nowait(out)

        async def split() -> None:
            run: asyncio.Queue | None = asyncio.Queue()
            first_run = run
            start(lambda out: self._main_run(first_run, out))
            try:
                async for language, text in fragments:
                    if not text:
                        continue
                    if not language or language == self.main.language:
                        if run is None:
                            run = asyncio.Queue()
                            this_run = run
                            start(lambda out, r=this_run: self._main_run(r, out))
                        run.put_nowait(text)
                    else:
                        if run is not None:
                            run.put_nowait(None)
                            run = None
                        start(lambda out, lang=language, t=text: self._passage(lang, t, out))
            except BaseException as exc:
                failure.append(exc)
                raise
            finally:
                if run is not None:
                    run.put_nowait(None)
                jobs.put_nowait(None)

        splitter = asyncio.create_task(split())
        try:
            while (out := await jobs.get()) is not None:
                while (item := await out.get()) is not _END:
                    if isinstance(item, BaseException):
                        raise item
                    yield item
            if failure and not isinstance(failure[0], asyncio.CancelledError):
                raise failure[0]
        finally:
            for t in [splitter, *tasks]:
                t.cancel()
            for t in [splitter, *tasks]:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t

