"""ola Care: interaction allowance maths, periods, complimentary grants, enforcement, top-ups (fake Stripe),
webhook idempotency, refunds, account isolation, concurrency, usage notices, customer plan API.
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
from app import billing, entitlements, plan as plan_mod, usage_ops
from app.db.models import (Account, AuditLog, RevenueEvent, StripeEvent, Subscription, TopUp, UsageNotice,
                           UsageOperation, UsageRecord)
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


def _use(account_id: int, n: int, kind: str = "chat") -> None:
    """n interactions already counted in the account's current period (settled operations)."""
    with session_scope() as db:
        sub = billing.active_subscription(db, account_id)
        pid = allowance_mod.period_identity(sub)
        db.add_all([UsageOperation(
            op_uid=secrets.token_hex(16), account_id=account_id, device_id="d", request_key=f"s:{secrets.token_hex(8)}",
            request_kind="server", request_fingerprint="fp", kind=kind, period_key=pid.key, period_kind=pid.period.kind,
            period_start=pid.period.start, period_end=pid.period.end, source=pid.source, state="settled",
            billable=True, interaction=kind in ("chat", "note", "reminder"), result_status="completed",
        ) for _ in range(n)])
        db.commit()


@pytest.fixture
def enforce():
    with session_scope() as db:
        plan_mod.update(db, {"enforce": True}, "test")
    yield
    with session_scope() as db:
        plan_mod.update(db, {"enforce": False}, "test")


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


def test_interactions_are_counted_not_limited():
    acc_id = _account()
    _grant(acc_id)
    _use(acc_id, 400)
    _use(acc_id, 5, kind="memory")  # background work: never an interaction
    _spend(acc_id, 50.00)  # provider cost, however high, is not the customer's concern
    with session_scope() as db:
        a = entitlements.allowance(db, db.get(Account, acc_id))
        assert a.used == 400 and not hasattr(a, "limit")


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


async def test_interactions_are_unlimited_and_watches_run_together(enforce):
    acc_id = _account()
    _grant(acc_id)
    _use(acc_id, 5000)  # far past the old 1,000 a month
    running = [_admit(acc_id, f"watch-{w}:1") for w in "abc"]
    assert all(r.allowed for r in running)  # several watches at once: nothing refused
    for r in running:
        usage_ops.settle(r.op_id, r.exec_token, status="completed", billable=True, interaction=True)
    assert (await entitlements.check(acc_id)).allowed
    results = await asyncio.gather(*(asyncio.to_thread(_admit, acc_id, f"w{i}:2") for i in range(4)))
    assert all(r.allowed for r in results)
    for r in results:
        _end(r)
    with session_scope() as db:
        assert entitlements.allowance(db, db.get(Account, acc_id)).used == 5003  # still counted


# --- top-ups bought before interactions became unlimited (webhooks still settle them) ----------------------


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
    """A top-up checkout started before extra usage stopped being sold (pending until its webhook)."""
    with session_scope() as db:
        pid = allowance_mod.period_identity(billing.active_subscription(db, acc_id))
        topup = TopUp(account_id=acc_id, amount_pence=199, allowance_pence=0, interactions=250,
                      period_start=pid.period.start, period_end=pid.period.end, period_key=pid.key,
                      stripe_session_id=f"cs_test_{secrets.token_hex(6)}")
        db.add(topup)
        db.commit()
        db.refresh(topup)
        return topup


async def test_late_topup_webhook_settles_once(billing_on, enforce):
    acc_id = _account()
    _grant(acc_id)
    topup = _new_topup(acc_id)
    event = _topup_session_event(topup)
    with session_scope() as db:
        assert await billing.handle_event(db, event) is True
        assert await billing.handle_event(db, event) is False  # same event again
        await billing.handle_event(db, _topup_session_event(topup, kind="checkout.session.async_payment_succeeded"))
        assert db.get(TopUp, topup.id).status == "paid"
        rev = db.exec(select(RevenueEvent).where(RevenueEvent.account_id == acc_id)).all()
        assert [(r.kind, r.amount_pence) for r in rev] == [("topup", 199)]


async def test_concurrent_duplicate_webhooks_grant_once(billing_on, enforce):
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
        assert len(db.exec(select(RevenueEvent).where(RevenueEvent.account_id == acc_id)).all()) == 1


async def test_unpaid_expired_and_mismatched_sessions_grant_nothing(billing_on, enforce):
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


async def test_refund_of_an_earlier_topup(billing_on, enforce):
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
        kinds = sorted((r.kind, r.amount_pence) for r in db.exec(select(RevenueEvent).where(RevenueEvent.account_id == acc_id)))
        assert kinds == [("refund", -199), ("topup", 199)]


def test_extra_usage_is_no_longer_sold(billing_on):
    client, _me = _customer()
    assert client.post("/api/me/topups/checkout").status_code in (404, 405)
    assert not hasattr(billing, "create_topup_checkout")


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
    _use(me["id"], 520)
    _spend(me["id"], 1.30, turn_uid="t-plan-1", turn_status="completed")
    body = client.get("/api/me/plan").json()
    assert body["status"]["kind"] == "complimentary" and body["enforced"] is True
    u = body["usage"]
    assert u["used"] == 520 and u["unlimited"] is True and "limit" not in u and "remaining" not in u
    assert body["prices"] == {"currency": "GBP", "care_price_pence": 790}
    assert "topup_available" not in body and "thresholds" not in body
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
    assert usage["questions"] == 520 and usage["unlimited"] is True and "limit" not in usage and "cost" not in usage
    assert client.get("/api/me/usage-notice").status_code == 404  # no usage warnings any more


def test_operator_settings_and_complimentary_api():
    from tests.test_api import _client as _admin_client  # operator session helper

    c = _admin_client()
    r = c.put("/api/billing/settings", json={"cost_warn_pence": 150})
    assert r.status_code == 200 and r.json()["cost_warn_pence"] == 150
    for gone in ("interaction_limit", "topup_interactions", "topup_price_pence", "thresholds", "care_allowance_pence"):
        assert gone not in r.json()
    assert c.put("/api/billing/settings", json={"cost_warn_pence": 600}).status_code == 422  # above the target
    c.put("/api/billing/settings", json={"cost_warn_pence": 200})
    assert c.patch("/api/accounts/1/allowance", json={"interaction_limit_override": 5}).status_code in (404, 405)
    acc_id = _account()
    first = c.post(f"/api/accounts/{acc_id}/complimentary", json={"days": 30}).json()
    second = c.post(f"/api/accounts/{acc_id}/complimentary", json={"days": 30}).json()
    assert first["created"] and not second["created"] and first["subscription"]["id"] == second["subscription"]["id"]
    assert c.delete(f"/api/accounts/{acc_id}/complimentary").json() == {"revoked": True}
    with TestClient(app) as anon:
        assert anon.put("/api/billing/settings", json={"enforce": True}).status_code == 401


# --- watch notice + full customer journey (fake Stripe) ------------------------------------------------------


async def test_customer_journey_subscribe_and_talk_without_limit(billing_on, fake_checkout):
    """Subscribe from the app -> webhook -> active -> thousands of interactions, an expensive month: always
    answered, no usage warnings. Stripe is faked (signed webhooks are covered in test_shop)."""
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
    assert plan["status"]["kind"] == "active" and plan["usage"] == {**plan["usage"], "used": 0, "unlimited": True}
    _use(acc_id, 3000)
    _spend(acc_id, 9.00)  # an expensive month: never what limits the customer
    assert (await entitlements.check(acc_id)).allowed
    assert client.get("/api/me/plan").json()["usage"]["used"] == 3000


async def test_invoice_paid_before_the_subscription_event_still_finds_the_account(billing_on):
    acc_id = _account()
    inv = {"id": f"in_{secrets.token_hex(4)}", "customer": "cus_unknown_yet", "amount_paid": 799, "currency": "gbp",
           "parent": {"subscription_details": {"metadata": {"account_id": str(acc_id)}}}}
    with session_scope() as db:
        await billing.handle_event(db, {"id": f"evt_{secrets.token_hex(6)}", "type": "invoice.paid", "data": {"object": inv}})
        rev = db.exec(select(RevenueEvent).where(RevenueEvent.source_id == inv["id"])).one()
        assert rev.account_id == acc_id
