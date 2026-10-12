"""Common provider types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ProviderError(Exception):
    """Raised by providers; `stage` is stt | llm | tts."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


# Provider errors end up in the logs, the turn record and ola Diagnostics. Provider response bodies can echo
# what was sent (the user's words), so only the exception type, the HTTP status and the provider's short
# error code/type are kept.

def _short(value: Any, limit: int = 48) -> str:
    return str(value)[:limit] if value not in (None, "") else ""


def error_summary(exc: BaseException) -> str:
    """"RateLimitError HTTP 429 insufficient_quota": type, status and error code; never the message body."""
    parts = [type(exc).__name__]
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    for attr in ("code", "type"):
        v = getattr(exc, attr, None)
        if isinstance(v, str) and v:
            parts.append(_short(v))
            break
    return " ".join(parts)


def error_dict_summary(err: Any) -> str:
    """A provider's error object ({"type": ..., "code": ..., "message": ...}) without its message."""
    if isinstance(err, dict):
        bits = [_short(err.get(k)) for k in ("type", "code")]
        return " ".join(b for b in bits if b) or "provider error"
    return "provider error"


def http_error_summary(status: int, body: bytes | str | None) -> str:
    """"HTTP 400 invalid_request_error": the status and, when the body is JSON, its error type/code."""
    kind = ""
    try:
        data = json.loads(body or b"")
        err = data.get("error", data) if isinstance(data, dict) else None
        if isinstance(err, dict):
            kind = error_dict_summary(err)
            kind = "" if kind == "provider error" else kind
    except (ValueError, TypeError):
        pass
    return f"HTTP {status}" + (f" {kind}" if kind else "")


@dataclass
class UsageItem:
    kind: str  # stt | llm | tts
    provider: str
    model: str
    unit: str  # audio_second | input_token | output_token | character
    quantity: float
