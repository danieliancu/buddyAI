"""OpenAI LLM (streaming). Params from config pass through (e.g. reasoning_effort).

Without web search: Chat Completions. With web search: Responses API + the hosted `web_search`
tool (the model decides per question whether to search); citations are stripped from the text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from app.providers.base import ProviderError
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest
from app.providers.llm.citations import CitationFilter


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str, timeout_s: float = 30.0) -> None:
        self.api_key = api_key
        self._client = AsyncOpenAI(api_key=api_key or "missing", base_url=base_url, timeout=timeout_s, max_retries=0)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        if not self.api_key:
            raise ProviderError("llm", "OpenAI API key is not configured")
        try:
            if request.web_search is not None:
                async for chunk in self._stream_responses(request):
                    yield chunk
                return
            stream = await self._client.chat.completions.create(
                model=request.model,
                messages=request.messages,
                stream=True,
                stream_options={"include_usage": True},
                max_completion_tokens=request.max_tokens,
                **request.params,
            )
            async for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    yield LLMChunk(delta=delta)
                if event.usage:
                    yield LLMChunk(
                        input_tokens=event.usage.prompt_tokens,
                        output_tokens=event.usage.completion_tokens,
                    )
        except OpenAIError as exc:
            raise ProviderError("llm", str(exc)) from exc

    async def _stream_responses(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        params: dict[str, Any] = dict(request.params)
        effort = params.pop("reasoning_effort", None)
        if effort is not None:
            params["reasoning"] = {"effort": effort}
        stream = await self._client.responses.create(
            model=request.model,
            input=request.messages,
            tools=[{"type": "web_search", **request.web_search}],
            stream=True,
            max_output_tokens=request.max_tokens,
            **params,
        )
        citations = CitationFilter()
        async for event in stream:
            if event.type == "response.output_text.delta":
                text = citations.feed(event.delta)
                if text:
                    yield LLMChunk(delta=text)
            elif event.type in ("response.completed", "response.incomplete"):
                text = citations.flush()
                if text:
                    yield LLMChunk(delta=text)
                r = event.response
                searches = sum(1 for item in r.output or [] if item.type == "web_search_call")
                yield LLMChunk(
                    input_tokens=r.usage.input_tokens if r.usage else 0,
                    output_tokens=r.usage.output_tokens if r.usage else 0,
                    web_searches=searches,
                )
            elif event.type in ("response.failed", "error"):
                err = getattr(getattr(event, "response", None), "error", None) or event
                raise ProviderError("llm", str(getattr(err, "message", err)))
