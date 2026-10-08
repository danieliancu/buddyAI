"""ola Care: 1,000 AI interactions per period. The counting rule (app/interactions.py) and its enforcement over
the real device protocol (FakeWatch, mock providers, the pipeline replaced where a scenario needs it)."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app import entitlements, usage_ops
from app.db.models import Account, UsageOperation, UsageRecord
from app.db.session import session_scope
from app.interactions import counts_as_interaction
from app.pipeline.turn import TurnResult
from app.providers.base import ProviderError, UsageItem
from tests.test_billing_v2 import _account, _grant, _limit, _use, enforce  # noqa: F401 - fixture
from tests.test_e2e import SAMPLES, server  # noqa: F401 - fixture
from tests.test_usage_ws import _settled_ops, _watch
from fake_watch import load_wav_16k  # noqa: E402 - path set by test_usage_ws


# --- the rule -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind,status,reason,text,counts", [
    ("chat", "completed", None, "what's the weather", True),  # one request = one interaction
    ("note", "completed", None, "add milk", True),
    ("reminder", "completed", None, "move it to six", True),
    ("chat", "no_speech", None, "", False),  # silence
    ("chat", "completed", None, "   ", False),  # nothing understood
    ("chat", "error", None, "hello", False),  # failed on ola's side (provider / server)
    ("chat", "aborted", "user_tap", "hello", True),  # the user cancelled after processing began
    ("chat", "aborted", "user_tap", "", False),  # cancelled before anything was understood
    ("chat", "aborted", "connection_lost", "hello", False),  # the connection dropped: never charged
    ("chat", "aborted", "timeout", "hello", False),  # the watch gave up waiting for us
    ("chat", "aborted", "lease_lost", "hello", False),
    ("chat", "aborted", "shutdown", "hello", False),
    ("chat", "aborted", "error", "hello", False),  # a watch-side error
    ("memory", "completed", None, "x", False),  # background learning
    ("voice_sample", "completed", None, "x", False),
    ("operator_test", "completed", None, "x", False),
])
def test_counting_rule(kind, status, reason, text, counts):
    assert counts_as_interaction(kind, status, reason, text) is counts


@pytest.mark.parametrize("outcome,counts", [("", True), ("ignored", False), ("other", False)])
def test_edit_screen_background_talk_is_not_a_request(outcome, counts):
    # the note / reminder edit mic stays open: a TV or another person talking is not the customer's request
    assert counts_as_interaction("note", "completed", None, "and then he said", outcome) is counts


# --- over the device protocol -----------------------------------------------------------------------------------


def _used(acc: int) -> int:
    with session_scope() as db:
        return entitlements.allowance(db, db.get(Account, acc)).used


def _pipeline(monkeypatch, run):
    from app.main import app

    monkeypatch.setattr(app.state.pipeline, "run", run)


async def _paid_watch(server, used: int = 0, acc: int | None = None):
    if acc is None:
        acc = _account()
        _grant(acc)
    if used:
        _use(acc, used)
    return acc, await _watch(server, acc)


async def test_completed_turn_is_one_interaction_and_a_resend_is_not_counted(server, enforce):
    acc, w = await _paid_watch(server)
    tl = await w.ask(load_wav_16k(SAMPLES / "en_1.wav"), "en", realtime=False)
    assert tl.status == "completed"
    answer = await w.resend()  # the same request again (lost reply): never a second interaction
    assert answer.get("duplicate") is True
    await w.close()
    (op,) = await _settled_ops(w.device_id)
    assert op.interaction is True and _used(acc) == 1


async def test_search_and_several_tools_are_still_one_interaction(server, enforce, monkeypatch):
    async def run(turn, io):
        turn.user_text = "opening hours of the library, and add it to my notes"
        turn.usage += [UsageItem("stt", "mock_stt", "m", "audio_second", 3),
                       UsageItem("llm", "openai", "m", "input_token", 900),
                       UsageItem("llm", "openai", "m", "web_search_call", 2),  # two searches
                       UsageItem("llm", "openai", "m", "output_token", 120),
                       UsageItem("tts", "mock_tts", "m", "character", 80)]
        turn.tools_used += ["web_search", "note_add"]
        return TurnResult("completed")

    _pipeline(monkeypatch, run)
    acc, w = await _paid_watch(server)
    await w.send("listen_start", 1, request_id=secrets.token_hex(16), language="en")
    assert (await w.expect("turn_end"))["status"] == "completed"
    await w.send("listen_start", 2, request_id=secrets.token_hex(16), language="en")  # a follow-up
    await w.expect("turn_end")
    await w.close()
    ops = await _settled_ops(w.device_id)
    assert len(ops) == 2 and all(o.interaction for o in ops) and _used(acc) == 2
    with session_scope() as db:
        recs = db.exec(select(UsageRecord).where(UsageRecord.operation_id == ops[0].id)).all()
    assert len(recs) == 5  # every internal cost recorded, one interaction


@pytest.mark.parametrize("scenario", ["no_speech", "provider_error", "server_exception"])
async def test_silence_and_failures_on_our_side_do_not_count(server, enforce, monkeypatch, scenario):
    async def run(turn, io):
        if scenario == "no_speech":
            return TurnResult("no_speech")
        turn.user_text = "hello"
        if scenario == "provider_error":
            raise ProviderError("llm", "Request timed out.")
        raise RuntimeError("boom")

    _pipeline(monkeypatch, run)
    acc, w = await _paid_watch(server)
    await w.send("listen_start", 1, request_id=secrets.token_hex(16), language="en")
    await w.expect("turn_end")
    await w.close()
    (op,) = await _settled_ops(w.device_id)
    assert op.interaction is False and _used(acc) == 0


async def test_ignored_edit_speech_is_not_counted_over_the_protocol(server, enforce, monkeypatch):
    async def run(turn, io):
        turn.user_text = "the weather on the telly"
        turn.edit_outcome = "ignored"  # what the edit modes set for speech not meant for the item
        return TurnResult("completed")

    _pipeline(monkeypatch, run)
    acc, w = await _paid_watch(server)
    await w.send("listen_start", 1, request_id=secrets.token_hex(16), language="en")
    await w.expect("turn_end")
    await w.close()
    (op,) = await _settled_ops(w.device_id)
    assert op.interaction is False and _used(acc) == 0


@pytest.mark.parametrize("understood,counts", [(True, True), (False, False)])
async def test_user_cancel_counts_only_after_the_request_was_understood(server, enforce, monkeypatch, understood, counts):
    started = asyncio.Event()

    async def run(turn, io):
        if understood:
            turn.user_text = "tell me a long story"
        started.set()
        await asyncio.sleep(3600)

    _pipeline(monkeypatch, run)
    acc, w = await _paid_watch(server)
    await w.send("listen_start", 1, request_id=secrets.token_hex(16), language="en")
    await asyncio.sleep(0.3)
    await w.send("abort", 1, reason="user_tap")
    await w.expect("turn_end")
    await w.close()
    (op,) = await _settled_ops(w.device_id)
    assert op.result_status == "aborted" and op.interaction is counts and _used(acc) == int(counts)


async def test_dropped_connection_does_not_count(server, enforce, monkeypatch):
    async def run(turn, io):
        turn.user_text = "what time is it in Tokyo"
        await asyncio.sleep(3600)

    _pipeline(monkeypatch, run)
    acc, w = await _paid_watch(server)
    await w.send("listen_start", 1, request_id=secrets.token_hex(16), language="en")
    await asyncio.sleep(0.3)
    await w.close(abrupt=True)  # Wi-Fi gone mid-answer
    (op,) = await _settled_ops(w.device_id)
    assert op.reason == "connection_lost" and op.interaction is False and _used(acc) == 0


async def test_refused_input_creates_nothing(server, enforce):
    acc, w = await _paid_watch(server)
    await w.send("listen_start", 1, request_id=secrets.token_hex(16), mode="note", note=999)  # no such note
    err = await w.expect("error")
    assert err["code"] == "bad_request"
    await w.close()
    assert await _settled_ops(w.device_id) == [] and _used(acc) == 0


async def test_the_1000th_is_admitted_the_1001st_refused_and_two_watches_share_it(server, enforce):
    acc, w1 = await _paid_watch(server, used=999)
    w2 = await _watch(server, acc)  # a second watch of the same account
    tl = await w1.ask(load_wav_16k(SAMPLES / "en_1.wav"), "en", realtime=False)
    assert tl.status == "completed"  # the 1,000th
    await _settled_ops(w1.device_id)
    assert _used(acc) == 1000
    t = w2.new_turn()
    await w2.send("listen_start", t.turn_id, request_id=secrets.token_hex(16), language="en")
    err = await w2.expect("error")
    assert err["code"] == "limit_reached"  # the 1,001st, from the other watch
    await w1.close()
    await w2.close()
    assert _used(acc) == 1000 and await _settled_ops(w2.device_id) == []  # a refusal never counts


def _trial(days: int, start):
    from app.db.models import Subscription

    return Subscription(id=42, source="stripe", status="trialing", current_period_start=start,
                        current_period_end=start + timedelta(days=days), trial_end=start + timedelta(days=days))


def test_a_long_trial_renews_the_interactions_every_month():
    from app.allowance import period_identity

    start = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
    sub = _trial(90, start)  # Stripe's current period is the whole 90-day trial
    p1 = period_identity(sub, start + timedelta(days=10))
    p2 = period_identity(sub, start + timedelta(days=40))
    p3 = period_identity(sub, start + timedelta(days=85))
    assert p1.key == f"stripe:42:{int(start.timestamp())}"  # the first cycle keeps the period's own key
    assert (p1.period.start, p1.period.end) == (start, datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc))
    assert (p2.period.start, p2.period.end) == (datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc),
                                                datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc))  # no day drift
    assert p3.period.end == start + timedelta(days=90)  # the last cycle ends with the trial
    assert len({p1.key, p2.key, p3.key}) == 3  # each month has its own 1,000


@pytest.mark.parametrize("days", [28, 30, 31])
def test_a_one_month_period_or_trial_is_one_cycle(days):
    from app.allowance import period_identity

    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    sub = _trial(days, start)
    p = period_identity(sub, start + timedelta(days=days - 1))
    assert (p.period.start, p.period.end) == (start, start + timedelta(days=days))


def test_trial_month_two_starts_a_fresh_allowance(enforce):
    """The 1,000 renew a month into a long trial: interactions of the first month do not count in the second."""
    from app.db.models import Subscription

    acc = _account()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add(Subscription(account_id=acc, source="stripe", status="trialing", stripe_subscription_id=f"sub_{secrets.token_hex(4)}",
                            current_period_start=now - timedelta(days=40), current_period_end=now + timedelta(days=50),
                            trial_end=now + timedelta(days=50)))
        db.commit()
    _use(acc, 1000)  # the whole allowance of the current (second) month
    assert usage_ops.admit(usage_ops.AdmitRequest(device_id="d", request_key=f"r:{secrets.token_hex(8)}", request_kind="client",
                                                  kind="chat", account_id=acc, fingerprint="f")).code == "limit_reached"
    with session_scope() as db:
        a = entitlements.allowance(db, db.get(Account, acc))
        first_month = f"stripe:{a.period_key.split(':')[1]}:{int((now - timedelta(days=40)).timestamp())}"
    assert a.period_key != first_month and a.period.start > now - timedelta(days=40)  # a later monthly cycle
    assert a.period.end <= now + timedelta(days=50) and (a.period.end - a.period.start).days <= 31


def test_new_period_starts_from_zero(enforce):
    acc = _account()
    _grant(acc)
    _use(acc, 1000)
    a = usage_ops.admit(usage_ops.AdmitRequest(device_id="d", request_key=f"r:{secrets.token_hex(8)}",
                                               request_kind="client", kind="chat", account_id=acc, fingerprint="f"))
    assert a.code == "limit_reached"
    from app import billing

    with session_scope() as db:  # the next period (a renewed grant: a new period key)
        billing.revoke_complimentary(db, db.get(Account, acc), "t")
    _grant(acc)
    assert _used(acc) == 0  # nothing rolls over, nothing is carried back
    a = usage_ops.admit(usage_ops.AdmitRequest(device_id="d", request_key=f"r:{secrets.token_hex(8)}",
                                               request_kind="client", kind="chat", account_id=acc, fingerprint="f"))
    assert a.allowed


def test_memory_learning_never_uses_an_interaction_but_needs_a_plan(enforce):
    acc = _account()
    req = lambda: usage_ops.AdmitRequest(device_id="d", request_key=f"s:memx:{secrets.token_hex(6)}",  # noqa: E731
                                         request_kind="server", kind="memory", account_id=acc, fingerprint="m")
    assert usage_ops.admit(req()).code == "subscription_required"
    _grant(acc)
    _limit(acc, 3)
    _use(acc, 3)  # all interactions used: learning from earlier conversations still runs
    a = usage_ops.admit(req())
    assert a.allowed
    v = usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, interaction=True)
    assert v.interaction is False and _used(acc) == 3  # a background kind can never count


def test_historical_operations_are_not_counted(enforce):
    """Operations from before migration 0027 (interaction NULL) are never reconstructed as interactions."""
    acc = _account()
    _grant(acc)
    _use(acc, 2)
    with session_scope() as db:
        for op in db.exec(select(UsageOperation).where(UsageOperation.account_id == acc)).all():
            op.interaction = None
            db.add(op)
        db.commit()
    assert _used(acc) == 0
