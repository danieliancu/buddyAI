"""A dropped connection mid-turn (PROTOCOL.md section 6.1): the watch resumes a question that was heard, or sends
one that was not heard in full again; a short gap in the audio does not cut the question."""

import asyncio
import sys
import uuid
from pathlib import Path

from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_watch import FakeWatch, load_wav_16k  # noqa: E402

import httpx  # noqa: E402

from app.db.models import Turn  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from tests.test_e2e import SAMPLES, server  # noqa: E402,F401

CODES = {"resume-1": "181818", "resume-2": "282828", "resume-3": "383838", "resume-4": "484848"}


async def _paired(host: str, device_id: str) -> tuple[FakeWatch, str]:
    code = CODES[device_id]
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        r = await http.get("/api/auth/status")
        if r.json()["needs_setup"]:
            await http.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
        else:
            await http.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        w = FakeWatch(f"ws://{host}/ws/device", device_id)
        await w.connect()
        await w.send("hello", device_id=device_id, fw_version="t", hw_model="t", pairing_code=code)
        await w.expect("pairing_pending")
        assert (await http.post("/api/devices/pair", json={"code": code, "name": "t"})).status_code == 200
        token = (await w.expect("paired"))["device_token"]
        await w.close()
    w = FakeWatch(f"ws://{host}/ws/device", device_id)
    await w.connect()
    await w.hello_token(token)
    return w, token


async def _reconnect(host: str, device_id: str, token: str) -> FakeWatch:
    w = FakeWatch(f"ws://{host}/ws/device", device_id)
    await w.connect()
    await w.hello_token(token)
    return w


def _turns(device_id: str) -> list[Turn]:
    with session_scope() as db:
        return list(db.exec(select(Turn).where(Turn.device_id == device_id).order_by(Turn.id)))


async def _start(w: FakeWatch, **extra):
    tl = w.new_turn()
    rid = uuid.uuid4().hex
    await w.send("listen_start", tl.turn_id, language="en", request_id=rid, **extra)
    return tl, rid


async def test_resume_replays_the_reply_after_a_drop_while_thinking(server):
    w, token = await _paired(server, "resume-1")
    tl, rid = await _start(w)
    await w.stream_utterance(tl, load_wav_16k(SAMPLES / "en_1.wav"), realtime=False)
    await asyncio.wait_for(tl.listen_stopped.wait(), 20)
    # The link dies without the server noticing (half-open): the old connection stays up until the watch's next
    # hello replaces it.
    w._reader.cancel()
    w2 = await _reconnect(server, "resume-1", token)
    r = w2.new_turn()
    await w2.send("resume", r.turn_id, request_id=rid)
    await asyncio.wait_for(r.ended.wait(), 30)
    await asyncio.sleep(0.3)
    await w2.close()
    await w.close(abrupt=True)
    assert r.status == "completed"
    assert r.transcript and r.reply_text and r.frames > 10
    rows = _turns("resume-1")
    assert [t.status for t in rows] == ["completed"]  # one turn, answered once


async def test_resume_of_an_unknown_request_says_missing(server):
    w, _ = await _paired(server, "resume-2")
    r = w.new_turn()
    await w.send("resume", r.turn_id, request_id=uuid.uuid4().hex)
    msg = await w.expect("turn_end")
    await w.close()
    assert msg["turn_id"] == r.turn_id and msg["status"] == "error" and msg["resume"] == "missing"


async def test_a_question_sent_again_replaces_the_first_attempt(server):
    w, token = await _paired(server, "resume-3")
    pcm = load_wav_16k(SAMPLES / "en_1.wav")
    first, rid = await _start(w)
    half = pcm[: len(pcm) // 2]
    await w.stream_utterance(first, half, realtime=False, tail_s=0)
    w._reader.cancel()  # dropped mid-sentence
    w2 = await _reconnect(server, "resume-3", token)
    second, _ = await _start(w2, replaces=rid)
    await w2.stream_utterance(second, pcm, realtime=False)
    await asyncio.wait_for(second.ended.wait(), 30)
    await w2.close()
    await w.close(abrupt=True)
    assert second.status == "completed" and second.frames > 0
    rows = _turns("resume-3")
    assert [t.status for t in rows] == ["aborted", "completed"]
    assert rows[0].error == "The connection closed during the turn"  # not one of the customer's interactions


async def test_a_short_gap_mid_sentence_does_not_end_the_question(server):
    w, _ = await _paired(server, "resume-4")
    pcm = load_wav_16k(SAMPLES / "en_1.wav")
    tl, _ = await _start(w)
    cut = len(pcm) // 2
    await w.stream_utterance(tl, pcm[:cut], realtime=False, tail_s=0)
    await asyncio.sleep(5)  # longer than the old 3 s stall limit
    assert not tl.listen_stopped.is_set()
    await w.stream_utterance(tl, pcm[cut:], realtime=False)  # the rest, on the same connection
    await asyncio.wait_for(tl.ended.wait(), 30)
    await w.close()
    assert tl.listen_stop_reason == "vad" and tl.status == "completed"
