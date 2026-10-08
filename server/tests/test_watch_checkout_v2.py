"""Watch-only checkout: the watch is charged once, the card is saved for ola Care with recorded consent,
and no subscription exists until the watch is paired. Orders and accounts come only from verified
webhooks of paid sessions. No real Stripe calls."""

import json
import secrets
import time

import pytest
import stripe
from fastapi.testclient import TestClient
from sqlmodel import select

from app import billing
from app.db.models import Account, BillingConsent, CareActivation, Order, Subscription
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from app.main import app
from app.ratelimit import CHECKOUT_STATUS_PER_IP, SIGNUP_PER_IP

from tests.watch_helpers import (
    WEBHOOK_SECRET,
    FakeCheckout,
    accept_terms_body,
    enable_billing,
    event,
    pi_fetch,
    session_obj,
    start_checkout,
)


@pytest.fixture(autouse=True)
def _billing(monkeypatch):
    enable_billing(monkeypatch)
    SIGNUP_PER_IP._hits.clear()
    CHECKOUT_STATUS_PER_IP._hits.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake(monkeypatch):
    return FakeCheckout(monkeypatch)


def _addr() -> str:
    return f"buyer-{secrets.token_hex(4)}@example.com"


async def _deliver(evt, pm="pm_card_visa"):
    with session_scope() as db:
        return await billing.handle_event(db, evt, fetch_payment_intent=pi_fetch(pm))


# --- checkout parameters + consent --------------------------------------------------------------------


def test_status_publishes_terms_with_price_trial_and_start_rule(client):
    st = client.get("/api/shop/status").json()
    assert st["open"] is True and st["trial_days"] == 30 and st["trial_starts"] == "on_pairing"
    t = st["care_terms"]["gbp"]
    assert t["amount_minor"] == 790 and t["interval"] == "month"
    assert "£7.90 per month" in t["text"] and "30-day" in t["text"] and "when I pair my watch" in t["text"]
    assert "Nothing is charged for ola Care today" in t["text"]


def test_checkout_is_one_time_payment_with_card_saved_and_no_subscription(client, fake):
    cs_id = start_checkout(client, fake)
    p = fake.last
    assert p["mode"] == "payment"
    assert [li["price"] for li in p["line_items"]] == ["price_watch_gbp"]  # the watch only
    assert "subscription_data" not in p
    assert p["payment_intent_data"]["setup_future_usage"] == "off_session"
    assert p["customer_creation"] == "always"
    assert p["consent_collection"] == {"terms_of_service": "required"}
    assert "£7.90 per month" in p["custom_text"]["terms_of_service_acceptance"]["message"]
    assert "not charged today" in p["custom_text"]["submit"]["message"]
    assert p["metadata"]["kind"] == "watch" and p["metadata"]["care_terms_version"]
    assert p["automatic_tax"] == {"enabled": True}
    countries = p["shipping_address_collection"]["allowed_countries"]
    assert "GB" in countries and "DE" in countries and "US" not in countries
    assert p["shipping_options"] == [{"shipping_rate": "shr_uk"}]
    assert p["success_url"].startswith("https://www.example.com/thank-you?session_id=")
    with session_scope() as db:
        row = db.exec(select(BillingConsent).where(BillingConsent.stripe_checkout_session_id == cs_id)).one()
        assert row.status == "pending" and row.amount_minor == 790 and row.currency == "gbp" and row.trial_days == 30
        assert row.terms_text == p["custom_text"]["terms_of_service_acceptance"]["message"]
        assert row.terms_sha256 == p["metadata"]["care_terms_sha256"] and row.trial_start_rule == "on_pairing"
        assert row.site_ip and row.site_accepted_at is not None
        assert db.exec(select(Subscription).where(Subscription.stripe_customer_id == "x")).first() is None


def test_checkout_refused_without_accepting_terms(client, fake):
    assert client.post("/api/shop/checkout", json={"currency": "gbp"}).status_code == 400
    body = accept_terms_body(client) | {"care_terms_accepted": False}
    assert client.post("/api/shop/checkout", json=body).status_code == 400
    assert fake.calls == []


def test_checkout_refused_when_terms_changed(client, fake):
    body = accept_terms_body(client) | {"care_terms_sha256": "0" * 64}
    r = client.post("/api/shop/checkout", json=body)
    assert r.status_code == 409 and "reload" in r.json()["detail"]
    assert fake.calls == []


# --- webhook outcomes -----------------------------------------------------------------------------------


async def test_paid_session_creates_account_order_and_pending_care_but_no_subscription(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    evt = event("checkout.session.completed", session_obj(cs_id, addr, cus))
    assert await _deliver(evt) is True
    assert await _deliver(evt) is False  # the same event id again: ignored
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        assert acc.password_hash is None and acc.stripe_customer_id == cus
        order = db.exec(select(Order).where(Order.account_id == acc.id)).one()
        assert order.status == "paid" and order.checkout_flow == "watch" and order.amount_total == 8498
        assert db.exec(select(Subscription).where(Subscription.account_id == acc.id)).all() == []  # no trial yet
        act = db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).one()
        assert act.status == "awaiting_pairing" and act.payment_method_id == "pm_card_visa" and act.stripe_customer_id == cus
        consent = db.exec(select(BillingConsent).where(BillingConsent.stripe_checkout_session_id == cs_id)).one()
        assert consent.status == "accepted" and consent.account_id == acc.id and consent.order_id == order.id
        assert consent.stripe_tos_consent == "accepted" and consent.stripe_payment_method_id == "pm_card_visa"
    mails = [m for m in ConsoleEmailSender.sent if m.to == addr]
    welcome = [m for m in mails if "set your password" in m.subject]
    assert len(welcome) == 1 and "/reset-password?welcome=1&token=" in welcome[0].text and "7 days" in welcome[0].text
    confirmed = [m for m in mails if m.subject.startswith("Your ola order")]
    assert len(confirmed) == 1 and "£7.90 per month" in confirmed[0].text and "Nothing has been charged for ola Care" in confirmed[0].text
    assert client.get(f"/api/shop/checkout-status?session_id={cs_id}").json() == {"state": "paid", "needs_password": True}


async def test_a_second_delivery_of_another_event_for_the_same_session_changes_nothing(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    obj = session_obj(cs_id, addr, cus)
    await _deliver(event("checkout.session.completed", obj))
    await _deliver(event("checkout.session.completed", obj))  # Stripe re-sent it with a new event id
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        assert len(db.exec(select(Order).where(Order.account_id == acc.id)).all()) == 1
        assert len(db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).all()) == 1
    assert len([m for m in ConsoleEmailSender.sent if m.to == addr and m.subject.startswith("Your ola order")]) == 1


async def test_delayed_payment_waits_then_succeeds(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    await _deliver(event("checkout.session.completed", session_obj(cs_id, addr, cus, payment_status="unpaid")))
    with session_scope() as db:
        order = db.exec(select(Order).where(Order.stripe_session_id == cs_id)).one()
        assert order.status == "payment_pending" and order.account_id is None
        assert db.exec(select(Account).where(Account.email == addr)).first() is None  # no account, no email yet
    assert not [m for m in ConsoleEmailSender.sent if m.to == addr]
    assert client.get(f"/api/shop/checkout-status?session_id={cs_id}").json()["state"] == "processing"

    await _deliver(event("checkout.session.async_payment_succeeded", session_obj(cs_id, addr, cus)))
    with session_scope() as db:
        order = db.exec(select(Order).where(Order.stripe_session_id == cs_id)).one()
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        assert order.status == "paid" and order.account_id == acc.id
        assert db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).one().status == "awaiting_pairing"
    assert client.get(f"/api/shop/checkout-status?session_id={cs_id}").json()["state"] == "paid"


async def test_delayed_payment_fails(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    await _deliver(event("checkout.session.completed", session_obj(cs_id, addr, cus, payment_status="unpaid")))
    await _deliver(event("checkout.session.async_payment_failed", session_obj(cs_id, addr, cus, payment_status="unpaid")))
    with session_scope() as db:
        assert db.exec(select(Order).where(Order.stripe_session_id == cs_id)).one().status == "payment_failed"
        assert db.exec(select(Account).where(Account.email == addr)).first() is None
    assert any("could not be paid" in m.subject for m in ConsoleEmailSender.sent if m.to == addr)
    assert client.get(f"/api/shop/checkout-status?session_id={cs_id}").json()["state"] == "failed"


async def test_expired_or_abandoned_session(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    assert client.get(f"/api/shop/checkout-status?session_id={cs_id}").json()["state"] == "processing"
    await _deliver(event("checkout.session.completed", session_obj(cs_id, addr, cus, payment_status="unpaid")))
    await _deliver(event("checkout.session.expired", session_obj(cs_id, addr, cus, payment_status="unpaid")))
    assert client.get(f"/api/shop/checkout-status?session_id={cs_id}").json()["state"] == "cancelled"
    # A cancelled checkout that never completed leaves nothing behind.
    other = start_checkout(client, fake)
    await _deliver(event("checkout.session.expired", session_obj(other, addr, cus, payment_status="unpaid")))
    with session_scope() as db:
        assert db.exec(select(Order).where(Order.stripe_session_id == other)).first() is None


def test_unknown_session_and_bad_ids(client):
    assert client.get("/api/shop/checkout-status?session_id=cs_test_nothinghere").json() == {"state": "unknown", "needs_password": False}
    assert client.get("/api/shop/checkout-status?session_id=nope").status_code == 422


async def test_stripe_failure_while_reading_the_card_is_retried_cleanly(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    evt = event("checkout.session.completed", session_obj(cs_id, addr, cus))

    def broken(pi_id):
        raise stripe.APIConnectionError("network down")

    with session_scope() as db, pytest.raises(stripe.APIConnectionError):
        await billing.handle_event(db, evt, fetch_payment_intent=broken)
    with session_scope() as db:  # nothing half done, and the event can be applied again
        assert db.exec(select(Order).where(Order.stripe_session_id == cs_id)).first() is None
    assert await _deliver(evt) is True
    with session_scope() as db:
        assert db.exec(select(Order).where(Order.stripe_session_id == cs_id)).one().status == "paid"


async def test_missing_stripe_consent_means_no_trial(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    await _deliver(event("checkout.session.completed", session_obj(cs_id, addr, cus, consent=None)))
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        act = db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).one()
        assert act.status == "not_eligible" and act.reason == "no_consent"
        consent = db.exec(select(BillingConsent).where(BillingConsent.stripe_checkout_session_id == cs_id)).one()
        assert consent.status == "missing"


async def test_terms_hash_mismatch_means_no_trial(client, fake):
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    obj = session_obj(cs_id, addr, cus)
    obj["metadata"]["care_terms_sha256"] = "f" * 64
    await _deliver(event("checkout.session.completed", obj))
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        assert db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).one().reason == "no_consent"


async def test_existing_paying_customer_keeps_their_stripe_customer(client, fake):
    """A second watch bought by someone who already pays keeps their billing customer for the portal."""
    from tests.test_shop import _session_event, _sub

    addr = _addr()
    old_cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:
        await billing.handle_event(db, _session_event(addr, old_cus, sub_id), fetch_subscription=lambda sid: _sub(sid, old_cus))
    new_cus = f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    await _deliver(event("checkout.session.completed", session_obj(cs_id, addr, new_cus)))
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        assert acc.stripe_customer_id == old_cus
        act = db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).one()
        assert act.stripe_customer_id == new_cus  # the card's customer, used only if a trial is allowed


def test_signed_webhook_route_applies_watch_events(client, fake, monkeypatch):
    monkeypatch.setattr(billing, "_fetch_payment_intent", lambda pi: {"payment_method": "pm_x"})
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    payload = json.dumps(event("checkout.session.completed", session_obj(cs_id, addr, cus)))
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{payload}", WEBHOOK_SECRET)
    bad = client.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": f"t={ts},v1=00"})
    assert bad.status_code == 400
    with session_scope() as db:
        assert db.exec(select(Order).where(Order.stripe_session_id == cs_id)).first() is None  # nothing applied
    # handle_event's default fetch is bound at definition time: route it through the patched module function.
    monkeypatch.setattr(billing.handle_event, "__defaults__", (billing._fetch_subscription, billing._fetch_payment_intent))
    ok = client.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": f"t={ts},v1={sig}"})
    assert ok.status_code == 200 and ok.json()["processed"] is True
    again = client.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": f"t={ts},v1={sig}"})
    assert again.json()["processed"] is False
    with session_scope() as db:
        assert db.exec(select(Order).where(Order.stripe_session_id == cs_id)).one().status == "paid"


# --- local test switches (no Stripe Tax, no Terms of Service URL) -------------------------------------


def test_local_switches_drop_tax_and_stripe_tos_but_keep_the_terms_visible(client, fake, monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "stripe_automatic_tax", False)
    monkeypatch.setattr(s, "stripe_require_tos", False)
    start_checkout(client, fake)
    p = fake.last
    assert p["automatic_tax"] == {"enabled": False}
    assert "consent_collection" not in p
    assert "£7.90 per month" in p["custom_text"]["submit"]["message"]  # terms above the Pay button


async def test_without_stripe_tos_the_site_checkbox_is_the_consent(client, fake, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "stripe_require_tos", False)
    addr, cus = _addr(), f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    await _deliver(event("checkout.session.completed", session_obj(cs_id, addr, cus, consent=None)))
    with session_scope() as db:
        consent = db.exec(select(BillingConsent).where(BillingConsent.stripe_checkout_session_id == cs_id)).one()
        assert consent.status == "accepted" and consent.stripe_tos_consent == "not_required"
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        assert db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).one().status == "awaiting_pairing"


def test_live_keys_ignore_the_local_switches(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "stripe_automatic_tax", False)
    monkeypatch.setattr(s, "stripe_require_tos", False)
    monkeypatch.setattr(s, "stripe_secret_key", "sk_live_x")
    assert s.stripe_tax_on and s.stripe_tos_on
