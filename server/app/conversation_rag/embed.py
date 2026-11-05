"""The embedding provider of Conversation RAG: its own setting, never the AI profile's.

Only OpenAI is allowed for conversation text (a Qwen profile does not send it to DashScope). Without its key
there is no provider: indexing and search stay off, and nothing falls back to another provider.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings

ALLOWED = ("openai",)
_cache: dict[tuple, Any] = {}


def provider() -> Any | None:
    s = get_settings()
    if s.conversation_rag_embed_provider not in ALLOWED:
        return None
    dims = int(s.conversation_rag_embed_dims)
    if s.mock_providers:
        from app.providers.mock import MockEmbeddings

        key = ("mock", min(dims, 64))
        return _cache.setdefault(key, MockEmbeddings(min(dims, 64)))
    api_key = s.provider_key("openai_api_key")
    if not api_key:
        return None
    from app.providers.embeddings.openai import OpenAICompatibleEmbeddings

    key = ("openai", s.conversation_rag_embed_model, dims, api_key, s.openai_base_url)
    if key not in _cache:
        _cache[key] = OpenAICompatibleEmbeddings("openai_embed", api_key, s.openai_base_url,
                                                 s.conversation_rag_embed_model, dims)
    return _cache[key]
