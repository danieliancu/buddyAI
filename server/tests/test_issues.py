"""Watch issue classification (app/issues.py)."""

from app.issues import describe, from_hello


def test_plain_hello_has_no_issues():
    assert from_hello({"device_id": "w", "token": "t"}) == []


def test_reset_reasons_are_classified():
    sev = {i.reason: i.severity for r in ("brownout", "panic", "power_on", "weird") for i in from_hello({"boot": {"reset_reason": r}})}
    assert sev == {"brownout": "error", "panic": "error", "power_on": "info", "weird": "warn"}


def test_disconnect_summary_names_the_wifi_reason():
    (issue,) = from_hello({"link": {"drop": "wifi_lost", "wifi_reason": 200, "offline_ms": 65000, "rssi": "x"}})
    assert issue.kind == "disconnect" and "rssi" not in issue.detail
    assert describe(issue) == "Wi-Fi connection lost: access point signal lost (beacon timeout); back after 1 min 5 s"
