"""OpenAI calls are made with store=False (no stored completions/responses on OpenAI's side), whatever the
configured params say; the Qwen provider (OpenAI-compatible endpoint) never gets the parameter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.providers.llm.base import LLMRequest
from app.providers.llm.openai import OpenAILLM
from app.providers.llm.qwen import QwenLLM


class _EmptyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

        async def chat_create(**kw):
            self.calls.append(("chat", kw))
            return _EmptyStream()

        async def responses_create(**kw):
            self.calls.append(("responses", kw))
            if kw.get("stream"):
                return _EmptyStream()
            return SimpleNamespace(output_text="It is sunny.", output=[], status="completed",
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5))

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=chat_create))
        self.responses = SimpleNamespace(create=responses_create)


def _openai() -> tuple[OpenAILLM, _FakeClient]:
    llm = OpenAILLM("sk-test", "https://api.openai.invalid/v1")
    fake = _FakeClient()
    llm._client = fake  # type: ignore[assignment]
    return llm, fake


async def test_chat_completions_store_false_even_if_params_say_otherwise():
    llm, fake = _openai()
    req = LLMRequest(messages=[{"role": "user", "content": "hi"}], model="gpt-test", params={"store": True})
    async for _ in llm.stream(req):
        pass
    kind, kw = fake.calls[-1]
    assert kind == "chat" and kw["store"] is False


async def test_streaming_responses_store_false():
    llm, fake = _openai()
    req = LLMRequest(messages=[{"role": "user", "content": "hi"}], model="gpt-test",
                     web_search={"search_context_size": "low"}, params={"store": True, "reasoning_effort": "low"})
    async for _ in llm.stream(req):
        pass
    kind, kw = fake.calls[-1]
    assert kind == "responses" and kw["stream"] is True and kw["store"] is False


async def test_web_search_responses_store_false():
    llm, fake = _openai()
    out = await llm.web_search("weather in London", "London", "en", model="gpt-test", tool={},
                               params={"store": True}, instructions="answer briefly")
    assert out[0] == "It is sunny."
    kind, kw = fake.calls[-1]
    assert kind == "responses" and kw["store"] is False


async def test_qwen_gets_no_store_parameter():
    llm = QwenLLM("dash-test", "https://dashscope.invalid/v1")
    fake = _FakeClient()
    llm._client = fake  # type: ignore[assignment]
    async for _ in llm.stream(LLMRequest(messages=[{"role": "user", "content": "hi"}], model="qwen-test")):
        pass
    kind, kw = fake.calls[-1]
    assert kind == "chat" and "store" not in kw
