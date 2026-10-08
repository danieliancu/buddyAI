"""Shop, subscription and fair-use allowance (plan M8). No real Stripe calls."""

import json
import secrets
import time

import pytest
import stripe
from fastapi.testclient import TestClient
from sqlmodel import select

from app import billing, entitlements
from app.config import get_settings
from app.db.models import Account, Order, Subscription, UsageRecord
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from app.main import app

WEBHOOK_SECRET = "whsec_test_secret"


@pytest.fixture
def billing_on(monkeypatch):
    s = get_settings()
    for name, value in {
        "stripe_secret_key": "sk_test_dummy",
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "stripe_price_watch_gbp": "price_watch_gbp",
        "stripe_price_care_gbp": "price_care_gbp",
        "stripe_price_watch_eur": "price_watch_eur",
        "stripe_price_care_eur": "price_care_eur",
        "stripe_shipping_rates_gbp": "shr_uk",
        "site_url": "https://www.example.com",
    }.items():
        monkeypatch.setattr(s, name, value)
    yield s


def _session_event(email_addr: str, customer: str, sub_id: str) -> dict:
    return {
        "id": f"evt_{secrets.token_hex(6)}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{secrets.token_hex(6)}",
                "customer": customer,
                "subscription": sub_id,
                "currency": "gbp",
                "amount_total": 20998,
                "total_details": {"amount_tax": 3500, "amount_shipping": 499},
                "customer_details": {"email": email_addr, "name": "Jane Buyer", "address": {"country": "GB"}},
                "collected_information": {
                    "shipping_details": {
                        "name": "Jane Buyer",
                        "address": {"line1": "1 High St", "city": "London", "postal_code": "N1 1AA", "country": "GB"},
                    }
                },
            }
        },
    }


def _sub(sub_id: str, customer: str, status: str = "trialing") -> dict:
    return {
        "id": sub_id,
        "customer": customer,
        "status": status,
        "trial_end": int(time.time()) + 90 * 86400,
        "cancel_at_period_end": False,
        "items": {"data": [{"current_period_end": int(time.time()) + 90 * 86400}]},
    }


async def test_checkout_webhook_creates_account_order_subscription(billing_on):
    addr = f"buyer-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    event = _session_event(addr, cus, sub_id)
    with session_scope() as db:
        assert await billing.handle_event(db, event, fetch_subscription=lambda sid: _sub(sid, cus)) is True
        assert await billing.handle_event(db, event, fetch_subscription=lambda sid: _sub(sid, cus)) is False  # idempotent
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        assert acc.password_hash is None and acc.stripe_customer_id == cus and acc.country == "GB"
        orders = db.exec(select(Order).where(Order.account_id == acc.id)).all()
        assert len(orders) == 1 and orders[0].amount_total == 20998 and orders[0].shipping_address["city"] == "London"
        sub = billing.active_subscription(db, acc.id)
        assert sub.status == "trialing" and sub.current_period_end is not None
    subjects = [m.subject for m in ConsoleEmailSender.sent if m.to == addr]
    assert any("set your password" in s for s in subjects) and any("order" in s.lower() for s in subjects)


async def test_subscription_status_drives_entitlement(billing_on):
    addr = f"sub-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:
        await billing.handle_event(db, _session_event(addr, cus, sub_id), fetch_subscription=lambda sid: _sub(sid, cus))
        acc_id = db.exec(select(Account).where(Account.email == addr)).one().id
    assert (await entitlements.check(acc_id)).allowed
    for status, allowed in (("past_due", True), ("unpaid", False), ("canceled", False), ("active", True)):
        with session_scope() as db:
            await billing.handle_event(
                db, {"id": f"evt_{secrets.token_hex(6)}", "type": "customer.subscription.updated", "data": {"object": _sub(sub_id, cus, status)}}
            )
        d = await entitlements.check(acc_id)
        assert d.allowed is allowed, status
        if not allowed:
            assert d.code == "subscription_required"


async def test_allowance_warns_then_blocks(billing_on, monkeypatch):
    addr = f"heavy-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:
        await billing.handle_event(db, _session_event(addr, cus, sub_id), fetch_subscription=lambda sid: _sub(sid, cus))
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        acc.interaction_limit_override = 20  # 20 interactions for this account (an authorised exception)
        db.add(acc)
        db.commit()
        acc_id = acc.id

    def spend(pounds: float) -> None:
        with session_scope() as db:
            db.add(UsageRecord(device_id="d", account_id=acc_id, kind="llm", provider="p", model="m", unit="u",
                               quantity=1, cost_usd=pounds / 0.75, cost_micro_gbp=round(pounds * 1_000_000)))
            db.commit()

    from tests.test_billing_v2 import _use

    spend(30.0)  # far above every internal cost threshold: changes nothing for the customer
    _use(acc_id, 17)  # 85% of 20
    assert (await entitlements.check(acc_id)).allowed
    warnings = [m for m in ConsoleEmailSender.sent if m.to == addr and "AI interactions" in m.subject]
    assert len(warnings) == 1
    assert (await entitlements.check(acc_id)).allowed
    assert len([m for m in ConsoleEmailSender.sent if m.to == addr and "AI interactions" in m.subject]) == 1  # once
    _use(acc_id, 3)
    d = await entitlements.check(acc_id)
    assert not d.allowed and d.code == "limit_reached"


# The checkout parameters (watch-only, card saved, consent) are tested in test_watch_checkout_v2.py.
# The webhook tests above replay legacy bundle sessions (no metadata.kind): they must keep working.


def test_shop_closed_without_stripe():
    with TestClient(app) as c:
        assert c.get("/api/shop/status").json()["open"] is False
        assert c.post("/api/shop/checkout", json={"currency": "gbp"}).status_code == 503


def test_webhook_signature(billing_on):
    payload = json.dumps({"id": f"evt_{secrets.token_hex(6)}", "type": "invoice.paid", "data": {"object": {}}})
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{payload}", WEBHOOK_SECRET)
    with TestClient(app) as c:
        ok = c.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": f"t={ts},v1={sig}"})
        bad = c.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": f"t={ts},v1=deadbeef"})
    assert ok.status_code == 200 and ok.json()["processed"] is True
    assert bad.status_code == 400


async def test_trial_ending_reminder_email(billing_on):
    addr = f"trial-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:
        await billing.handle_event(db, _session_event(addr, cus, sub_id), fetch_subscription=lambda sid: _sub(sid, cus))
        await billing.handle_event(
            db, {"id": f"evt_{secrets.token_hex(6)}", "type": "customer.subscription.trial_will_end", "data": {"object": _sub(sub_id, cus)}}
        )
    assert any("free period ends" in m.subject for m in ConsoleEmailSender.sent if m.to == addr)


async def test_cancel_at_from_the_billing_portal_counts_as_cancelled(billing_on):
    """Newer Stripe API versions cancel 'at period end' by setting cancel_at, not cancel_at_period_end."""
    addr = f"cancel-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:
        await billing.handle_event(db, _session_event(addr, cus, sub_id), fetch_subscription=lambda sid: _sub(sid, cus))
        cancelled = _sub(sub_id, cus) | {"cancel_at_period_end": False, "cancel_at": int(time.time()) + 90 * 86400}
        await billing.handle_event(db, {"id": f"evt_{secrets.token_hex(6)}", "type": "customer.subscription.updated", "data": {"object": cancelled}})
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        sub = billing.active_subscription(db, acc.id)
        assert sub.status == "trialing" and sub.cancel_at_period_end is True  # still entitled until the end date
        # The trial reminder is not sent to someone who cancelled.
        before = len([m for m in ConsoleEmailSender.sent if m.to == addr])
        await billing.handle_event(db, {"id": f"evt_{secrets.token_hex(6)}", "type": "customer.subscription.trial_will_end", "data": {"object": cancelled}})
        assert len([m for m in ConsoleEmailSender.sent if m.to == addr]) == before
