"""Usage admission over the real device protocol: new watches (listen_start.request_id) and older firmware
(no request_id), resends, the "other conversations in progress" refusal, and costs written per operation."""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import httpx
from sqlmodel import select

from app import usage_ops
from app.db.models import Device, UsageOperation, UsageRecord
from app.db.session import session_scope
from tests.test_billing_v2 import _account, _grant, _use, enforce  # noqa: F401 - fixture
from tests.test_e2e import SAMPLES, server  # noqa: F401 - fixture

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_watch import FakeWatch, load_wav_16k  # noqa: E402


async def _watch(host: str, account_id: int | None, legacy: bool = False) -> FakeWatch:
    device_id = f"uws-{secrets.token_hex(3)}"
    code = f"{secrets.randbelow(900000) + 100000}"
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
    if account_id is not None:
        with session_scope() as db:
            dev = db.get(Device, device_id)
            dev.account_id = account_id
            db.add(dev)
            db.commit()
    w = FakeWatch(f"ws://{host}/ws/device", device_id, legacy=legacy)
    await w.connect()
    await w.hello_token(token)
    return w


async def _settled_ops(device_id: str) -> list[UsageOperation]:
    """The device's operations once none is active (settlement follows turn_end)."""
    for _ in range(100):
        ops = _ops(device_id)
        if ops and all(o.state not in usage_ops.ACTIVE for o in ops):
            return ops
        await asyncio.sleep(0.05)
    return _ops(device_id)


def _ops(device_id: str) -> list[UsageOperation]:
    with session_scope() as db:
        rows = db.exec(select(UsageOperation).where(UsageOperation.device_id == device_id)).all()
        for r in rows:
            db.expunge(r)
        return list(rows)


async def test_new_watch_turn_is_one_settled_operation_and_a_resend_is_not_run(server):
    w = await _watch(server, None)
    tl = await w.ask(load_wav_16k(SAMPLES / "en_1.wav"), "en", realtime=False)
    assert tl.status == "completed"
    answer = await w.resend()
    assert answer["type"] == "turn_end" and answer.get("duplicate") is True and answer["status"] == "completed"
    await w.close()
    ops = await _settled_ops(w.device_id)
    assert len(ops) == 1 and ops[0].state == "settled" and ops[0].request_kind == "client"
    with session_scope() as db:
        recs = db.exec(select(UsageRecord).where(UsageRecord.operation_id == ops[0].id)).all()
    assert recs and all(r.dedup_key and r.period_key == ops[0].period_key for r in recs)
    assert {r.kind for r in recs} >= {"stt", "llm", "tts"}


async def test_legacy_watch_is_admitted_by_session_turn(server):
    w = await _watch(server, None, legacy=True)
    tl = await w.ask(load_wav_16k(SAMPLES / "en_1.wav"), "en", realtime=False)
    await w.close()
    assert tl.status == "completed"
    ops = await _settled_ops(w.device_id)
    assert len(ops) == 1 and ops[0].request_kind == "legacy" and ops[0].request_key.startswith("l:")


async def _blocked_account() -> int:
    acc = _account()
    _grant(acc)
    _use(acc, 999)  # room for one more interaction, held by another watch below
    other = usage_ops.admit(usage_ops.AdmitRequest(device_id="other-watch", request_key=f"r:{secrets.token_hex(8)}",
                                                   request_kind="client", kind="chat", account_id=acc, fingerprint="x"))
    assert other.allowed
    return acc


async def test_other_conversations_in_progress_new_and_legacy(server, enforce):
    acc = await _blocked_account()
    new = await _watch(server, acc)
    tl = new.new_turn()
    await new.send("listen_start", tl.turn_id, language="en", request_id=secrets.token_hex(16))
    err = await new.expect("error")
    assert err["code"] == "busy_concurrent"
    await new.close()
    old = await _watch(server, acc, legacy=True)
    tl = old.new_turn()
    await old.send("listen_start", tl.turn_id, language="en")
    err = await old.expect("error")
    assert err["code"] == "busy"  # older firmware: its existing "Server busy" screen
    await old.close()


async def test_bad_request_id_is_rejected(server):
    w = await _watch(server, None)
    tl = w.new_turn()
    await w.send("listen_start", tl.turn_id, request_id="short")
    err = await w.expect("error")
    assert err["code"] == "bad_request"
    await w.close()
