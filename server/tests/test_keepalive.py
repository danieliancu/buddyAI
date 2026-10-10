"""Keep-alive timing shared with the watch (firmware protocol_client.c: JSON ping every 30 s, status every 5 min)."""

from datetime import UTC, datetime

from app.config import get_settings
from app.incidents.classify import classify_event
from app.incidents.events import from_hello
from app.issues import DROP_REASONS

WATCH_PING_INTERVAL_S = 30


def test_idle_timeout_survives_one_lost_watch_ping():
    # Only messages reset the idle timer (not WebSocket ping frames): two ping gaps must fit.
    assert get_settings().session_idle_timeout_s > 2 * WATCH_PING_INTERVAL_S


def test_pong_timeout_drop_is_a_connection_timeout():
    hello = {"link": {"drop": "pong_timeout", "offline_ms": 4000, "report_id": "1a2b"}}
    (ev,) = from_hello(hello, "dev-1", "abc", datetime.now(UTC))
    assert ev.reason == "pong_timeout"
    c = classify_event(ev.kind, ev.reason, ev.detail)
    assert (c.category, c.reason_code) == ("connection", "connection_timeout")
    assert "pong_timeout" in DROP_REASONS
