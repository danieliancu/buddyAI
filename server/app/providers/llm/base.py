"""LLMProvider interface: streaming chat completion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMChunk:
    delta: str = ""
    input_tokens: int | None = None  # set on the final chunk when the provider reports usage
    output_tokens: int | None = None
    web_searches: int = 0  # web searches the provider ran (reported with the usage)


@dataclass
class LLMRequest:
    messages: list[dict[str, str]]
    model: str
    max_tokens: int = 400
    params: dict[str, Any] = field(default_factory=dict)
    # Hosted web search tool config (e.g. {"search_context_size": "low", "user_location": {...}});
    # None = no web access. Providers without web search ignore it.
    web_search: dict[str, Any] | None = None


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]: ...
