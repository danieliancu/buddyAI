"""ola Diagnostics: events built from a watch hello - validation, correlation keys, dedup keys."""

from datetime import datetime, timedelta, timezone

from app.incidents.events import from_hello, server_event

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def test_plain_hello_has_no_events():
    assert from_hello({"device_id": "w", "token": "t"}, "w", "s0", NOW) == []


def test_firmware_011_link_report_names_its_session_and_report_id():
    (e,) = from_hello({"link": {"drop": "ws_error", "offline_ms": 4200, "mid_turn": True, "prev_session": "AB12",
                                "turn_id": 7, "report_id": "1a2b3c4d", "min_heap": 41000,
                                "ws": {"type": 1, "tls": 0x8001, "errno": 104, "junk": 5}}}, "w", "other", NOW)
    assert e.correlation_key == "sess:w:ab12" and e.session_id == "ab12" and e.turn_id == 7
    assert e.dedup_key == "w:1a2b3c4d:link" and e.legacy_window_s == 0
    assert e.detail["link_basis"] == "reported" and e.detail["ws"] == {"type": 1, "tls": 0x8001, "errno": 104}
    assert e.occurred_at == NOW - timedelta(milliseconds=4200) and e.recovered


def test_firmware_010_link_report_is_inferred_from_the_last_session():
    (e,) = from_hello({"link": {"drop": "wifi_lost", "wifi_reason": 201, "offline_ms": 1000, "session_s": 30}},
                      "w", "prev-sess", NOW)
    assert e.correlation_key == "sess:w:prev-sess" and e.detail["link_basis"] == "inferred"
    assert e.dedup_key.startswith("l:") and e.legacy_window_s > 0
    # the same report resent later (offline_ms grew) has the same fingerprint
    (again,) = from_hello({"link": {"drop": "wifi_lost", "wifi_reason": 201, "offline_ms": 9000, "session_s": 30}},
                          "w", "newer-sess", NOW + timedelta(seconds=8))
    assert again.dedup_key == e.dedup_key and abs((again.occurred_at - e.occurred_at).total_seconds()) < 1


def test_boot_with_prev_session_joins_that_session():
    (b,) = from_hello({"boot": {"reset_reason": "task_wdt", "prev_session": "cafe", "report_id": 99}}, "w", None, NOW)
    assert b.correlation_key == "sess:w:cafe" and b.dedup_key == "w:00000063:boot"


def test_boot_without_session_is_its_own_incident():
    (b,) = from_hello({"boot": {"reset_reason": "power_on"}}, "w", "s0", NOW)
    assert b.correlation_key.startswith("boot:w:l:") and b.session_id is None


def test_malformed_reports_are_cleaned_field_by_field():
    msg = {
        "boot": {"reset_reason": "PANIC; DROP TABLE", "prev_uptime_s": "95", "prev_session": "../x"},
        "link": {"drop": {"x": 1}, "offline_ms": 1e300, "rssi": 40, "wifi_reason": True, "mid_turn": "yes",
                 "prev_session": "zz-not-hex", "turn_id": -1, "ws": "nope", "report_id": "not hex!"},
    }
    boot, link = from_hello(msg, "w", None, NOW)
    assert boot.reason == "unknown" and boot.detail == {}
    assert link.reason == "unknown" and link.detail == {"mid_turn": False}
    assert link.correlation_key.startswith("link:w:")  # no session known, none invented


def test_reports_of_the_wrong_type_are_ignored():
    assert from_hello({"boot": "x", "link": [1, 2]}, "w", None, NOW) == []


def test_connect_failure_without_session_is_not_attached_to_the_last_one():
    (e,) = from_hello({"link": {"drop": "connect_failed", "fails": 4, "ws": {"tls": 0x8001}}}, "w", "old", NOW)
    assert e.session_id is None and e.correlation_key.startswith("link:w:")


def test_server_events_have_stable_dedup_keys():
    a = server_event("turn_interrupted", "w", "s1", NOW, turn_id=3)
    b = server_event("turn_interrupted", "w", "s1", NOW + timedelta(seconds=5), turn_id=3)
    assert a.dedup_key == b.dedup_key and a.correlation_key == "sess:w:s1"
    t = server_event("provider_failure", "w", "s1", NOW, turn_id=3, per_turn=True)
    assert t.correlation_key == "turn:w:s1:3"
