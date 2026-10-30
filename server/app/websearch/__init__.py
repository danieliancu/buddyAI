"""Our own web search (setting search_backend = "ddgs_brave"): DDGS first, the Brave Search API as fallback,
Trafilatura for page text, one small LLM call that answers from the evidence. See orchestrator.py.

The legacy backend (OpenAI hosted web search) stays in app/providers/llm/openai.py; app/search.py (cache,
scope, freshness) is shared by both.
"""
