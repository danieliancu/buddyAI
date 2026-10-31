"""The process-wide SearchOrchestrator for the current settings (rebuilt when a setting or the Brave key
changes, e.g. from the web UI), and the answering call on the profile's default chat model."""

from __future__ import annotations

from app.config import Settings
from app.providers.llm.base import LLMProvider, LLMRequest
from app.websearch.extract import ContentExtractor
from app.websearch.fetch import PageFetcher
from app.websearch.orchestrator import AnswerFn, SearchConfig, SearchOrchestrator
from app.websearch.providers import BraveSearchProvider, CircuitBreaker, DDGSSearchProvider
from app.websearch.weather import WeatherSource

_current: tuple[tuple, SearchOrchestrator] | None = None


def get_orchestrator(s: Settings) -> SearchOrchestrator:
    global _current
    config = SearchConfig.from_settings(s)
    key = (config, s.provider_key("brave_api_key"), s.ddgs_region, s.ddgs_backends, s.search_max_concurrency,
           s.extraction_max_bytes, s.ddgs_circuit_breaker_threshold, s.ddgs_circuit_breaker_cooldown_s, s.weather_source)
    if _current is None or _current[0] != key:
        fetcher = PageFetcher(max_bytes=s.extraction_max_bytes, max_concurrency=s.search_max_concurrency)
        _current = (key, SearchOrchestrator(
            DDGSSearchProvider(s.ddgs_region, s.search_max_concurrency, s.ddgs_backends),
            BraveSearchProvider(s.provider_key("brave_api_key")),
            ContentExtractor(fetcher, s.extraction_timeout_s),
            config,
            CircuitBreaker(s.ddgs_circuit_breaker_threshold, s.ddgs_circuit_breaker_cooldown_s),
            weather=WeatherSource() if s.weather_source == "metno" else None,
        ))
    return _current[1]


def answer_fn(llm: LLMProvider, model: str, params: dict) -> AnswerFn:
    """One short, tool-less, history-less completion: (system, user) -> (text, input, cached input, output)."""

    async def run(system: str, user: str) -> tuple[str, int, int, int]:
        request = LLMRequest(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                             model=model, max_tokens=200, params=dict(params))
        parts: list[str] = []
        tin = cached = tout = 0
        async for chunk in llm.stream(request):
            if chunk.delta:
                parts.append(chunk.delta)
            if chunk.input_tokens is not None:
                tin += chunk.input_tokens
                cached += min(chunk.cached_input_tokens, chunk.input_tokens)
                tout += chunk.output_tokens or 0
        return "".join(parts), tin, cached, tout

    return run
