"""ola Care v2: allowance maths, periods, complimentary grants, enforcement, top-ups (fake Stripe),
webhook idempotency, refunds, account isolation, concurrency reservations, usage notices, customer plan API.
No real Stripe calls."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import stripe
from fastapi.testclient import TestClient
from sqlmodel import select

from app import allowance as allowance_mod
from app import billing, entitlements, plan as plan_mod, usage_notices, usage_ops
from app.db.models import Account, AuditLog, RevenueEvent, StripeEvent, Subscription, TopUp, UsageNotice, UsageRecord
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from app.main import app
from app.money import usd_to_micro_gbp
from app.pricing.pricing import ProviderPricingConfig, PriceKey
from app.providers.base import UsageItem
from tests.test_accounts import _customer, _no_rate_limits_between_tests  # noqa: F401 - fixture
from tests.test_shop import _session_event, _sub, billing_on  # noqa: F401 - fixture

NOW = datetime.now(timezone.utc)


def _account(**kw) -> int:
    with session_scope() as db:
        acc = Account(email=f"b2-{secrets.token_hex(4)}@example.com", **kw)
        db.add(acc)
        db.commit()
        db.refresh(acc)
        return acc.id


def _spend(account_id: int, pounds: float, **kw) -> None:
    with session_scope() as db:
        db.add(UsageRecord(device_id="d", account_id=account_id, kind="llm", provider="openai", model="m", unit="u",
                           quantity=1, cost_usd=pounds / 0.75, cost_micro_gbp=round(pounds * 1_000_000), **kw))
        db.commit()


@pytest.fixture
def enforce():
    with session_scope() as db:
        plan_mod.update(db, {"enforce": True}, "test")
    yield
    with session_scope() as db:
        plan_mod.update(db, {"enforce": False, "care_allowance_pence": 250, "thresholds": "80,95,100"}, "test")


def _grant(account_id: int, days: int = 30, allowance_pence: int | None = None):
    with session_scope() as db:
        return billing.grant_complimentary(db, db.get(Account, account_id), days, "tester", allowance_pence=allowance_pence)


# --- money / pricing -------------------------------------------------------------------------------------


def test_money_is_exact_and_frozen_per_record():
    assert usd_to_micro_gbp(0.1, Decimal("0.75")) == 75_000
    assert usd_to_micro_gbp(0.0000003, Decimal("0.79")) == 0  # below a micro-pound rounds to 0
    assert usd_to_micro_gbp(None, Decimal("0.75")) is None
    cfg = ProviderPricingConfig({PriceKey("openai", "m", "input_token"): 0.0000001}, Decimal("0.8"))
    recs = cfg.records(
        [UsageItem("llm", "openai", "m", "input_token", 10_000), UsageItem("tts", "openai_tts", "x", "character", 50),
         UsageItem("stt", "mock_stt", "mock", "audio_second", 3)],
        "dev", 7, 1, turn_status="aborted", billable=True,
    )
    priced, unpriced, mock = recs
    assert priced.cost_micro_gbp == 800 and priced.fx_rate == 0.8 and priced.turn_uid == "t7" and priced.turn_status == "aborted"
    assert unpriced.cost_usd is None and unpriced.cost_micro_gbp is None  # flagged, not free
    assert mock.mock is True and priced.mock is False


# --- periods / allowance ----------------------------------------------------------------------------------


def test_periods():
    comp = Subscription(source="complimentary", status="active", current_period_start=NOW - timedelta(days=45),
                        current_period_end=NOW + timedelta(days=45))
    p = allowance_mod.period_for(comp, NOW)
    assert p.kind == "complimentary" and p.start == NOW - timedelta(days=15) and p.end == NOW + timedelta(days=15)
    stripe_sub = Subscription(source="stripe", status="active", current_period_start=NOW - timedelta(days=3),
                              current_period_end=NOW + timedelta(days=27))
    assert allowance_mod.period_for(stripe_sub, NOW).start == NOW - timedelta(days=3)
    cal = allowance_mod.period_for(None, datetime(2026, 12, 15, tzinfo=timezone.utc))
    assert (cal.start.month, cal.end.year, cal.end.month) == (12, 2027, 1)


def test_allowance_excludes_mock_and_non_billable_and_counts_topups():
    acc_id = _account()
    _grant(acc_id)
    _spend(acc_id, 1.00)
    _spend(acc_id, 5.00, mock=True)
    _spend(acc_id, 5.00, billable=False)
    with session_scope() as db:
        acc = db.get(Account, acc_id)
        a = entitlements.allowance(db, acc)
        assert a.used_micro == 1_000_000 and a.limit_micro == 2_500_000 and a.used_pct == 40
        sub = billing.active_subscription(db, acc_id)
        period = allowance_mod.period_for(sub)
        db.add(TopUp(account_id=acc_id, amount_pence=199, allowance_pence=65, status="paid",
                     period_start=period.start, period_end=period.end))
        db.add(TopUp(account_id=acc_id, amount_pence=199, allowance_pence=65, status="pending",
                     period_start=period.start, period_end=period.end))
        db.commit()
        a = entitlements.allowance(db, acc)
        assert a.limit_micro == 3_150_000 and a.used_pct == 31


# --- entitlement ------------------------------------------------------------------------------------------


async def test_enforcement_off_allows_everyone():
    acc_id = _account()
    assert (await entitlements.check(acc_id)).allowed  # no subscription, but enforcement is off


async def test_internal_and_complimentary(enforce):
    internal = _account(internal=True)
    assert (await entitlements.check(internal)).allowed
    acc_id = _account()
    d = await entitlements.check(acc_id)
    assert not d.allowed and d.code == "subscription_required"
    sub, created = _grant(acc_id)
    assert created and sub.stripe_subscription_id is None and sub.stripe_customer_id is None
    assert (await entitlements.check(acc_id)).allowed
    again, created2 = _grant(acc_id)  # idempotent
    assert not created2 and again.id == sub.id
    with session_scope() as db:
        assert len(db.exec(select(Subscription).where(Subscription.account_id == acc_id)).all()) == 1
        assert db.exec(select(AuditLog).where(AuditLog.account_id == acc_id, AuditLog.action == "subscription.complimentary")).first()
        row = db.get(Subscription, sub.id)
        row.current_period_end = NOW - timedelta(minutes=1)  # the pilot ended
        db.add(row)
        db.commit()
    d = await entitlements.check(acc_id)
    assert not d.allowed and d.code == "subscription_required"


async def test_complimentary_refused_over_paid_stripe_subscription(billing_on):
    addr = f"paid-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:
        await billing.handle_event(db, _session_event(addr, cus, sub_id), fetch_subscription=lambda sid: _sub(sid, cus, "active"))
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        with pytest.raises(billing.BillingError) as exc:
            billing.grant_complimentary(db, acc, 30, "tester")
        assert exc.value.status == 409


def _admit(acc_id: int, key: str):
    return usage_ops.admit(usage_ops.AdmitRequest(device_id=key.split(":")[0], request_key=f"r:{key}",
                                                  request_kind="client", kind="chat", account_id=acc_id,
                                                  fingerprint="fp"))


def _end(a) -> None:
    usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True)


async def test_limit_and_concurrent_watches(enforce):
    acc_id = _account()
    _grant(acc_id)
    _spend(acc_id, 2.48)  # 99.2 % of £2.50, reserve is 3p
    first = _admit(acc_id, "watch-a:1")
    assert first.allowed  # a single watch is only refused at 100 %
    second = _admit(acc_id, "watch-b:1")
    assert not second.allowed and second.code == "busy_concurrent"  # the other watch's reservation counts
    _end(first)
    b2 = _admit(acc_id, "watch-b:2")
    assert b2.allowed
    _end(b2)
    _spend(acc_id, 0.02)
    d = await entitlements.check(acc_id)
    assert not d.allowed and d.code == "limit_reached"


async def test_parallel_begin_turns_do_not_overshoot(enforce):
    acc_id = _account()
    _grant(acc_id)
    _spend(acc_id, 2.46)  # room for one 3p reservation, not two
    results = await asyncio.gather(*(asyncio.to_thread(_admit, acc_id, f"w{i}:1") for i in range(4)))
    assert sum(r.allowed for r in results) == 1
    for r in results:
        if r.allowed:
            _end(r)


# --- usage notices ----------------------------------------------------------------------------------------


async def test_thresholds_once_per_period_and_dismiss(enforce):
    acc_id = _account()
    _grant(acc_id)
    _spend(acc_id, 2.10)  # 84 %
    assert await usage_notices.evaluate(acc_id) == [80]
    assert await usage_notices.evaluate(acc_id) == []  # once
    _spend(acc_id, 0.30)  # 96 %
    assert await usage_notices.evaluate(acc_id) == [95]
    with session_scope() as db:
        acc = db.get(Account, acc_id)
        assert usage_notices.pending_web(db, acc).threshold == 95
        usage_notices.dismiss_web(db, acc, 95)
        assert usage_notices.pending_web(db, acc) is None
    first = usage_notices.take_watch_notice(acc_id)
    assert first["threshold"] == 95 and first["level"] == "warning"
    assert usage_notices.take_watch_notice(acc_id) is None  # shown once on the watch
    _spend(acc_id, 0.20)
    assert await usage_notices.evaluate(acc_id) == [100]
    assert usage_notices.take_watch_notice(acc_id)["level"] == "limit"
    emails = [m for m in ConsoleEmailSender.sent if "allowance" in m.subject]
    assert emails  # the first threshold still emails


# --- top-ups (fake Stripe) ---------------------------------------------------------------------------------


def _topup_session_event(topup: TopUp, paid: bool = True, account_id: int | None = None, amount: int | None = None,
                         kind: str = "checkout.session.completed") -> dict:
    return {
        "id": f"evt_{secrets.token_hex(6)}",
        "type": kind,
        "data": {"object": {
            "id": topup.stripe_session_id,
            "payment_status": "paid" if paid else "unpaid",
            "amount_total": topup.amount_pence if amount is None else amount,
            "currency": "gbp",
            "payment_intent": f"pi_{topup.id}",
            "client_reference_id": str(account_id or topup.account_id),
            "metadata": {"kind": "topup", "topup_id": str(topup.id), "account_id": str(account_id or topup.account_id)},
        }},
    }


@pytest.fixture
def fake_checkout(monkeypatch):
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return {"id": f"cs_test_{secrets.token_hex(6)}", "url": "https://checkout.stripe.com/c/pay/test"}

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))
    return calls


def _new_topup(acc_id: int) -> TopUp:
    with session_scope() as db:
        topup, _url = billing.create_topup_checkout(db, db.get(Account, acc_id))
        return topup


async def test_topup_checkout_is_one_time_and_grants_once(billing_on, fake_checkout, enforce):
    acc_id = _account()
    _grant(acc_id)
    topup = _new_topup(acc_id)
    params = fake_checkout[-1]
    assert params["mode"] == "payment" and "subscription_data" not in params
    li = params["line_items"][0]["price_data"]
    assert li["unit_amount"] == 199 and li["currency"] == "gbp" and "recurring" not in li
    assert params["metadata"] == {"kind": "topup", "topup_id": str(topup.id), "account_id": str(acc_id)}
    with session_scope() as db:
        assert entitlements.allowance(db, db.get(Account, acc_id)).limit_micro == 2_500_000  # nothing yet

    event = _topup_session_event(topup)
    with session_scope() as db:
        assert await billing.handle_event(db, event) is True
        assert await billing.handle_event(db, event) is False  # same event again
        # a different event for the same session (e.g. async_payment_succeeded) must not grant twice
        await billing.handle_event(db, _topup_session_event(topup, kind="checkout.session.async_payment_succeeded"))
        assert db.get(TopUp, topup.id).status == "paid"
        assert entitlements.allowance(db, db.get(Account, acc_id)).limit_micro == 3_150_000
        rev = db.exec(select(RevenueEvent).where(RevenueEvent.account_id == acc_id)).all()
        assert [(r.kind, r.amount_pence) for r in rev] == [("topup", 199)]


async def test_concurrent_duplicate_webhooks_grant_once(billing_on, fake_checkout, enforce):
    acc_id = _account()
    _grant(acc_id)
    topup = _new_topup(acc_id)
    event = _topup_session_event(topup)

    async def deliver():
        with session_scope() as db:
            return await billing.handle_event(db, event)

    results = await asyncio.gather(deliver(), deliver(), deliver())
    assert sorted(results) == [False, False, True]
    with session_scope() as db:
        assert entitlements.allowance(db, db.get(Account, acc_id)).topup_micro == 650_000


async def test_unpaid_expired_and_mismatched_sessions_grant_nothing(billing_on, fake_checkout, enforce):
    acc_id, other_id = _account(), _account()
    _grant(acc_id)
    _grant(other_id)
    unpaid = _new_topup(acc_id)
    with session_scope() as db:
        await billing.handle_event(db, _topup_session_event(unpaid, paid=False))
        assert db.get(TopUp, unpaid.id).status == "pending"
        await billing.handle_event(db, _topup_session_event(unpaid, kind="checkout.session.expired"))
        assert db.get(TopUp, unpaid.id).status == "expired"
        await billing.handle_event(db, _topup_session_event(unpaid))  # a late "paid" after expiry
        assert db.get(TopUp, unpaid.id).status == "expired"
    wrong_account = _new_topup(acc_id)
    underpaid = _new_topup(acc_id)
    with session_scope() as db:
        await billing.handle_event(db, _topup_session_event(wrong_account, account_id=other_id))
        await billing.handle_event(db, _topup_session_event(underpaid, amount=1))
        assert db.get(TopUp, wrong_account.id).status == "pending"
        assert db.get(TopUp, underpaid.id).status == "pending"
        assert entitlements.allowance(db, db.get(Account, acc_id)).topup_micro == 0
        assert entitlements.allowance(db, db.get(Account, other_id)).topup_micro == 0  # isolation


async def test_refund_withdraws_topup(billing_on, fake_checkout, enforce):
    acc_id = _account()
    _grant(acc_id)
    topup = _new_topup(acc_id)
    with session_scope() as db:
        await billing.handle_event(db, _topup_session_event(topup))
        refund = {"id": f"evt_{secrets.token_hex(6)}", "type": "charge.refunded", "data": {"object": {
            "id": f"ch_{secrets.token_hex(4)}", "payment_intent": f"pi_{topup.id}", "refunded": True,
            "amount_refunded": 199, "currency": "gbp"}}}
        await billing.handle_event(db, refund)
        assert db.get(TopUp, topup.id).status == "refunded"
        assert entitlements.allowance(db, db.get(Account, acc_id)).topup_micro == 0
        kinds = sorted((r.kind, r.amount_pence) for r in db.exec(select(RevenueEvent).where(RevenueEvent.account_id == acc_id)))
        assert kinds == [("refund", -199), ("topup", 199)]


async def test_topup_needs_a_plan(billing_on, fake_checkout, enforce):
    acc_id = _account()
    with session_scope() as db, pytest.raises(billing.BillingError) as exc:
        billing.create_topup_checkout(db, db.get(Account, acc_id))
    assert exc.value.status == 409


async def test_failed_handler_releases_the_event_claim(billing_on):
    event = {"id": f"evt_{secrets.token_hex(6)}", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_x", "metadata": {"kind": "subscription"}, "client_reference_id": "0",
                                 "subscription": "sub_x"}}}

    def boom(_sid):
        raise RuntimeError("stripe down")

    with session_scope() as db:
        with pytest.raises(RuntimeError):
            await billing.handle_event(db, event, fetch_subscription=boom)
        assert db.get(StripeEvent, event["id"]) is None  # Stripe's retry will run it


async def test_invoice_paid_records_subscription_revenue(billing_on):
    addr = f"inv-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:
        await billing.handle_event(db, _session_event(addr, cus, sub_id), fetch_subscription=lambda sid: _sub(sid, cus))
        inv = {"id": f"in_{secrets.token_hex(4)}", "customer": cus, "amount_paid": 799, "currency": "gbp"}
        for _ in range(2):
            await billing.handle_event(db, {"id": f"evt_{secrets.token_hex(6)}", "type": "invoice.paid", "data": {"object": inv}})
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        rev = db.exec(select(RevenueEvent).where(RevenueEvent.account_id == acc.id)).all()
        assert sorted((r.kind, r.amount_pence) for r in rev) == [("subscription", 799), ("watch", 20998)]


def test_subscription_checkout_for_existing_account(billing_on, fake_checkout):
    acc_id = _account()
    with session_scope() as db:
        url = billing.create_subscription_checkout(db, db.get(Account, acc_id))
    params = fake_checkout[-1]
    assert url.startswith("https://checkout.stripe.com")
    assert params["mode"] == "subscription" and params["client_reference_id"] == str(acc_id)
    assert [li["price"] for li in params["line_items"]] == ["price_care_gbp"]  # no watch in the basket
    assert "trial_period_days" not in params["subscription_data"]  # paid from the first month


# --- customer API -----------------------------------------------------------------------------------------


def test_customer_plan_api_hides_costs(enforce):
    client, me = _customer()
    _grant(me["id"])
    _spend(me["id"], 1.30, turn_uid="t-plan-1", turn_status="completed")
    body = client.get("/api/me/plan").json()
    assert body["status"]["kind"] == "complimentary" and body["enforced"] is True
    assert body["usage"]["used_pct"] == 52 and body["usage"]["activity_count"] == 1
    assert body["prices"] == {"currency": "GBP", "care_price_pence": 790, "topup_price_pence": 199, "topup_adds_pct": 26}
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
        else:
            yield node

    values = list(walk(body))
    for secret in ("cost", "micro", "token", "usd"):
        assert not any(isinstance(v, str) and secret in v.lower() for v in values), secret
    assert 1.3 not in values and 1_300_000 not in values  # the internal £ amount is never sent
    usage = client.get("/api/me/usage").json()
    assert usage["questions"] == 1 and "cost" not in usage
    assert client.get("/api/me/usage-notice").json() == {"notice": None}  # 52 % < 80 %


def test_operator_settings_and_complimentary_api():
    from tests.test_api import _client as _admin_client  # operator session helper

    c = _admin_client()
    r = c.put("/api/billing/settings", json={"care_allowance_pence": 300, "thresholds": "95, 80,100"})
    assert r.status_code == 200 and r.json()["thresholds"] == "80,95,100" and r.json()["care_allowance_pence"] == 300
    assert c.put("/api/billing/settings", json={"thresholds": "0,150"}).status_code == 422
    c.put("/api/billing/settings", json={"care_allowance_pence": 250})
    acc_id = _account()
    first = c.post(f"/api/accounts/{acc_id}/complimentary", json={"days": 30}).json()
    second = c.post(f"/api/accounts/{acc_id}/complimentary", json={"days": 30}).json()
    assert first["created"] and not second["created"] and first["subscription"]["id"] == second["subscription"]["id"]
    assert c.delete(f"/api/accounts/{acc_id}/complimentary").json() == {"revoked": True}
    with TestClient(app) as anon:
        assert anon.put("/api/billing/settings", json={"enforce": True}).status_code == 401


# --- watch notice + full customer journey (fake Stripe) ------------------------------------------------------


async def test_watch_notice_sent_once_to_the_accounts_watches(enforce):
    from app.gateway.hub import DeviceHub
    from tests.test_items import FakeConn

    acc_id, other = _account(), _account()
    _grant(acc_id)
    _spend(acc_id, 2.00)  # 80 %
    await usage_notices.evaluate(acc_id)
    hub = DeviceHub()
    mine, theirs = FakeConn(acc_id), FakeConn(other)
    hub.connections.update({"w-mine": mine, "w-theirs": theirs})
    assert await hub.push_usage_notice(acc_id) is True
    assert await hub.push_usage_notice(acc_id) is False  # once per threshold and period
    notices = [f for t, f in mine.sent if t == "notice"]
    assert notices == [{"level": "info", "text": "80% of your monthly AI usage used."}]
    assert not [t for t, _ in theirs.sent if t == "notice"]  # other accounts see nothing


async def test_customer_journey_subscribe_limit_topup_refund(billing_on, fake_checkout):
    """Subscribe from the app -> webhook -> active -> 80/95/100 % -> refused -> extra usage paid -> answers
    again -> refund -> refused again. Stripe is faked (signed webhooks are covered in test_shop)."""
    client, me = _customer()
    acc_id = me["id"]
    assert client.post("/api/me/subscribe").status_code == 200
    params = fake_checkout[-1]
    assert params["client_reference_id"] == str(acc_id)
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    completed = {"id": f"evt_{secrets.token_hex(6)}", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_sub", "customer": cus, "subscription": sub_id, "client_reference_id": str(acc_id),
        "metadata": {"kind": "subscription", "account_id": str(acc_id)}}}}
    with session_scope() as db:
        await billing.handle_event(db, completed, fetch_subscription=lambda sid: _sub(sid, cus, "active") | {
            "current_period_start": int(NOW.timestamp()) - 3600, "metadata": {"account_id": str(acc_id)}})
    plan = client.get("/api/me/plan").json()
    assert plan["status"]["kind"] == "active" and plan["topup_available"] and plan["usage"]["used_pct"] == 0

    _spend(acc_id, 2.00)
    assert (await entitlements.check(acc_id)).allowed
    assert client.get("/api/me/usage-notice").json()["notice"] == {"threshold": 80, "level": "info"}
    client.post("/api/me/usage-notice/dismiss", json={"threshold": 80})
    assert client.get("/api/me/usage-notice").json()["notice"] is None
    _spend(acc_id, 0.50)
    d = await entitlements.check(acc_id)
    assert not d.allowed and d.code == "limit_reached"
    await usage_notices.evaluate(acc_id)
    assert client.get("/api/me/usage-notice").json()["notice"]["level"] == "limit"

    r = client.post("/api/me/topups/checkout")
    assert r.status_code == 200
    with session_scope() as db:
        topup = db.get(TopUp, r.json()["topup_id"])
        await billing.handle_event(db, _topup_session_event(topup))
    assert (await entitlements.check(acc_id)).allowed  # extra usage: talking again
    plan = client.get("/api/me/plan").json()
    assert plan["usage"]["extra_pct"] == 26 and plan["topups"][0]["current"] is True

    with session_scope() as db:
        await billing.handle_event(db, {"id": f"evt_{secrets.token_hex(6)}", "type": "charge.refunded", "data": {"object": {
            "id": "ch_r", "payment_intent": f"pi_{r.json()['topup_id']}", "refunded": True, "amount_refunded": 199, "currency": "gbp"}}})
    assert not (await entitlements.check(acc_id)).allowed


async def test_invoice_paid_before_the_subscription_event_still_finds_the_account(billing_on):
    acc_id = _account()
    inv = {"id": f"in_{secrets.token_hex(4)}", "customer": "cus_unknown_yet", "amount_paid": 799, "currency": "gbp",
           "parent": {"subscription_details": {"metadata": {"account_id": str(acc_id)}}}}
    with session_scope() as db:
        await billing.handle_event(db, {"id": f"evt_{secrets.token_hex(6)}", "type": "invoice.paid", "data": {"object": inv}})
        rev = db.exec(select(RevenueEvent).where(RevenueEvent.source_id == inv["id"])).one()
        assert rev.account_id == acc_id
