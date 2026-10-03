"""End-to-end: real server (mock providers) + FakeWatch over the real WebSocket protocol."""

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_watch import FakeWatch, load_wav_16k  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


async def _paired_watch(host: str, device_id: str) -> tuple[FakeWatch, str]:
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        r = await http.get("/api/auth/status")
        if r.json()["needs_setup"]:
            await http.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
        else:
            await http.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        w = FakeWatch(f"ws://{host}/ws/device", device_id)
        await w.connect()
        code = {"e2e-1": "424242", "e2e-2": "737373", "e2e-auto": "515151", "e2e-issues": "626262"}[device_id]
        await w.send("hello", device_id=device_id, fw_version="t", hw_model="t", pairing_code=code)
        await w.expect("pairing_pending")
        assert (await http.post("/api/devices/pair", json={"code": code, "name": "t"})).status_code == 200
        token = (await w.expect("paired"))["device_token"]
        await w.close()
        w = FakeWatch(f"ws://{host}/ws/device", device_id)
        await w.connect()
        await w.hello_token(token)
        return w, token


async def test_turn_completes_with_audio_and_ttfa(server):
    w, _ = await _paired_watch(server, "e2e-1")
    tl = await w.ask(load_wav_16k(SAMPLES / "en_1.wav"), "en", realtime=False)
    await w.close()
    assert tl.status == "completed"
    assert tl.listen_stop_reason == "vad"
    assert tl.transcript and tl.reply_text and tl.frames > 10
    assert tl.ttfa_ms is not None


async def test_abort_never_delivers_frames_after_turn_end(server):
    w, _ = await _paired_watch(server, "e2e-2")
    pcm = load_wav_16k(SAMPLES / "en_2.wav")
    first = w.new_turn()
    await w.send("listen_start", first.turn_id, language="en")
    await w.stream_utterance(first, pcm, realtime=False)
    await asyncio.wait_for(first.tts_started.wait(), 20)
    await w.abort_active()
    second = w.new_turn()
    await w.send("listen_start", second.turn_id, language="en")
    await w.stream_utterance(second, pcm, realtime=False)
    await asyncio.wait_for(second.ended.wait(), 30)
    await asyncio.wait_for(first.ended.wait(), 5)
    await asyncio.sleep(0.3)
    await w.close()
    assert first.status == "aborted"
    assert second.status == "completed" and second.frames > 0
    assert w.stale_violations == []


async def test_bad_token_is_rejected(server):
    w = FakeWatch(f"ws://{server}/ws/device", "e2e-x")
    await w.connect()
    await w.send("hello", device_id="e2e-x", fw_version="t", hw_model="t", token="nope")
    msg = await w.inbox.get()
    await w.close()
    assert msg["type"] == "error" and msg["code"] == "unauthorized"


async def test_auto_language_is_detected_and_reported(server):
    w, _ = await _paired_watch(server, "e2e-auto")
    tl = await w.ask(load_wav_16k(SAMPLES / "en_3.wav"), "auto", realtime=False)
    await w.close()
    assert tl.status == "completed"
    assert tl.language == "en"  # mock STT returns English text in auto mode


async def test_hello_boot_and_link_reports_become_issues(server):
    w, token = await _paired_watch(server, "e2e-issues")
    await w.close()
    w = FakeWatch(f"ws://{server}/ws/device", "e2e-issues")
    await w.connect()
    await w.send(
        "hello",
        device_id="e2e-issues",
        fw_version="t",
        hw_model="t",
        token=token,
        boot={"reset_reason": "brownout", "prev_uptime_s": 95},
        link={"drop": "wifi_lost", "wifi_reason": 201, "mid_turn": True, "offline_ms": 4200},
    )
    await w.expect("hello_ack")
    await w.close()
    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        await http.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        rows = (await http.get("/api/issues", params={"device_id": "e2e-issues"})).json()
        by_kind = {r["kind"]: r for r in rows}
        assert by_kind["reboot"]["severity"] == "error"
        assert "brownout" in by_kind["reboot"]["summary"] and "1 min 35 s" in by_kind["reboot"]["summary"]
        assert by_kind["disconnect"]["detail"]["mid_turn"] is True
        assert "network not found" in by_kind["disconnect"]["summary"]
        assert (await http.delete("/api/issues", params={"device_id": "e2e-issues"})).json()["deleted"] == len(rows)
        assert (await http.get("/api/issues", params={"device_id": "e2e-issues"})).json() == []


async def test_operator_history_has_type_resources_and_cost(server):
    # e2e-1 had a completed turn in test_turn_completes_with_audio_and_ttfa
    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        await http.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        convs = (await http.get("/api/devices/e2e-1/conversations")).json()
    turn = next(t for c in convs for t in c["turns"] if t["status"] == "completed")
    assert turn["mode"] == "chat" and turn["tools"] == []
    assert {u["kind"] for u in turn["usage"]} >= {"stt", "llm", "tts"}
    assert "cost_gbp" in turn and "stt_ms" in turn
