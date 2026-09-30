"""Qwen via Model Studio OpenAI-compatible endpoint (streaming)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI, OpenAIError

from app.providers.base import ProviderError
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest, ToolCallAccumulator, chat_tools


class QwenLLM(LLMProvider):
    name = "qwen"

    def __init__(self, api_key: str, base_url: str, timeout_s: float = 30.0) -> None:
        self.api_key = api_key
        self._client = AsyncOpenAI(api_key=api_key or "missing", base_url=base_url, timeout=timeout_s, max_retries=0)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        if not self.api_key:
            raise ProviderError("llm", "DashScope API key is not configured")
        params = dict(request.params)
        extra_body = {}
        if "enable_thinking" in params:
            extra_body["enable_thinking"] = params.pop("enable_thinking")
        tools = chat_tools(request.tools)
        if tools:
            params["tools"] = tools
        try:
            stream = await self._client.chat.completions.create(
                model=request.model,
                messages=request.messages,
                stream=True,
                stream_options={"include_usage": True},
                max_tokens=request.max_tokens,
                extra_body=extra_body or None,
                **params,
            )
            calls = ToolCallAccumulator()
            async for event in stream:
                delta = ""
                if event.choices:
                    delta = event.choices[0].delta.content or ""
                    calls.feed(getattr(event.choices[0].delta, "tool_calls", None))
                if delta:
                    yield LLMChunk(delta=delta)
                if event.usage:
                    yield LLMChunk(
                        input_tokens=event.usage.prompt_tokens,
                        output_tokens=event.usage.completion_tokens,
                    )
            tool_calls = calls.result()
            if tool_calls:
                yield LLMChunk(tool_calls=tool_calls)
        except OpenAIError as exc:
            raise ProviderError("llm", str(exc)) from exc
