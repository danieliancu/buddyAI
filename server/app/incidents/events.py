"""ola Diagnostics: diagnostic events, built from a watch hello (validated) or by the server itself.

Correlation (never by time proximity alone):
- one connection = one incident, keyed by the server's session id: "sess:{device}:{session}". The server's own
  events carry it; firmware 0.1.1+ names the session that dropped ("prev_session"); for firmware 0.1.0 it is
  the device's last session id stored at its previous hello (link_basis "inferred", confidence capped);
- a failed turn without a disconnect: "turn:{device}:{session}:{turn}";
- a restart not tied to a session: "boot:{device}:{dedup}"; a report with no session at all: "link:{device}:{dedup}".
Duplicates (the watch repeats its report until hello_ack) are dropped by dedup_key: the watch's report_id
(0.1.1+), or for 0.1.0 a fingerprint of the report's fixed fields, matched within a short time window.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

DROP_REASONS = {"wifi_lost", "ws_error", "ws_closed", "ws_disconnected", "hello_timeout", "reconnect", "reboot",
                "connect_failed"}
_TOKEN = re.compile(r"[a-z0-9_]{1,32}")
_HEX = re.compile(r"[0-9a-fA-F]{1,64}")
_WS_FIELDS = {"type": (0, 16), "tls": (0, 0xFFFF), "tls_stack": (-0x7FFFFFFF, 0x7FFFFFFF), "errno": (0, 1000),
              "hs": (0, 999), "close": (0, 4999)}


@dataclass
class Event:
    kind: str
    reason: str
    detected_by: str  # watch | server
    correlation_key: str
    occurred_at: datetime
    session_id: str | None = None
    turn_id: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    dedup_key: str | None = None
    legacy_window_s: int = 0  # >0: firmware 0.1.0 fingerprint, also matched against nearby earlier reports
    recovered: bool = False  # reported from a working new session: the incident has already recovered


def _int(v: Any, lo: int, hi: int) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    n = int(v)
    return n if lo <= n <= hi else None


def _token(v: Any) -> str | None:
    return v if isinstance(v, str) and _TOKEN.fullmatch(v) else None


def _hex(v: Any) -> str | None:
    return v.lower() if isinstance(v, str) and _HEX.fullmatch(v) else None


def _report_id(v: Any) -> str | None:
    if isinstance(v, str) and _HEX.fullmatch(v) and len(v) <= 16:
        return v.lower()
    n = _int(v, 1, 0xFFFFFFFF)
    return f"{n:08x}" if n is not None else None


def _ws(v: Any) -> dict[str, int]:
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, (lo, hi) in _WS_FIELDS.items():
        n = _int(v.get(k), lo, hi)
        if n:
            out[k] = n
    return out


def _fp(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def from_hello(msg: dict[str, Any], device_id: str, last_session: str | None, now: datetime) -> list[Event]:
    """The "boot" / "link" reports of a token hello (PROTOCOL.md section 3.1). Anything malformed is
    dropped field by field; the hello itself is never refused because of its reports."""
    out: list[Event] = []
    try:
        boot = msg.get("boot")
        if isinstance(boot, dict):
            out.append(_boot_event(boot, device_id, now))
        link = msg.get("link")
        if isinstance(link, dict):
            out.append(_link_event(link, device_id, last_session, now))
    except Exception:  # noqa: BLE001 - a bad report must not break the hello
        log.warning("diagnostics: malformed report in hello from %s ignored", device_id, exc_info=True)
    return out


def _boot_event(boot: dict[str, Any], device_id: str, now: datetime) -> Event:
    reason = _token(boot.get("reset_reason")) or "unknown"
    detail: dict[str, Any] = {}
    for k, hi in (("prev_uptime_s", 10**9), ("prev_min_heap", 10**8), ("uptime_s", 10**7)):
        n = _int(boot.get(k), 0, hi)
        if n is not None:
            detail[k] = n
    prev_session = _hex(boot.get("prev_session"))
    if prev_session:
        detail["prev_session"] = prev_session
    rid = _report_id(boot.get("report_id"))
    occurred = now - timedelta(seconds=detail.get("uptime_s", 0))
    if rid:
        dedup, window = f"w:{rid}:boot", 0
    else:  # firmware 0.1.0
        dedup, window = f"l:{_fp('boot', reason, detail.get('prev_uptime_s'))}", 600
    key = f"sess:{device_id}:{prev_session}" if prev_session else f"boot:{device_id}:{dedup}"
    return Event("reboot", reason, "watch", key, occurred, session_id=prev_session, detail=detail,
                 dedup_key=dedup, legacy_window_s=window, recovered=True)


def _link_event(link: dict[str, Any], device_id: str, last_session: str | None, now: datetime) -> Event:
    drop = link.get("drop")
    reason = drop if isinstance(drop, str) and drop in DROP_REASONS else (_token(drop) or "unknown")
    detail: dict[str, Any] = {"mid_turn": link.get("mid_turn") is True}
    for k, lo, hi in (("offline_ms", 0, 10**10), ("wifi_reason", 0, 1000), ("rssi", -127, -1), ("session_s", 0, 10**8),
                      ("uptime_s", 0, 10**9), ("heap", 0, 10**8), ("min_heap", 0, 10**8), ("fails", 0, 10**6),
                      ("turn_id", 1, 0xFFFFFFFF)):
        n = _int(link.get(k), lo, hi)
        if n is not None:
            detail[k] = n
    for k in ("ws", "retry_ws"):
        ws = _ws(link.get(k))
        if ws:
            detail[k] = ws
    reported = _hex(link.get("prev_session"))
    session = reported or (last_session if reason != "connect_failed" else None)
    if session:
        detail["link_basis"] = "reported" if reported else "inferred"
    occurred = now - timedelta(milliseconds=detail.get("offline_ms", 0))
    rid = _report_id(link.get("report_id"))
    if rid:
        dedup, window = f"w:{rid}:link", 0
    else:  # firmware 0.1.0: offline_ms grows with each resend, so it is not part of the fingerprint
        dedup = f"l:{_fp('link', reason, detail.get('session_s'), detail.get('wifi_reason'), detail.get('rssi'), detail['mid_turn'])}"
        window = 120
    key = f"sess:{device_id}:{session}" if session else f"link:{device_id}:{dedup}"
    return Event("disconnect", reason, "watch", key, occurred, session_id=session,
                 turn_id=detail.get("turn_id"), detail=detail, dedup_key=dedup, legacy_window_s=window, recovered=True)


def server_event(kind: str, device_id: str, session_id: str | None, now: datetime, *, turn_id: int | None = None,
                 reason: str = "", detail: dict[str, Any] | None = None, per_turn: bool = False) -> Event:
    """An event the server saw itself. per_turn: an incident of its own for this turn (no disconnect)."""
    sess = session_id or "none"
    key = f"turn:{device_id}:{sess}:{turn_id}" if per_turn else f"sess:{device_id}:{sess}"
    dedup = f"s:{sess}:{kind}:{turn_id if turn_id is not None else ''}"
    return Event(kind, reason[:64], "server", key, now, session_id=session_id, turn_id=turn_id,
                 detail=dict(detail or {}), dedup_key=dedup[:96])
