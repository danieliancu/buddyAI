"""Readable event summaries for the older /api/issues list (app/issues.py)."""

from app.db.models import DeviceIssue
from app.issues import describe


def test_reboot_summary_names_the_reset_reason_and_uptime():
    text = describe(DeviceIssue(device_id="w", kind="reboot", reason="brownout", detail={"prev_uptime_s": 95}))
    assert "brownout" in text and "1 min 35 s" in text


def test_disconnect_summary_names_the_wifi_reason():
    issue = DeviceIssue(device_id="w", kind="disconnect", reason="wifi_lost",
                        detail={"wifi_reason": 200, "offline_ms": 65000})
    assert describe(issue) == "Wi-Fi connection lost: access point signal lost (beacon timeout); back after 1 min 5 s"


def test_generic_drops_do_not_blame_the_server():
    for drop in ("ws_error", "ws_closed", "ws_disconnected"):
        text = describe(DeviceIssue(device_id="w", kind="disconnect", reason=drop, detail={}))
        assert "server" not in text.lower() and "not reported" in text


def test_new_event_kinds_use_the_diagnostics_title():
    issue = DeviceIssue(device_id="w", kind="provider_failure", reason="llm_failed", reason_code="llm_timeout", detail={})
    assert describe(issue) == "The AI model timed out"
