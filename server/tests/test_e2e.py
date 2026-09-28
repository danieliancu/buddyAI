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
        code = "424242" if device_id.endswith("1") else "737373"
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
