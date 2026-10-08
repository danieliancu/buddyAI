"""ola Care starts when the watch is paired: exactly one subscription, with the configured trial, on the
card saved at purchase; never before pairing, never twice, never for accounts that must not get a trial;
failures are visible and retryable. No real Stripe calls (tests.watch_helpers.FakeGateway)."""

import secrets
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app import billing, care_activation
from app.db.models import Account, AuthToken, CareActivation, Subscription, utcnow
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from app.main import app
from app.ratelimit import CHECKOUT_STATUS_PER_IP, LOGIN_PER_ACCOUNT, LOGIN_PER_IP, RESET_PER_EMAIL, SIGNUP_PER_IP

from tests.watch_helpers import (
    FakeCheckout,
    enable_billing,
    event,
    install_gateway,
    last_link,
    pi_fetch,
    session_obj,
    start_checkout,
    stripe_error,
    watch_waiting,
)


@pytest.fixture(autouse=True)
def _billing(monkeypatch):
    enable_billing(monkeypatch)
    for limiter in (SIGNUP_PER_IP, CHECKOUT_STATUS_PER_IP, LOGIN_PER_ACCOUNT, LOGIN_PER_IP, RESET_PER_EMAIL):
        limiter._hits.clear()
    with session_scope() as db:  # recovery is global: rows left stuck by earlier tests must not be picked up
        for row in db.exec(select(CareActivation).where(col(CareActivation.status).in_(("activating", "failed")))).all():
            row.status = "not_eligible"
            db.add(row)
        db.commit()
    yield


@pytest.fixture
def gw(monkeypatch):
    return install_gateway(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    return FakeCheckout(monkeypatch)


def _client() -> TestClient:
    c = TestClient(app)
    c.__enter__()
    return c


async def _buy(client, fake, pm: str | None = "pm_card_visa", addr: str | None = None, consent="accepted") -> tuple[str, str]:
    addr = addr or f"buyer-{secrets.token_hex(4)}@example.com"
    cus = f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(client, fake)
    with session_scope() as db:
        await billing.handle_event(db, event("checkout.session.completed", session_obj(cs_id, addr, cus, consent=consent)),
                                   fetch_payment_intent=pi_fetch(pm))
    return addr, cus


def _sign_in_from_welcome(client, addr: str) -> None:
    token = last_link(addr, "welcome=1&token=")
    r = client.post("/api/me/password/reset", json={"token": token, "password": "correct-horse-1"})
    assert r.status_code == 200, r.text


def _pair(client, name="Gran's ola"):
    _dev, code = watch_waiting(client)
    return client.post("/api/me/devices/pair", json={"code": code, "name": name})


def _activation(addr: str) -> CareActivation:
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        return db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).one()


def _subs(addr: str) -> list[Subscription]:
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        return list(db.exec(select(Subscription).where(Subscription.account_id == acc.id)).all())


# --- the happy path -------------------------------------------------------------------------------------


async def test_trial_starts_only_when_the_watch_is_paired(fake, gw):
    c = _client()
    addr, cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    plan = c.get("/api/me/plan").json()
    assert plan["status"]["kind"] == "none" and plan["care_activation"]["status"] == "awaiting_pairing"
    assert gw.created == [] and _subs(addr) == []

    r = _pair(c)
    assert r.status_code == 200 and r.json()["care"]["status"] == "active"
    assert len(gw.created) == 1
    params, key = gw.created[0]
    assert params["customer"] == cus and params["trial_period_days"] == 30
    assert params["default_payment_method"] == "pm_card_visa" and params["off_session"] is True
    assert params["items"] == [{"price": "price_care_gbp", "quantity": 1}]
    assert params["metadata"]["activation_id"] == str(_activation(addr).id) and params["metadata"]["consent_id"]
    assert key == f"care-activation-{_activation(addr).id}-pm_card_visa"
    assert gw.default_pm[cus] == "pm_card_visa"
    subs = _subs(addr)
    assert len(subs) == 1 and subs[0].status == "trialing" and subs[0].trial_end is not None
    plan = c.get("/api/me/plan").json()
    assert plan["status"]["kind"] == "trial" and plan["status"]["trial_end"] and plan["care_activation"]["status"] == "active"
    started = [m for m in ConsoleEmailSender.sent if m.to == addr and "free trial has started" in m.subject]
    assert len(started) == 1 and "£7.90 per month" in started[0].text
    paired = [m for m in ConsoleEmailSender.sent if m.to == addr and m.subject == "Your ola watch is connected"]
    assert paired and paired[-1].html and "Gran&#x27;s ola" in paired[-1].html


async def test_a_second_watch_and_repeated_pairing_never_add_a_subscription(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    assert _pair(c).status_code == 200
    assert _pair(c, "Second").status_code == 200
    assert c.post("/api/me/care/activate").status_code == 200  # a stray retry is harmless
    assert len(gw.created) == 1 and len(_subs(addr)) == 1


async def test_concurrent_activations_create_one_subscription(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    acc_id = _activation(addr).account_id
    from tests.test_accounts import _give_watch

    _give_watch(acc_id, with_history=False)
    gw.create_delay = 0.3
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(care_activation.activate_sync(acc_id))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(gw.created) == 1 and results.count("active") >= 1
    assert _activation(addr).status == "active" and len(_subs(addr)) == 1


async def test_live_lease_blocks_a_second_claim(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    acc_id = _activation(addr).account_id
    with session_scope() as db:
        assert care_activation.claim(db, acc_id) is not None
        assert care_activation.claim(db, acc_id) is None  # still leased
        assert care_activation.claim(db, acc_id, now=utcnow() + timedelta(minutes=5)) is not None  # lease expired


# --- failures, retries and recovery ------------------------------------------------------------------------


async def test_missing_card_fails_visibly_then_retry_creates_one(fake, gw):
    c = _client()
    addr, cus = await _buy(c, fake, pm=None)
    _sign_in_from_welcome(c, addr)
    r = _pair(c)
    assert r.status_code == 200  # the watch is paired anyway
    assert r.json()["care"]["status"] == "failed" and r.json()["care"]["error"] == "missing_payment_method"
    plan = c.get("/api/me/plan").json()
    assert plan["status"]["kind"] == "none" and plan["care_activation"]["can_retry"] is True  # never "active"
    assert len(c.get("/api/me/devices").json()) == 1
    gw.default_pm[cus] = "pm_new_card"  # the customer added a card in the billing portal
    r = c.post("/api/me/care/activate")
    assert r.status_code == 200 and r.json()["care_activation"]["status"] == "active"
    assert len(gw.created) == 1 and gw.created[0][0]["default_payment_method"] == "pm_new_card"


async def test_stripe_error_then_retry(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    gw.fail_create = stripe_error("api")
    assert _pair(c).json()["care"]["status"] == "failed"
    assert _activation(addr).last_error_code == "stripe_error" and _activation(addr).next_retry_at is not None
    gw.fail_create = None
    assert c.post("/api/me/care/activate").json()["care_activation"]["status"] == "active"
    assert len(gw.created) == 1


async def test_invalid_card_is_reported(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    gw.fail_create = stripe_error("invalid_pm")
    assert _pair(c).json()["care"]["error"] == "payment_method_invalid"
    gw.fail_create = stripe_error("card")
    assert c.post("/api/me/care/activate").json()["care_activation"]["error"] == "card_error"


async def test_stripe_lookup_failure_never_creates(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    gw.fail_find = stripe_error("api")
    assert _pair(c).json()["care"]["error"] == "stripe_unreachable"
    assert gw.created == []


async def _stuck_activating(c, fake, gw) -> tuple[str, int]:
    addr, _cus = await _buy(c, fake)
    acc_id = _activation(addr).account_id
    from tests.test_accounts import _give_watch

    _give_watch(acc_id, with_history=False)
    with session_scope() as db:  # the server died after marking it activating
        row = db.exec(select(CareActivation).where(CareActivation.account_id == acc_id)).one()
        row.status, row.lease_until, row.attempts = "activating", utcnow() - timedelta(seconds=1), 1
        db.add(row)
        db.commit()
    return addr, acc_id


async def test_crash_after_stripe_created_it_is_adopted_not_duplicated(fake, gw):
    c = _client()
    addr, acc_id = await _stuck_activating(c, fake, gw)
    row = _activation(addr)
    # Stripe created the subscription just before the crash (our DB never saw the answer).
    gw.subs.setdefault(row.stripe_customer_id, []).append(
        gw._new_sub({"customer": row.stripe_customer_id, "trial_period_days": 30,
                     "metadata": {"activation_id": str(row.id), "account_id": str(acc_id)}})
    )
    assert care_activation.recover_sync() == 1
    assert gw.created == [] and _activation(addr).status == "active" and len(_subs(addr)) == 1


async def test_crash_before_stripe_creates_exactly_one(fake, gw):
    c = _client()
    addr, _acc_id = await _stuck_activating(c, fake, gw)
    assert care_activation.recover_sync() == 1
    assert care_activation.recover_sync() == 0  # done: nothing left to recover
    assert len(gw.created) == 1 and _activation(addr).status == "active"


async def test_stuck_row_is_shown_as_retryable(fake, gw):
    c = _client()
    addr, _acc_id = await _stuck_activating(c, fake, gw)
    with session_scope() as db:
        state = care_activation.public_state(db, _activation(addr).account_id)
    assert state["status"] == "failed" and state["can_retry"] is True


async def test_webhook_completes_an_activating_row(fake, gw):
    c = _client()
    addr, acc_id = await _stuck_activating(c, fake, gw)
    row = _activation(addr)
    sub = gw._new_sub({"customer": row.stripe_customer_id, "trial_period_days": 30,
                       "metadata": {"activation_id": str(row.id), "account_id": str(acc_id)}})
    with session_scope() as db:
        await billing.handle_event(db, event("customer.subscription.created", sub))
    assert _activation(addr).status == "active" and _activation(addr).stripe_subscription_id == sub["id"]
    assert care_activation.recover_sync() == 0 and gw.created == []


async def test_automatic_retries_back_off_and_stop(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    gw.fail_create = stripe_error("api")
    _pair(c)
    assert care_activation.recover_sync() == 0  # next retry is in the future
    with session_scope() as db:
        row = db.exec(select(CareActivation).where(CareActivation.account_id == _activation(addr).account_id)).one()
        row.next_retry_at, row.attempts = utcnow() - timedelta(seconds=1), care_activation.MAX_AUTO_ATTEMPTS
        db.add(row)
        db.commit()
    assert care_activation.recover_sync() == 0  # gave up automatically; the customer can still retry
    gw.fail_create = None
    assert c.post("/api/me/care/activate").json()["care_activation"]["status"] == "active"


# --- who gets a trial ---------------------------------------------------------------------------------------


async def test_complimentary_account_gets_no_trial(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        billing.grant_complimentary(db, acc, 30, "test")
    r = _pair(c)
    assert r.json()["care"]["status"] == "not_eligible" and r.json()["care"]["reason"] == "complimentary"
    assert gw.created == [] and c.get("/api/me/plan").json()["status"]["kind"] == "complimentary"


async def test_internal_account_gets_no_trial(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        acc.internal = True
        db.add(acc)
        db.commit()
        acc_id = acc.id
    from tests.test_accounts import _give_watch

    _give_watch(acc_id, with_history=False)
    assert care_activation.activate_sync(acc_id) == "not_eligible" and gw.created == []


async def test_legacy_customer_buying_again_gets_no_second_trial(fake, gw):
    from tests.test_shop import _session_event, _sub

    c = _client()
    addr = f"legacy-{secrets.token_hex(3)}@example.com"
    cus, sub_id = f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}"
    with session_scope() as db:  # bought before this change: watch + trial started at purchase
        await billing.handle_event(db, _session_event(addr, cus, sub_id), fetch_subscription=lambda sid: _sub(sid, cus))
    _sign_in_from_welcome(c, addr)
    assert c.get("/api/me/plan").json()["care_activation"] is None  # legacy: nothing pending
    assert _pair(c).status_code == 200 and gw.created == []  # pairing changes nothing for them
    await _buy(c, fake, addr=addr)  # a second watch, new flow
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
    assert care_activation.activate_sync(acc.id) == "not_eligible"
    assert _activation(addr).reason == "trial_used" and gw.created == [] and len(_subs(addr)) == 1


async def test_no_consent_no_trial_even_after_pairing(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake, consent=None)
    _sign_in_from_welcome(c, addr)
    assert _pair(c).json()["care"]["status"] == "not_eligible" and gw.created == []


# --- authorisation and eligibility to set up a watch --------------------------------------------------------


def _signup(verified: bool = True) -> tuple[TestClient, str]:
    c = _client()
    addr = f"nobuy-{secrets.token_hex(4)}@example.com"
    assert c.post("/api/me/signup", json={"email": addr, "password": "correct-horse-1"}).status_code == 200
    if verified:
        token = [m for m in ConsoleEmailSender.sent if m.to == addr][-1].text.split("token=")[1].split()[0]
        assert c.post("/api/me/verify-email", json={"token": token}).status_code == 200
    return c, addr


def test_account_without_a_watch_order_cannot_pair(gw):
    c, _addr = _signup()
    r = _pair(c)
    assert r.status_code == 403 and "order" in r.json()["detail"]
    ob = c.get("/api/me/onboarding").json()
    assert ob["eligible"] is False and ob["order"] is None


def test_unverified_account_cannot_pair(gw):
    c, _addr = _signup(verified=False)
    assert _pair(c).status_code == 403


def test_retry_needs_a_paired_watch_and_an_activation(gw):
    c, _addr = _signup()
    assert c.post("/api/me/care/activate").status_code == 404


async def test_retry_before_pairing_is_refused(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    assert c.post("/api/me/care/activate").status_code == 409
    assert gw.created == []


def test_retry_needs_sign_in():
    with TestClient(app) as c:
        assert c.post("/api/me/care/activate").status_code == 401


def test_pilot_with_complimentary_access_can_still_pair(gw):
    c, addr = _signup()
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        billing.grant_complimentary(db, acc, 30, "test")
    assert _pair(c).status_code == 200


# --- onboarding state + welcome link ----------------------------------------------------------------------------


async def test_onboarding_progress_is_derived_and_resumable(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    ob = c.get("/api/me/onboarding").json()
    assert ob["eligible"] and ob["order"]["status"] == "paid" and ob["has_password"] and ob["email_verified"]
    assert ob["platform"] is None and ob["watches"] == 0 and ob["care"]["status"] == "awaiting_pairing" and not ob["complete"]
    assert c.put("/api/me/onboarding", json={"platform": "android"}).json()["platform"] == "android"
    assert c.put("/api/me/onboarding", json={"platform": "blackberry"}).status_code == 422
    c.post("/api/me/logout")
    c.post("/api/me/login", json={"email": addr, "password": "correct-horse-1"})
    assert c.get("/api/me/onboarding").json()["platform"] == "android"  # kept across sign-ins
    _pair(c)
    ob = c.get("/api/me/onboarding").json()
    assert ob["watches"] == 1 and ob["care"]["status"] == "active" and ob["complete"] is True


async def test_welcome_link_lasts_a_week_and_works_once(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    token = last_link(addr, "welcome=1&token=")
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        row = db.exec(select(AuthToken).where(AuthToken.account_id == acc.id, AuthToken.purpose == "set_password")).one()
        assert timedelta(days=6) < (row.expires_at.replace(tzinfo=None) - utcnow().replace(tzinfo=None)) <= timedelta(days=7)
        row.expires_at = utcnow() - timedelta(days=1)  # 8 days later
        db.add(row)
        db.commit()
    assert c.post("/api/me/password/reset", json={"token": token, "password": "correct-horse-1"}).status_code == 400
    # A fresh link (forgot password) still lasts 1 hour, and the used link cannot be replayed.
    c.post("/api/me/password/forgot", json={"email": addr})
    reset = last_link(addr, "/reset-password?token=")
    assert c.post("/api/me/password/reset", json={"token": reset, "password": "correct-horse-2"}).status_code == 200
    assert c.post("/api/me/password/reset", json={"token": reset, "password": "correct-horse-3"}).status_code == 400


async def test_a_removed_watch_waiting_with_a_code_is_announced_to_its_account(fake, gw):
    c = _client()
    addr, _cus = await _buy(c, fake)
    _sign_in_from_welcome(c, addr)
    device_id, code = watch_waiting(c)
    assert c.post("/api/me/devices/pair", json={"code": code, "name": "Gran's ola"}).status_code == 200
    assert c.get("/api/me/onboarding").json()["waiting_watch"] is None
    assert c.delete(f"/api/me/devices/{device_id}").status_code == 200
    from tests.watch_helpers import FakeWatchConn

    c.app.state.hub.add_pending("424242", FakeWatchConn(device_id), "hw", "0.1.0")  # the same watch, new code
    w = c.get("/api/me/onboarding").json()["waiting_watch"]
    assert w["name"] == "Gran's ola" and w["expires_in_s"] > 0
    # Re-pairing the same watch never starts a second subscription.
    assert c.post("/api/me/devices/pair", json={"code": "424242", "name": "Gran's ola"}).status_code == 200
    assert len(gw.created) == 1
    assert c.get("/api/me/onboarding").json()["waiting_watch"] is None  # paired again: nothing waiting


def test_a_waiting_watch_is_never_announced_to_another_account(gw):
    c, _addr = _signup()
    hub = c.app.state.hub
    from tests.watch_helpers import FakeWatchConn

    hub.note_removed("buddy-someone-else", 999999, "Not yours")
    hub.add_pending("515151", FakeWatchConn("buddy-someone-else"), "hw", "0.1.0")
    assert c.get("/api/me/onboarding").json()["waiting_watch"] is None
