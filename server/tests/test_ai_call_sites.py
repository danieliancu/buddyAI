"""Every place that calls an AI provider must be covered by usage admission or be a documented exception.

Scans app/ (outside app/providers/) for provider calls - `.stream(...)`, `.web_search(...)`, `.embed(...)` and STT `.start(...)` on a provider
object - and compares them with the list below. A new call site fails this test until it is admitted (a
watch turn, app/usage_ops.py) or added here with its reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

# file -> (function, why it may call a provider)
ALLOWED = {
    ("pipeline/conversation.py", "_listen"): "watch turn: admitted in device_ws before the pipeline runs (STT)",
    ("pipeline/conversation.py", "pump_llm"): "watch turn: admitted (LLM rounds)",
    ("pipeline/conversation.py", "_reply"): "watch turn: admitted (TTS of the reply)",
    ("pipeline/conversation.py", "run_search"): "watch turn: admitted (web search tool, usage via the tool outcome)",
    ("pipeline/conversation.py", "_apologise"): "watch turn: admitted (spoken apology)",
    ("pipeline/conversation.py", "_edit_llm"): "watch turn: admitted (edit-mode LLM)",
    ("api/common.py", "voice_sample"): "voice sample: free (owner decision), rate-limited, recorded non-billable",
    ("api/system.py", "_run_test"): "operator provider test: documented exception, recorded as operator_test",
    ("memory/retrieve.py", "_recall"): "watch turn: admitted (query embedding, usage on the turn)",
    ("memory/jobs.py", "run_embed"): "memory vectors: recorded as a non-billable 'memory' operation (tiny cost)",
    ("memory/extract.py", "run_extract"): "memory learning: admitted against the account (kind memory, s:memx:<job>)",
}


def _calls() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith("providers/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        stack: list[str] = []

        def visit(node: ast.AST) -> None:
            is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_fn:
                stack.append(node.name)  # type: ignore[attr-defined]
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = ast.unparse(node.func.value)
                name = node.func.attr
                provider_like = any(w in target for w in ("llm", "provider", "stt", "tts", "sel"))
                if provider_like and (name in ("stream", "web_search", "embed") or (name == "start" and "stt" in target)):
                    found.add((rel, stack[-1] if stack else "<module>"))
            for child in ast.iter_child_nodes(node):
                visit(child)
            if is_fn:
                stack.pop()

        visit(tree)
    return found


def test_every_provider_call_site_is_admitted_or_documented():
    calls = _calls()
    unknown = calls - set(ALLOWED)
    assert not unknown, f"provider calls without usage admission (add to app/usage_ops.py or ALLOWED): {unknown}"
    assert calls, "the scan found nothing - it is broken"
