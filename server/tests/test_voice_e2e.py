"""End to end over the real WebSocket protocol (real server, FakeWatch): clarify which meeting, keep the
requested change, then a deletion that only happens after "da" in the next turn - with turn_end.expect_reply
telling the watch to listen again, and items_open sent before turn_end. The model is scripted and the
speech-to-text returns the scripted sentence (no real AI involved)."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np

from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.providers import mock
from app.providers.llm.base import LLMChunk, LLMProvider, ToolCall
from app.providers.router import ProviderRouter
from tests.test_e2e import server  # noqa: F401  (pytest fixture: a real server on a free port)
from tests.test_items import _account

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_watch import FakeWatch, load_wav_16k  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"


class Script(LLMProvider):
    name = "script"

    def __init__(self) -> None:
        self.steps: list = []
        self.requests: list = []

    async def stream(self, request):
        self.requests.append(request)
        step = self.steps.pop(0) if self.steps else "OK."
        if callable(step):
            step = step(request)
        yield LLMChunk(input_tokens=10, output_tokens=5)
        if isinstance(step, list):
            yield LLMChunk(tool_calls=step)
        else:
            yield LLMChunk(delta=step)


def _ref_for(request, word: str) -> str:
    note = next(m["content"] for m in request.messages if m["role"] == "system" and "Items from the last search" in m["content"])
    return re.search(r"(c\d+) = reminder «[^»]*" + word, note).group(1)


async def _drain(w: FakeWatch) -> list[dict]:
    out = []
    while not w.inbox.empty():
        out.append(w.inbox.get_nowait())
    return out


async def _turn(w: FakeWatch, pcm: np.ndarray, said: str) -> list[dict]:
    mock.MOCK_TRANSCRIPTS["ro"] = said
    await _drain(w)
    tl = await w.ask(pcm, "ro", realtime=False)
    await asyncio.sleep(0.2)
    msgs = await _drain(w)
    assert tl.status == "completed", (said, tl.status, tl.error)
    return msgs


async def _scenario(host: str, script: Script, acc: int) -> None:
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        r = await http.get("/api/auth/status")
        if r.json()["needs_setup"]:
            await http.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
        else:
            await http.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        w = FakeWatch(f"ws://{host}/ws/device", "e2e-voice")
        await w.connect()
        await w.send("hello", device_id="e2e-voice", fw_version="t", hw_model="t", pairing_code="818181")
        await w.expect("pairing_pending")
        assert (await http.post("/api/devices/pair", json={"code": "818181", "name": "t", "account_id": acc})).status_code == 200
        token = (await w.expect("paired"))["device_token"]
        await w.close()
    w = FakeWatch(f"ws://{host}/ws/device", "e2e-voice")
    await w.connect()
    await w.hello_token(token)
    pcm = load_wav_16k(SAMPLES / "en_1.wav")

    # 1. "Mută întâlnirea cu Ștefan la 12": two match -> a question, nothing changed, the watch listens again
    script.steps = [
        [ToolCall("u", "item_update", json.dumps({"target": {"query": {"kind": "reminder", "person": ["Stefan"]}},
                                                  "changes": {"time_local": "12:00"}}))],
        "Cea de luni sau cea de joi?",
    ]
    msgs = await _turn(w, pcm, "Mută întâlnirea cu Ștefan la 12")
    end = next(m for m in msgs if m["type"] == "turn_end")
    assert end.get("expect_reply") is True
    assert not any(m["type"] == "item_show" for m in msgs)

    # 2. "Cea de joi": the choice keeps "at 12"; the changed reminder opens
    script.steps = [lambda req: [ToolCall("c", "item_choose", json.dumps({"ref": _ref_for(req, "Întâlnire")}))], "Am mutat-o la 12."]
    msgs = await _turn(w, pcm, "Cea de joi")
    show = next(m for m in msgs if m["type"] == "item_show")
    assert show["item"]["text"] == "Întâlnire cu Ștefan" and show["item"]["due_local"].endswith("12:00")
    assert not next(m for m in msgs if m["type"] == "turn_end").get("expect_reply")

    # 3. "Șterge întâlnirea de joi": only prepared, asked, the watch listens again
    script.steps = [
        [ToolCall("d", "item_delete", json.dumps({"target": {"query": {"kind": "reminder", "text": "intalnire cu stefan", "weekday": "thu"}}}))],
        "Șterg întâlnirea cu Ștefan de joi, la 12?",
    ]
    msgs = await _turn(w, pcm, "Șterge întâlnirea cu Ștefan de joi")
    assert next(m for m in msgs if m["type"] == "turn_end").get("expect_reply") is True
    with session_scope() as db:
        assert len(ItemRepo(db).list(acc, "reminder")) == 2

    # 4. "Da": deleted now; fresh items, then items_open, then turn_end
    script.steps = ["Am șters întâlnirea."]
    msgs = await _turn(w, pcm, "Da")
    order = [m["type"] for m in msgs if m["type"] in ("items", "items_open", "turn_end")]
    assert order.index("items") < order.index("items_open") < order.index("turn_end")
    with session_scope() as db:
        assert [it.text for it in ItemRepo(db).list(acc, "reminder")] == ["Ședință cu Ștefan"]
    await w.close()


def test_clarify_choose_and_confirmed_delete_over_the_protocol(server, monkeypatch) -> None:  # noqa: F811
    acc = _account()
    base = datetime.now(timezone.utc) + timedelta(days=10)
    mon = base - timedelta(days=base.weekday())
    with session_scope() as db:
        r = ItemRepo(db)
        r.create(acc, "reminder", "Ședință cu Ștefan", mon.replace(hour=7, minute=0, second=0, microsecond=0), participants="Ștefan")
        r.create(acc, "reminder", "Întâlnire cu Ștefan", (mon + timedelta(days=3)).replace(hour=12, minute=0, second=0, microsecond=0),
                 participants="Ștefan, Ana")
    script = Script()
    original = ProviderRouter.llm
    monkeypatch.setattr(ProviderRouter, "llm", lambda self, s: (script, "script-model"))
    saved = dict(mock.MOCK_TRANSCRIPTS)
    try:
        asyncio.run(_scenario(server, script, acc))
    finally:
        mock.MOCK_TRANSCRIPTS.clear()
        mock.MOCK_TRANSCRIPTS.update(saved)
        monkeypatch.setattr(ProviderRouter, "llm", original)
