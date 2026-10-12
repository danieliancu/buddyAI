"""A logging filter that removes secrets from log lines: query-string values of token / code / session_id /
key / password / secret (e.g. the uvicorn access line of a password-reset link) and bearer tokens.

The message and each argument are redacted separately, so formatters that read `record.args` themselves
(uvicorn's access log) keep working.
"""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[redacted]"
_QUERY = re.compile(
    r"(?i)(?<=[?&;])((?:[a-z0-9_.-]*_)?(?:token|code|session_id|key|password|secret))=([^&#\s\"']*)"
)
_BEARER = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+")


def redact(text: str) -> str:
    if "=" not in text and "earer" not in text:
        return text
    text = _QUERY.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)


def _redact_value(value: Any) -> Any:
    return redact(value) if isinstance(value, str) else value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            args = record.args
            if isinstance(args, tuple):
                record.args = tuple(_redact_value(a) for a in args)
            elif isinstance(args, dict):
                record.args = {k: _redact_value(v) for k, v in args.items()}
            if record.args and record.name != "uvicorn.access":
                # a secret split between the format string and an argument ("Bearer %s"): the whole line
                full = record.getMessage()
                clean = redact(full)
                if clean != full:
                    record.msg, record.args = clean, None
        except Exception:  # noqa: BLE001 - a filter must never lose a log line
            pass
        return True


_FILTER = RedactingFilter()


def install() -> None:
    """Attach the filter to the root handlers and to uvicorn's loggers and handlers. Idempotent; call it
    again after uvicorn has configured its logging (its handlers are replaced then)."""
    loggers = [logging.getLogger(), *(logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access"))]
    for lg in loggers:
        if _FILTER not in lg.filters:
            lg.addFilter(_FILTER)
        for h in lg.handlers:
            if _FILTER not in h.filters:
                h.addFilter(_FILTER)
