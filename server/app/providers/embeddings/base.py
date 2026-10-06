"""Embedding providers: text -> vectors, separate from the chat model (its own provider, model and size)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int


class EmbeddingProvider(ABC):
    name: str
    model: str
    dims: int

    @property
    def model_key(self) -> str:
        """The vector space: vectors are only ever compared within one model_key."""
        return f"{self.name}:{self.model}:{self.dims}"

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult: ...
