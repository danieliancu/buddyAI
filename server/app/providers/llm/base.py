"""LLMProvider interface: streaming chat completion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text as produced by the model


@dataclass
class LLMChunk:
    delta: str = ""
    input_tokens: int | None = None  # set on the final chunk when the provider reports usage
    output_tokens: int | None = None
    web_searches: int = 0  # web searches the provider ran (reported with the usage)
    tool_calls: list[ToolCall] | None = None  # function calls the model wants run (emitted once, at the end)


@dataclass
class LLMRequest:
    # Chat Completions shape; may include assistant `tool_calls` and `role: tool` results.
    messages: list[dict[str, Any]]
    model: str
    max_tokens: int = 400
    params: dict[str, Any] = field(default_factory=dict)
    # Hosted web search tool config (e.g. {"search_context_size": "low", "user_location": {...}});
    # None = no web access. Providers without web search ignore it.
    web_search: dict[str, Any] | None = None
    # Function tools: [{"name", "description", "parameters": <JSON schema>}]. None = no tools.
    tools: list[dict[str, Any]] | None = None


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]: ...


def chat_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Tool definitions in Chat Completions format."""
    if not tools:
        return None
    return [{"type": "function", "function": t} for t in tools]


class ToolCallAccumulator:
    """Joins streamed Chat Completions tool-call deltas (split across events, keyed by index)."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}

    def feed(self, deltas: Any) -> None:
        for d in deltas or []:
            c = self._calls.setdefault(d.index, {"id": "", "name": "", "arguments": ""})
            if d.id:
                c["id"] = d.id
            fn = d.function
            if fn is not None:
                if fn.name:
                    c["name"] += fn.name
                if fn.arguments:
                    c["arguments"] += fn.arguments

    def result(self) -> list[ToolCall] | None:
        calls = [
            ToolCall(c["id"] or f"call_{i}", c["name"], c["arguments"] or "{}")
            for i, c in sorted(self._calls.items())
        ]
        return [c for c in calls if c.name] or None
