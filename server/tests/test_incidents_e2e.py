"""ola Diagnostics end to end: real server (mock providers) + FakeWatch over the real WebSocket protocol.
Failure scenarios: Wi-Fi drop mid-turn, provider timeout, server exception, idle timeout, malformed and
duplicate reports, firmware 0.1.0 reports, diagnostics storage unavailable."""

import asyncio
import random
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.config import get_settings
from app.incidents import service
from app.providers.base import ProviderError
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_watch import FakeWatch  # noqa: E402
from tests.test_e2e import _free_port  # noqa: E402


@pytest.fixture(autouse=True)
def _no_login_rate_limit():
    """These tests log in as the operator often, all from 127.0.0.1."""
    for limiter in (LOGIN_PER_ACCOUNT, LOGIN_PER_IP):
        limiter._hits.clear()
    yield


@pytest.fixture(scope="module")
def server():
    from app.main import app

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(300):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "test server did not start"
    yield f"127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)


async def _admin(http: httpx.AsyncClient) -> None:
    if (await http.get("/api/auth/status")).json()["needs_setup"]:
        r = await http.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
    else:
        r = await http.post("/api/auth/login", json={"username": "admin", "password": "password123"})
    assert r.status_code == 200, r.text


async def _paired(host: str, device_id: str) -> str:
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        await _admin(http)
        w = FakeWatch(f"ws://{host}/ws/device", device_id)
        await w.connect()
        code = f"{random.randint(100000, 999999)}"
        await w.send("hello", device_id=device_id, fw_version="t", hw_model="t", pairing_code=code)
        await w.expect("pairing_pending")
        assert (await http.post("/api/devices/pair", json={"code": code, "name": "Diag watch"})).status_code == 200
        token = (await w.expect("paired"))["device_token"]
        await w.close()
        return token


async def _online(host: str, device_id: str, token: str, fw: str = "0.1.1", **reports) -> FakeWatch:
    w = FakeWatch(f"ws://{host}/ws/device", device_id)
    await w.connect()
    await w.send("hello", device_id=device_id, fw_version=fw, hw_model="t", token=token,
                 audio={"uplink_rate": 16000, "downlink_rates": [16000]}, **reports)
    ack = await w.expect("hello_ack")
    w.session_id = ack["session_id"]
    return w


async def _incidents(host: str, device_id: str, until, timeout: float = 10.0) -> list[dict]:
    """Poll the admin API until `until(items)` holds (events are stored in the background)."""
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        await _admin(http)
        deadline = time.monotonic() + timeout
        while True:
            r = await http.get("/api/incidents", params={"device_id": device_id})
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            if until(items) or time.monotonic() > deadline:
                return items
            await asyncio.sleep(0.1)


async def _detail(host: str, incident_id: int) -> dict:
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        await _admin(http)
        r = await http.get(f"/api/incidents/{incident_id}")
        assert r.status_code == 200, r.text
        return r.json()


def _hold_turns(monkeypatch, app):
    """The pipeline waits forever: a turn stays active until the connection goes."""
    started = threading.Event()

    async def run(turn, io):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(app.state.pipeline, "run", run)
    return started


async def test_wifi_drop_mid_turn_is_one_connection_incident(server, monkeypatch):
    from app.main import app

    started = _hold_turns(monkeypatch, app)
    dev = "diag-e2e-wifi"
    token = await _paired(server, dev)
    w = await _online(server, dev, token)
    lost_session = w.session_id
    await w.send("listen_start", 1, request_id="a" * 32, language="en")
    assert await asyncio.to_thread(started.wait, 5)
    await w.close(abrupt=True)  # Wi-Fi gone: no close frame
    items = await _incidents(server, dev, lambda i: i and i[0]["event_count"] >= 2)
    assert len(items) == 1 and items[0]["category"] == "undetermined"  # the server alone cannot tell why
    # the watch is back and reports what it saw
    w = await _online(server, dev, token, link={"drop": "wifi_lost", "wifi_reason": 200, "rssi": -74, "mid_turn": True,
                                                "offline_ms": 5000, "prev_session": lost_session, "turn_id": 1,
                                                "report_id": "0f0f0f0f", "min_heap": 52000})
    items = await _incidents(server, dev, lambda i: i and i[0]["event_count"] >= 3)
    await w.close()
    assert len(items) == 1, items
    inc = items[0]
    assert (inc["category"], inc["confidence"], inc["reason_code"], inc["related_count"]) == (
        "connection", "confirmed", "wifi_lost", 2)
    assert inc["recovered_at"] is not None and inc["session_id"] == lost_session and inc["turn_id"] == 1
    d = await _detail(server, inc["id"])
    assert {e["kind"] for e in d["events"]} == {"turn_interrupted", "ws_close", "disconnect"}
    assert "conversation was cut off" in d["explanation"]["affected"]


async def test_generic_ws_error_from_firmware_010_is_undetermined_and_grouped_by_inference(server, monkeypatch):
    from app.main import app

    _hold_turns(monkeypatch, app)
    dev = "diag-e2e-legacy"
    token = await _paired(server, dev)
    w = await _online(server, dev, token, fw="0.1.0")
    lost_session = w.session_id
    await w.close(abrupt=True)
    await _incidents(server, dev, lambda i: len(i) == 1)
    report = {"drop": "ws_error", "mid_turn": False, "offline_ms": 2500, "session_s": 12}
    w = await _online(server, dev, token, fw="0.1.0", link=report)
    await w.close()
    # the hello is repeated (lost ack): still one event
    w = await _online(server, dev, token, fw="0.1.0", link={**report, "offline_ms": 6500})
    await w.close()
    items = await _incidents(server, dev, lambda i: i and i[-1]["event_count"] >= 2)
    first = next(i for i in items if i["session_id"] == lost_session)
    assert first["event_count"] == 2 and (first["category"], first["confidence"]) == ("undetermined", "unknown")
    d = await _detail(server, first["id"])
    watch = next(e for e in d["events"] if e["detected_by"] == "watch")
    assert watch["detail"]["link_basis"] == "inferred"
    assert "server" not in d["explanation"]["cause"].split(".")[0].lower()


async def test_ai_provider_timeout_is_a_server_incident(server, monkeypatch):
    from app.main import app

    async def run(turn, io):
        raise ProviderError("llm", "Request timed out.")

    monkeypatch.setattr(app.state.pipeline, "run", run)
    dev = "diag-e2e-llm"
    token = await _paired(server, dev)
    w = await _online(server, dev, token)
    await w.send("listen_start", 1, request_id="b" * 32, language="en")
    end = await w.expect("turn_end")
    assert end["status"] == "error"
    await w.send("ping")
    await w.expect("pong")  # the session carries on
    await w.close()
    items = await _incidents(server, dev, lambda i: any(x["category"] == "server" for x in i))
    inc = next(i for i in items if i["category"] == "server")
    assert (inc["reason_code"], inc["suspected_component"], inc["confidence"], inc["turn_id"]) == (
        "llm_timeout", "llm", "confirmed", 1)


async def test_server_exception_during_a_turn_does_not_end_the_session(server, monkeypatch):
    from app.main import app

    async def run(turn, io):
        raise RuntimeError("boom with private words")

    monkeypatch.setattr(app.state.pipeline, "run", run)
    dev = "diag-e2e-exc"
    token = await _paired(server, dev)
    w = await _online(server, dev, token)
    await w.send("listen_start", 1, request_id="c" * 32, language="en")
    assert (await w.expect("turn_end"))["status"] == "error"
    await w.send("ping")
    await w.expect("pong")
    await w.close()
    items = await _incidents(server, dev, lambda i: any(x["category"] == "server" for x in i))
    inc = next(i for i in items if i["category"] == "server")
    assert inc["reason_code"] == "server_exception" and inc["severity"] == "error"
    d = await _detail(server, inc["id"])
    assert d["events"][0]["detail"]["exception"] == "RuntimeError"
    assert "private words" not in str(d)  # exception messages are never stored


async def test_missing_heartbeat_times_out_undetermined_then_recovers(server, monkeypatch):
    monkeypatch.setattr(get_settings(), "session_idle_timeout_s", 1)
    dev = "diag-e2e-silent"
    token = await _paired(server, dev)
    w = await _online(server, dev, token)
    items = await _incidents(server, dev, lambda i: len(i) == 1)  # the watch says nothing for 1 s
    assert items[0]["reason_code"] == "watch_silent" and items[0]["category"] == "undetermined"
    assert items[0]["recovered_at"] is None
    await w.close()
    monkeypatch.setattr(get_settings(), "session_idle_timeout_s", 45)
    w = await _online(server, dev, token)
    items = await _incidents(server, dev, lambda i: i and i[0]["recovered_at"] is not None)
    await w.close()
    assert items[0]["recovered_at"] is not None


async def test_watchdog_restart_report_is_a_watch_incident(server):
    dev = "diag-e2e-wdt"
    token = await _paired(server, dev)
    w = await _online(server, dev, token)
    sess = w.session_id
    await w.close(abrupt=True)
    w = await _online(server, dev, token, boot={"reset_reason": "task_wdt", "prev_uptime_s": 4000,
                                                "prev_session": sess, "report_id": "11112222"},
                      link={"drop": "reboot", "prev_session": sess, "offline_ms": 9000, "report_id": "11112223"})
    await w.close()
    items = await _incidents(server, dev, lambda i: any(x["event_count"] >= 3 for x in i))
    inc = next(i for i in items if i["session_id"] == sess)
    assert (inc["category"], inc["reason_code"], inc["confidence"], inc["severity"]) == (
        "watch", "watch_freeze", "confirmed", "error")


async def test_malformed_reports_do_not_break_the_hello(server):
    dev = "diag-e2e-junk"
    token = await _paired(server, dev)
    w = await _online(server, dev, token, boot=[1, 2, 3],
                      link={"drop": {"$gt": ""}, "offline_ms": "lots", "rssi": 10**30, "ws": {"tls": "x"},
                            "prev_session": "<script>"})
    await w.send("ping")
    await w.expect("pong")
    await w.close()
    items = await _incidents(server, dev, lambda i: len(i) >= 1)
    assert items and all(i["category"] == "undetermined" for i in items)


async def test_diagnostics_storage_down_never_affects_the_watch(server, monkeypatch):
    dev = "diag-e2e-dbdown"
    token = await _paired(server, dev)
    real = service.record
    calls = []

    def down(*a, **k):
        calls.append(1)
        raise RuntimeError("diagnostics database unavailable")

    monkeypatch.setattr(service, "record", down)
    w = await _online(server, dev, token, boot={"reset_reason": "brownout", "report_id": "abcdef01"})
    await w.send("ping")
    await w.expect("pong")
    await w.close()
    # The failed write is queued on another thread just after the call; a maintenance retry may also hold it
    # for a moment while it tries again (and fails again).
    for _ in range(50):
        if calls and service.pending() >= 1:
            break
        await asyncio.sleep(0.1)
    assert calls and service.pending() >= 1
    monkeypatch.setattr(service, "record", real)
    await asyncio.to_thread(service.flush_retry)
    items = await _incidents(server, dev, lambda i: len(i) >= 1)
    assert [i["reason_code"] for i in items] == ["watch_power_fault"]
