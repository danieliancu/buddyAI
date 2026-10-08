"""ola Diagnostics: storing, grouping, dedup, recovery, retry, retention and the admin API (app/incidents)."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import DeviceIssue, DiagIncident, utcnow
from app.db.repositories import DeviceRepo, IncidentRepo
from app.db.session import session_scope
from app.incidents import service
from app.incidents.events import from_hello, server_event
from app.main import app


def _device(last_session: str | None = None) -> str:
    dev = f"diag-{uuid.uuid4().hex[:10]}"
    with session_scope() as db:
        DeviceRepo(db).pair(dev, f"hash-{dev}", "Kitchen watch", "t", "0.1.1")
        if last_session:
            DeviceRepo(db).touch(dev, last_session_id=last_session)
    return dev


def _incidents(dev: str) -> list[DiagIncident]:
    with session_scope() as db:
        return list(IncidentRepo(db).page(device_id=dev, limit=100))


def _events(inc_id: int) -> list[DeviceIssue]:
    with session_scope() as db:
        return list(IncidentRepo(db).events(inc_id))


@pytest.fixture
def admin_client():
    """An operator session; the app (and its background loops) is shut down after the test."""
    with TestClient(app) as c:
        if c.get("/api/auth/status").json()["needs_setup"]:
            c.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
        else:
            c.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        yield c


def test_wifi_drop_during_a_conversation_is_one_incident():
    dev, sess = _device(), uuid.uuid4().hex
    now = utcnow()
    service.record(dev, None, "0.1.1", [server_event("turn_interrupted", dev, sess, now, turn_id=4),
                                        server_event("ws_close", dev, sess, now, reason="1006",
                                                     detail={"code": 1006, "mid_turn": True})])
    (inc,) = _incidents(dev)
    assert (inc.category, inc.confidence) == ("undetermined", "unknown")  # the server alone cannot tell
    assert inc.recovered_at is None
    # the watch reconnects and reports the Wi-Fi loss for that session
    report = from_hello({"link": {"drop": "wifi_lost", "wifi_reason": 200, "rssi": -71, "mid_turn": True,
                                  "offline_ms": 6000, "prev_session": sess, "turn_id": 4, "report_id": "abc1"}},
                        dev, None, now + timedelta(seconds=6))
    service.record(dev, None, "0.1.1", report)
    (inc,) = _incidents(dev)
    assert (inc.category, inc.confidence, inc.reason_code, inc.suspected_component) == (
        "connection", "confirmed", "wifi_lost", "wifi")
    assert inc.event_count == 3 and inc.turn_id == 4 and inc.session_id == sess
    assert inc.recovered_at is not None
    evs = _events(inc.id)
    primary = next(e for e in evs if e.id == inc.primary_event_id)
    assert primary.kind == "disconnect" and primary.detected_by == "watch"
    # the drop happened before the server noticed: the incident starts at the watch's drop time
    assert abs((inc.occurred_at - (now + timedelta(seconds=6) - timedelta(milliseconds=6000))).total_seconds()) < 1


def test_generic_ws_error_stays_undetermined():
    dev, sess = _device(), uuid.uuid4().hex
    service.record(dev, None, "0.1.1", from_hello({"link": {"drop": "ws_error", "prev_session": sess,
                                                            "report_id": "77"}}, dev, None, utcnow()))
    (inc,) = _incidents(dev)
    assert (inc.category, inc.confidence, inc.reason_code) == ("undetermined", "unknown", "link_lost_unknown")


def test_duplicate_reports_are_stored_once():
    dev, sess = _device(), uuid.uuid4().hex
    msg = {"boot": {"reset_reason": "panic", "prev_uptime_s": 3600, "report_id": "b007", "prev_session": sess},
           "link": {"drop": "reboot", "prev_session": sess, "report_id": "11"}}
    for _ in range(3):  # resent until hello_ack
        service.record(dev, None, "0.1.1", from_hello(msg, dev, None, utcnow()))
    (inc,) = _incidents(dev)
    assert inc.event_count == 2 and inc.reason_code == "watch_crash"


def test_legacy_010_report_resent_after_a_lost_ack_is_not_attached_to_the_new_session():
    dev = _device()
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    link = {"drop": "wifi_lost", "wifi_reason": 201, "session_s": 300, "offline_ms": 3000}
    now = utcnow()
    service.record(dev, None, "0.1.0", from_hello({"link": link}, dev, first, now))
    # the ack was lost; the next hello repeats the report, and the server's "last session" has moved on
    service.record(dev, None, "0.1.0", from_hello({"link": {**link, "offline_ms": 9000}}, dev, second,
                                                   now + timedelta(seconds=6)))
    (inc,) = _incidents(dev)
    assert inc.session_id == first and inc.event_count == 1
    assert (inc.category, inc.confidence) == ("connection", "probable")  # inferred link: never "confirmed"


def test_events_arriving_out_of_order_give_the_same_incident():
    def run(order):
        dev, sess = _device(), uuid.uuid4().hex
        now = utcnow()
        evs = {
            "watch": from_hello({"link": {"drop": "ws_error", "prev_session": sess, "report_id": "5",
                                          "ws": {"type": 2}}}, dev, None, now),
            "turn": [server_event("turn_interrupted", dev, sess, now, turn_id=2)],
            "silent": [server_event("server_timeout", dev, sess, now, detail={"timeout_s": 45})],
        }
        for k in order:
            service.record(dev, None, "0.1.1", evs[k])
        (inc,) = _incidents(dev)
        return inc.category, inc.confidence, inc.reason_code, inc.event_count

    a = run(["watch", "turn", "silent"])
    assert a == run(["silent", "turn", "watch"]) == run(["turn", "watch", "silent"])
    assert a == ("connection", "probable", "connection_timeout", 3)


def test_unrelated_events_close_in_time_are_not_grouped():
    dev = _device()
    now = utcnow()
    s1, s2 = uuid.uuid4().hex, uuid.uuid4().hex
    service.record(dev, None, "t", [server_event("ws_close", dev, s1, now, detail={"code": 1006})])
    service.record(dev, None, "t", [server_event("provider_failure", dev, s2, now, turn_id=1, per_turn=True,
                                                 detail={"stage": "llm", "timeout": True})])
    incs = _incidents(dev)
    assert len(incs) == 2 and {i.category for i in incs} == {"undetermined", "server"}


def test_ai_provider_timeout_and_recovery_after_a_completed_turn():
    dev, sess = _device(), uuid.uuid4().hex
    service.record(dev, None, "t", [server_event("provider_failure", dev, sess, utcnow(), turn_id=3, per_turn=True,
                                                 reason="llm_failed", detail={"stage": "llm", "timeout": True})])
    (inc,) = _incidents(dev)
    assert (inc.category, inc.reason_code, inc.severity, inc.recovered_at) == ("server", "llm_timeout", "error", None)
    service.turn_succeeded(dev)
    (inc,) = _incidents(dev)
    assert inc.recovered_at is not None


def test_new_session_recovers_earlier_incidents_but_not_its_own():
    dev, old, new = _device(), uuid.uuid4().hex, uuid.uuid4().hex
    service.record(dev, None, "t", [server_event("server_timeout", dev, old, utcnow())])
    service.session_started(dev, new)
    (inc,) = _incidents(dev)
    assert inc.recovered_at is not None


def test_server_side_event_arriving_after_the_watch_reconnected_is_already_recovered():
    old, new = uuid.uuid4().hex, uuid.uuid4().hex
    dev = _device(last_session=new)  # the watch's newer hello was handled first
    service.record(dev, None, "t", [server_event("ws_close", dev, old, utcnow(), detail={"code": 1006})])
    (inc,) = _incidents(dev)
    assert inc.recovered_at is not None


def test_recovery_mark_is_retried_when_storage_fails(monkeypatch):
    dev, old, new = _device(), uuid.uuid4().hex, uuid.uuid4().hex
    service.record(dev, None, "t", [server_event("server_timeout", dev, old, utcnow())])
    real = service.session_started

    def down(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "session_started", down)
    asyncio.run(service.record_now(None, dev, None, "t", [], started_session=new))
    assert _incidents(dev)[0].recovered_at is None and service.pending() >= 1
    monkeypatch.setattr(service, "session_started", real)
    service.flush_retry()
    assert _incidents(dev)[0].recovered_at is not None


def test_storage_failure_queues_events_and_the_retry_stores_them(monkeypatch):
    dev, sess = _device(), uuid.uuid4().hex
    real = service.record

    def down(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "record", down)
    asyncio.run(service.record_now(None, dev, None, "t", [server_event("server_timeout", dev, sess, utcnow())]))
    assert service.pending() >= 1 and _incidents(dev) == []
    with pytest.raises(RuntimeError):
        service.flush_retry()  # still down: kept for later
    assert service.pending() >= 1
    monkeypatch.setattr(service, "record", real)
    service.flush_retry()
    assert service.pending() == 0
    (inc,) = _incidents(dev)
    assert inc.reason_code == "watch_silent"
    service.flush_retry()  # nothing left; storing twice would not duplicate anyway
    assert _incidents(dev)[0].event_count == 1


def test_retry_queue_is_bounded(monkeypatch):
    monkeypatch.setattr(service, "_retry", service.deque(maxlen=3))
    monkeypatch.setattr(service, "RETRY_MAX", 3)
    for _ in range(5):
        service._enqueue("record", "x", None, "t", [])
    assert service.pending() == 3


def test_retention_removes_old_incidents_and_their_events(monkeypatch):
    dev = _device()
    service.record(dev, None, "t", [server_event("server_timeout", dev, uuid.uuid4().hex, utcnow())])
    (inc,) = _incidents(dev)
    with session_scope() as db:
        row = db.get(DiagIncident, inc.id)
        row.updated_at = utcnow() - timedelta(days=400)
        db.add(row)
        db.commit()
    assert service.prune() >= 1
    assert _incidents(dev) == [] and _events(inc.id) == []


def test_api_lists_filters_pages_and_explains(admin_client):
    c = admin_client
    dev = _device()
    base = utcnow() - timedelta(minutes=10)
    for i in range(5):
        service.record(dev, None, "t", [server_event("server_timeout", dev, f"s{i}", base + timedelta(seconds=i))])
    service.record(dev, None, "t", [server_event("provider_failure", dev, "sx", base, turn_id=1, per_turn=True,
                                                 detail={"stage": "tts"})])
    page1 = c.get("/api/incidents", params={"device_id": dev, "limit": 4}).json()
    assert len(page1["items"]) == 4 and page1["next_cursor"]
    assert page1["counts"] == {"undetermined": 5, "server": 1}
    page2 = c.get("/api/incidents", params={"device_id": dev, "limit": 4, "cursor": page1["next_cursor"]}).json()
    ids = [i["id"] for i in page1["items"] + page2["items"]]
    assert len(ids) == 6 == len(set(ids)) and page2["next_cursor"] is None
    srv = c.get("/api/incidents", params={"device_id": dev, "category": "server"}).json()["items"]
    assert [i["reason_code"] for i in srv] == ["tts_failure"] and srv[0]["device_name"] == "Kitchen watch"
    assert c.get("/api/incidents", params={"device_id": dev, "severity": "error"}).json()["counts"] == {"server": 1}
    assert len(c.get("/api/incidents", params={"device_id": dev, "recovered": "false"}).json()["items"]) == 6
    assert c.get("/api/incidents", params={"category": "nonsense"}).status_code == 400
    assert c.get("/api/incidents", params={"cursor": "%%%"}).status_code == 400
    detail = c.get(f"/api/incidents/{srv[0]['id']}").json()
    assert set(detail["explanation"]) == {"what", "where", "cause", "affected", "recovered", "next_step"}
    assert detail["explanation"]["cause"].startswith("Confirmed:")
    assert detail["events"][0]["primary"] is True and detail["events"][0]["detail"]["stage"] == "tts"
    assert "email" not in str(detail).lower()
    assert c.get("/api/incidents/99999999").status_code == 404
    assert c.delete("/api/incidents", params={"device_id": dev}).json()["deleted"] == 6
    assert c.get("/api/incidents", params={"device_id": dev}).json()["items"] == []


def test_api_is_admin_only():
    with TestClient(app) as anon:
        assert anon.get("/api/incidents").status_code == 401
        assert anon.get("/api/incidents/1").status_code == 401
        assert anon.delete("/api/incidents").status_code == 401


def test_legacy_incidents_are_not_listed_as_open():
    dev = _device()
    with session_scope() as db:
        db.add(DiagIncident(device_id=dev, correlation_key=f"legacy:t-{dev}", category="watch", reason_code="watch_crash",
                            event_count=1, legacy=True))
        db.commit()
    with session_scope() as db:
        repo = IncidentRepo(db)
        assert [i.legacy for i in repo.page(device_id=dev)] == [True]
        assert list(repo.page(device_id=dev, recovered=False)) == []


def test_provider_error_text_never_carries_the_conversation():
    from types import SimpleNamespace

    from app.gateway.device_ws import _safe_message

    turn = SimpleNamespace(user_text="please remind me to call my sister Ana tomorrow", assistant_text="")
    assert _safe_message("Error 400: input 'call my sister Ana' rejected", turn).startswith("[redacted")
    assert _safe_message("Error code: 429 - insufficient_quota", turn) == "Error code: 429 - insufficient_quota"
    assert len(_safe_message("x" * 500, SimpleNamespace(user_text="", assistant_text=None))) == 120
