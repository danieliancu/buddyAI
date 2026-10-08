"""ola Diagnostics: deterministic root-cause classification (app/incidents/classify.py)."""

import itertools

import pytest

from app.incidents.classify import classify_event, classify_incident


def ev(kind, reason="", detected_by="watch", **detail):
    return {"kind": kind, "reason": reason, "detail": detail, "detected_by": detected_by}


@pytest.mark.parametrize(
    "event, expected",
    [
        (ev("reboot", "task_wdt"), ("watch", "confirmed", "watch_freeze", "firmware", "error")),
        (ev("reboot", "int_wdt"), ("watch", "confirmed", "watch_freeze", "firmware", "error")),
        (ev("reboot", "panic"), ("watch", "confirmed", "watch_crash", "firmware", "error")),
        (ev("reboot", "brownout"), ("watch", "confirmed", "watch_power_fault", "power", "error")),
        (ev("reboot", "software"), ("watch", "confirmed", "watch_restart", "firmware", "info")),
        (ev("reboot", "power_on", prev_session="ab"), ("watch", "probable", "watch_power_lost", "power", "warn")),
        (ev("reboot", "weird"), ("watch", "unknown", "watch_restart_unknown", "firmware", "warn")),
        (ev("disconnect", "wifi_lost", wifi_reason=201), ("connection", "confirmed", "wifi_lost", "wifi", "warn")),
        (ev("disconnect", "wifi_lost", rssi=-85), ("connection", "confirmed", "wifi_weak_signal", "wifi", "warn")),
        (ev("disconnect", "wifi_lost", link_basis="inferred"), ("connection", "probable", "wifi_lost", "wifi", "warn")),
        (ev("disconnect", "ws_error", ws={"tls": 0x8001}), ("connection", "confirmed", "dns_failure", "dns", "warn")),
        (ev("disconnect", "ws_error", ws={"tls": 0x801A}), ("connection", "probable", "tls_failure", "tls", "warn")),
        (ev("disconnect", "ws_error", ws={"type": 1, "errno": 104}), ("connection", "probable", "tcp_failure", "tcp", "warn")),
        (ev("disconnect", "ws_error", ws={"type": 2}), ("connection", "probable", "connection_timeout", "network", "warn")),
        (ev("disconnect", "ws_error", ws={"hs": 502}), ("server", "probable", "server_unavailable", "proxy", "error")),
        (ev("disconnect", "ws_closed", ws={"close": 1012}), ("server", "confirmed", "server_closed_service_restart", "process", "error")),
        (ev("disconnect", "ws_closed", ws={"close": 4000}), ("connection", "probable", "server_replaced", "network", "info")),
        (ev("disconnect", "ws_closed", ws={"close": 4001}), ("server", "confirmed", "server_token_revoked", "gateway", "info")),
        # 1001 "going away" comes from a proxy reload, never from the ola server: not a server fault
        (ev("disconnect", "ws_closed", ws={"close": 1001}), ("undetermined", "unknown", "closed_going_away", "proxy", "warn")),
        (ev("disconnect", "ws_error", min_heap=9000), ("watch", "probable", "watch_low_memory", "memory", "warn")),
        (ev("disconnect", "ws_error", retry_ws={"tls": 0x8001}), ("connection", "probable", "dns_failure", "dns", "warn")),
        (ev("disconnect", "reconnect"), ("watch", "confirmed", "reconnect_requested", "firmware", "info")),
        (ev("server_exception", "RuntimeError", "server", component="gateway"), ("server", "confirmed", "server_exception", "gateway", "error")),
        (ev("server_exception", "OperationalError", "server", db=True), ("server", "confirmed", "server_exception", "database", "error")),
        (ev("provider_failure", "llm_failed", "server", stage="llm", timeout=True), ("server", "confirmed", "llm_timeout", "llm", "error")),
        (ev("provider_failure", "tts_failed", "server", stage="tts"), ("server", "confirmed", "tts_failure", "tts", "error")),
        (ev("server_shutdown", "", "server"), ("server", "confirmed", "server_shutdown", "process", "warn")),
        (ev("ws_close", "1012", "server", code=1012), ("server", "confirmed", "server_shutdown", "process", "warn")),
        (ev("lease_lost", "", "server"), ("server", "probable", "usage_lease_lost", "database", "error")),
    ],
)
def test_event_rules(event, expected):
    c = classify_event(event["kind"], event["reason"], event["detail"], event["detected_by"])
    assert (c.category, c.confidence, c.reason_code, c.component, c.severity) == expected


@pytest.mark.parametrize("drop", ["ws_error", "ws_closed", "ws_disconnected", "connect_failed", "hello_timeout"])
def test_generic_websocket_drops_are_never_blamed_on_the_server(drop):
    c = classify_event("disconnect", drop, {"mid_turn": True})
    assert c.category == "undetermined" and c.confidence == "unknown"


@pytest.mark.parametrize("code", [None, 1006, 1005, 1000])
def test_server_side_view_of_a_drop_is_undetermined(code):
    assert classify_event("ws_close", str(code), {"code": code}).category == "undetermined"
    assert classify_event("server_timeout", "", {"timeout_s": 45}).category == "undetermined"


def test_interrupted_turn_is_never_the_cause():
    alone = classify_incident([ev("turn_interrupted", "", "server")])
    assert alone.category == "undetermined" and alone.reason_code == "turn_interrupted"
    with_cause = classify_incident([ev("turn_interrupted", "", "server"), ev("ws_close", "1006", "server", code=1006)])
    assert with_cause.reason_code == "connection_closed_unknown"


def test_wifi_report_explains_the_servers_undetermined_view():
    events = [ev("turn_interrupted", "", "server"), ev("ws_close", "1006", "server", code=1006),
              ev("disconnect", "wifi_lost", wifi_reason=200, mid_turn=True)]
    c = classify_incident(events)
    assert (c.category, c.confidence, c.reason_code, c.detected_by) == ("connection", "confirmed", "wifi_lost", "watch")
    assert events[c.primary_index]["kind"] == "disconnect"


def test_watchdog_restart_beats_the_generic_drop_it_caused():
    c = classify_incident([ev("disconnect", "reboot", prev_session="ab"), ev("reboot", "task_wdt", prev_session="ab"),
                           ev("server_timeout", "", "server")])
    assert (c.category, c.reason_code, c.severity) == ("watch", "watch_freeze", "error")


def test_server_shutdown_explains_the_watch_report():
    c = classify_incident([ev("disconnect", "ws_closed"), ev("server_shutdown", "", "server")])
    assert (c.category, c.confidence, c.reason_code) == ("server", "confirmed", "server_shutdown")


def test_classification_does_not_depend_on_arrival_order():
    events = [ev("turn_interrupted", "", "server"), ev("ws_close", "1006", "server", code=1006),
              ev("disconnect", "ws_error", ws={"type": 2}), ev("server_timeout", "", "server")]
    results = {
        (c.category, c.confidence, c.reason_code, c.severity, perm[c.primary_index]["kind"])
        for perm in itertools.permutations(events)
        for c in [classify_incident(list(perm))]
    }
    assert results == {("connection", "probable", "connection_timeout", "warn", "disconnect")}


def test_secondary_events_do_not_raise_severity_to_error():
    assert classify_incident([ev("turn_interrupted", "", "server")]).severity == "warn"


def test_routine_restart_that_closed_the_session_stays_info():
    c = classify_incident([ev("reboot", "software", prev_session="ab"), ev("disconnect", "reboot", prev_session="ab"),
                           ev("ws_close", "1006", "server", code=1006), ev("turn_interrupted", "", "server")])
    assert (c.reason_code, c.severity) == ("watch_restart", "info")


def test_a_second_determined_cause_still_raises_severity():
    c = classify_incident([ev("disconnect", "wifi_lost"), ev("server_exception", "RuntimeError", "server")])
    assert c.severity == "error"


def test_undetermined_drop_alone_is_a_warning():
    assert classify_incident([ev("ws_close", "1006", "server", code=1006)]).severity == "warn"


def test_empty_incident_is_undetermined():
    assert classify_incident([]).category == "undetermined"


def test_every_reason_code_has_an_explanation():
    from app.incidents import texts

    assert texts.reason("embedding_timeout")[0] == "Memory search timed out"
    assert texts.reason("vision_failure") == texts.REASONS["provider_failure"]
    assert texts.reason("vision_timeout") == texts.REASONS["provider_timeout"]
    for code in ("closed_going_away", "server_replaced", "watch_freeze", "dns_failure", "watch_silent"):
        assert code in texts.REASONS
