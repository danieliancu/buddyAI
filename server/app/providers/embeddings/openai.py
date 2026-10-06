"""Embeddings through an OpenAI-compatible endpoint: OpenAI itself, and DashScope's compatible mode (Qwen)."""

from __future__ import annotations

from openai import AsyncOpenAI, OpenAIError

from app.providers.base import ProviderError
from app.providers.embeddings.base import EmbeddingProvider, EmbeddingResult


class OpenAICompatibleEmbeddings(EmbeddingProvider):
    def __init__(self, name: str, api_key: str, base_url: str, model: str, dims: int, timeout_s: float = 10.0) -> None:
        self.name, self.model, self.dims = name, model, int(dims)
        self.api_key = api_key
        self._client = AsyncOpenAI(api_key=api_key or "missing", base_url=base_url, timeout=timeout_s, max_retries=0)

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not self.api_key:
            raise ProviderError("embedding", f"{self.name} API key is not configured")
        if not texts:
            return EmbeddingResult([], 0)
        try:
            res = await self._client.embeddings.create(model=self.model, input=texts, dimensions=self.dims)
        except OpenAIError as exc:
            raise ProviderError("embedding", f"{type(exc).__name__}") from exc
        vectors = [list(d.embedding) for d in sorted(res.data, key=lambda d: d.index)]
        if any(len(v) != self.dims for v in vectors):
            raise ProviderError("embedding", "unexpected vector size")
        tokens = getattr(getattr(res, "usage", None), "prompt_tokens", None) or getattr(getattr(res, "usage", None), "total_tokens", 0) or 0
        return EmbeddingResult(vectors, int(tokens))
