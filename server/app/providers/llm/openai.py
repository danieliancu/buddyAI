"""OpenAI Chat Completions (streaming). Params from config pass through (e.g. reasoning_effort)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI, OpenAIError

from app.providers.base import ProviderError
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str, timeout_s: float = 30.0) -> None:
        self.api_key = api_key
        self._client = AsyncOpenAI(api_key=api_key or "missing", base_url=base_url, timeout=timeout_s, max_retries=0)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        if not self.api_key:
            raise ProviderError("llm", "OpenAI API key is not configured")
        try:
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
