"""ola Diagnostics across a server restart: a turn is running when the server shuts down; after the restart
the watch (firmware 0.1.0: no session id in its report) reconnects and its report joins the same incident,
which the server's own shutdown evidence classifies as a server-side restart."""

import asyncio
import threading
import time

import httpx
import uvicorn

from tests.test_e2e import _free_port
from tests.test_incidents_e2e import _admin, _incidents, _no_login_rate_limit, _online, _paired  # noqa: F401 - fixture


def _start(app, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(300):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    return srv, th


async def test_server_restart_during_a_turn(monkeypatch):
    from app.main import app

    port = _free_port()
    host = f"127.0.0.1:{port}"
    srv, th = _start(app, port)
    started = threading.Event()

    async def run(turn, io):
        started.set()
        await asyncio.sleep(3600)

    try:
        monkeypatch.setattr(app.state.pipeline, "run", run)
        dev = "diag-e2e-restart"
        token = await _paired(host, dev)
        w = await _online(host, dev, token, fw="0.1.0")
        lost = w.session_id
        await w.send("listen_start", 1, request_id="d" * 32, language="en")
        assert await asyncio.to_thread(started.wait, 5)
    finally:
        srv.should_exit = True
        await asyncio.to_thread(th.join, 15)
    await w.close(abrupt=True)

    srv, th = _start(app, port)  # the server is back (state rebuilt from the database only)
    try:
        w = await _online(host, dev, token, fw="0.1.0",
                          link={"drop": "ws_closed", "mid_turn": True, "offline_ms": 3000, "session_s": 2})
        await w.close()
        items = await _incidents(host, dev, lambda i: any(x["session_id"] == lost and x["event_count"] >= 3 for x in i))
        inc = next(i for i in items if i["session_id"] == lost)
        assert (inc["category"], inc["confidence"], inc["reason_code"]) == ("server", "confirmed", "server_shutdown")
        assert inc["recovered_at"] is not None
        async with httpx.AsyncClient(base_url=f"http://{host}") as http:
            await _admin(http)
            r = await http.get(f"/api/incidents/{inc['id']}")
            assert r.status_code == 200, r.text
            d = r.json()
        kinds = {e["kind"] for e in d["events"]}
        assert {"turn_interrupted", "disconnect"} <= kinds and kinds & {"ws_close", "server_shutdown"}
        watch = next(e for e in d["events"] if e["kind"] == "disconnect")
        assert watch["detail"]["link_basis"] == "inferred"
    finally:
        srv.should_exit = True
        await asyncio.to_thread(th.join, 15)
