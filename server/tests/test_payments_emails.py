"""Launch checklist #11 and #16: refunds, disputes, billing emails and their durable delivery. No real Stripe calls."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import col, select

from app import accounts, billing, care_activation, cli, email, email_outbox, retention
from app.config import get_settings
from app.db.models import Account, AuditLog, CareActivation, EmailOutbox, Order, RevenueEvent, Subscription, utcnow
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from tests.test_accounts import _give_watch, _no_rate_limits_between_tests  # noqa: F401 - fixture
from tests.test_care_activation import _activation, _buy, _client, _pair, _sign_in_from_welcome, _subs, fake, gw  # noqa: F401
from tests.watch_helpers import CARE_PENCE, FakeCheckout, enable_billing, event, pi_fetch, session_obj, start_checkout


@pytest.fixture(autouse=True)
def _billing(monkeypatch):
    enable_billing(monkeypatch)
    with session_scope() as db:  # outbox and recovery are global: rows of earlier tests must not be picked up
        for row in db.exec(select(EmailOutbox).where(EmailOutbox.status == "pending")).all():
            db.delete(row)
        for row in db.exec(select(CareActivation).where(col(CareActivation.status).in_(("activating", "failed")))).all():
            row.status = "not_eligible"
            db.add(row)
        db.commit()
    yield


def _mails(to: str, subject_part: str = "") -> list[email.Email]:
    return [m for m in ConsoleEmailSender.sent if m.to == to and subject_part in m.subject]


def _account(**kw) -> tuple[int, str, str]:
    addr, cus = f"pay-{secrets.token_hex(4)}@example.com", f"cus_{secrets.token_hex(4)}"
    with session_scope() as db:
        acc = Account(email=addr, stripe_customer_id=cus, **kw)
        db.add(acc)
        db.commit()
        return acc.id, addr, cus


def _order(account_id: int, addr: str, pi: str | None = None, status: str = "paid", total: int = 8498) -> tuple[int, str]:
    pi = pi or f"pi_{secrets.token_hex(6)}"
    with session_scope() as db:
        order = Order(stripe_session_id=f"cs_{secrets.token_hex(6)}", stripe_payment_intent=pi, account_id=account_id, email=addr,
                      currency="gbp", amount_total=total, status=status, checkout_flow="watch")
        db.add(order)
        db.commit()
        return order.id, pi


def _charge(pi: str | None, total: int, full: bool = False, charge_id: str | None = None, **kw) -> dict:
    return {"id": charge_id or f"ch_{secrets.token_hex(5)}", "payment_intent": pi, "refunded": full, "amount_refunded": total,
            "currency": "gbp", **kw}


async def _handle(ev: dict) -> bool:
    with session_scope() as db:
        return await billing.handle_event(db, ev)


def _refunds(account_id: int) -> list[RevenueEvent]:
    with session_scope() as db:
        return list(db.exec(select(RevenueEvent).where(RevenueEvent.account_id == account_id, RevenueEvent.kind == "refund")
                            .order_by(col(RevenueEvent.id))).all())


def _audit(action: str, account_id: int | None = None) -> list[AuditLog]:
    with session_scope() as db:
        q = select(AuditLog).where(AuditLog.action == action)
        if account_id is not None:
            q = q.where(AuditLog.account_id == account_id)
        return list(db.exec(q).all())


def _order_status(order_id: int) -> tuple[str, int]:
    with session_scope() as db:
        o = db.get(Order, order_id)
        return o.status, o.amount_refunded


def _sub(sub_id: str, customer: str, status: str = "active", **kw) -> dict:
    end = int(time.time()) + 20 * 86400
    return {"id": sub_id, "customer": customer, "status": status, "trial_end": None, "cancel_at_period_end": False,
            "items": {"data": [{"current_period_start": end - 30 * 86400, "current_period_end": end}]}, **kw}


async def _live_sub(customer: str, status: str = "active") -> str:
    sub_id = f"sub_{secrets.token_hex(5)}"
    await _handle(event("customer.subscription.created", _sub(sub_id, customer, status)))
    return sub_id


class FailingSender:
    calls = 0

    async def send(self, message) -> None:
        FailingSender.calls += 1
        raise OSError("smtp down, secret-detail@example.com")


# --- refunds --------------------------------------------------------------------------------------------------


async def test_full_order_refund():
    acc_id, addr, _cus = _account()
    order_id, pi = _order(acc_id, addr)
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True)))
    assert _order_status(order_id) == ("refunded", 8498)
    assert [r.amount_pence for r in _refunds(acc_id)] == [-8498]
    mail = _mails(addr, "refund")
    assert len(mail) == 1 and "£84.98" in mail[0].text and f"#{order_id}" in mail[0].text and "£84.98" in mail[0].html
    assert len(_audit("refund.order", acc_id)) == 1


async def test_two_partial_refunds_on_one_charge_both_count():
    """Stripe's amount is cumulative: 10.00 then 15.00 total means two refunds, 10.00 and 5.00."""
    acc_id, addr, _cus = _account()
    order_id, pi = _order(acc_id, addr, status="shipped")
    ch = f"ch_{secrets.token_hex(5)}"
    await _handle(event("charge.refunded", _charge(pi, 1000, charge_id=ch)))
    assert _order_status(order_id) == ("partial_refund", 1000)
    await _handle(event("charge.refunded", _charge(pi, 1500, charge_id=ch)))
    assert _order_status(order_id) == ("partial_refund", 1500)
    assert [r.amount_pence for r in _refunds(acc_id)] == [-1000, -500]
    mails = _mails(addr, "refund")
    assert len(mails) == 2 and "£10.00" in mails[0].text and "£5.00" in mails[1].text and "£15.00" in mails[1].text


async def test_partial_refunds_reaching_the_full_amount_mark_the_order_refunded():
    acc_id, addr, _cus = _account()
    order_id, pi = _order(acc_id, addr)
    ch = f"ch_{secrets.token_hex(5)}"
    await _handle(event("charge.refunded", _charge(pi, 8000, charge_id=ch)))
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True, charge_id=ch)))
    assert _order_status(order_id) == ("refunded", 8498)
    assert sum(r.amount_pence for r in _refunds(acc_id)) == -8498


async def test_replayed_events_do_not_double_count():
    acc_id, addr, _cus = _account()
    order_id, pi = _order(acc_id, addr)
    ch = f"ch_{secrets.token_hex(5)}"
    first = event("charge.refunded", _charge(pi, 1000, charge_id=ch))
    assert await _handle(first) is True
    assert await _handle(first) is False  # the same event id
    # A new event id carrying the same total (or a released claim being retried).
    assert await _handle(event("charge.refunded", _charge(pi, 1000, charge_id=ch))) is True
    assert [r.amount_pence for r in _refunds(acc_id)] == [-1000]
    assert len(_mails(addr, "refund")) == 1 and len(_audit("refund.order", acc_id)) == 1


async def test_events_out_of_order_keep_the_right_total():
    acc_id, addr, _cus = _account()
    order_id, pi = _order(acc_id, addr)
    ch = f"ch_{secrets.token_hex(5)}"
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True, charge_id=ch)))
    await _handle(event("charge.refunded", _charge(pi, 1000, charge_id=ch)))  # older, arrives late
    assert _order_status(order_id) == ("refunded", 8498)  # never back to partial
    assert [r.amount_pence for r in _refunds(acc_id)] == [-8498]


async def test_refund_recorded_under_the_old_key_is_not_counted_again():
    acc_id, addr, _cus = _account()
    _order_id, pi = _order(acc_id, addr)
    ch = f"ch_{secrets.token_hex(5)}"
    with session_scope() as db:
        db.add(RevenueEvent(source_id=f"refund:{ch}", account_id=acc_id, kind="refund", amount_pence=-1000))
        db.commit()
    await _handle(event("charge.refunded", _charge(pi, 1500, charge_id=ch)))
    assert sum(r.amount_pence for r in _refunds(acc_id)) == -1500


async def test_care_invoice_refund_is_recorded_without_charge_invoice():
    """Current Stripe API versions have no charge.invoice: the known customer identifies the Care payment."""
    acc_id, addr, cus = _account()
    await _handle(event("charge.refunded", _charge(f"pi_{secrets.token_hex(5)}", 799, full=True, customer=cus)))
    assert [(r.amount_pence, r.kind) for r in _refunds(acc_id)] == [(-799, "refund")]
    assert "ola Care payment" in _mails(addr, "refund")[0].text
    assert len(_audit("refund.care", acc_id)) == 1


async def test_care_refund_found_through_the_subscription_customer():
    acc_id, addr, _cus = _account()
    other = f"cus_{secrets.token_hex(4)}"
    with session_scope() as db:
        db.add(Subscription(stripe_subscription_id=f"sub_{secrets.token_hex(4)}", stripe_customer_id=other, account_id=acc_id, status="active"))
        db.commit()
    await _handle(event("charge.refunded", _charge(None, 799, full=True, customer=other)))  # no payment intent either
    assert [r.amount_pence for r in _refunds(acc_id)] == [-799]


async def test_refund_with_older_charge_invoice_and_unknown_customer_is_still_revenue():
    ch = f"ch_{secrets.token_hex(5)}"
    await _handle(event("charge.refunded", _charge(f"pi_{secrets.token_hex(5)}", 799, full=True, charge_id=ch,
                                                   customer="cus_unknown_x", invoice="in_1")))
    with session_scope() as db:
        assert db.exec(select(RevenueEvent).where(RevenueEvent.source_id == f"refund:{ch}:799")).one().amount_pence == -799


async def test_unknown_refund_records_nothing_but_leaves_a_trace():
    ch = f"ch_{secrets.token_hex(5)}"
    await _handle(event("charge.refunded", _charge(f"pi_{secrets.token_hex(5)}", 500, charge_id=ch, customer="cus_nobody")))
    with session_scope() as db:
        assert db.exec(select(RevenueEvent).where(col(RevenueEvent.source_id).contains(ch))).first() is None
    assert any(ch in a.detail for a in _audit("refund.unmatched"))


async def test_partial_refund_of_a_topup_leaves_it_paid():
    from app.db.models import TopUp

    acc_id, addr, _cus = _account()
    pi = f"pi_{secrets.token_hex(5)}"
    with session_scope() as db:
        topup = TopUp(account_id=acc_id, amount_pence=199, allowance_pence=0, period_start=utcnow(), period_end=utcnow(), status="paid", stripe_payment_intent=pi)
        db.add(topup)
        db.commit()
        topup_id = topup.id
    ch = f"ch_{secrets.token_hex(5)}"
    await _handle(event("charge.refunded", _charge(pi, 100, charge_id=ch)))
    with session_scope() as db:
        assert db.get(TopUp, topup_id).status == "paid"
    await _handle(event("charge.refunded", _charge(pi, 199, full=True, charge_id=ch)))
    with session_scope() as db:
        assert db.get(TopUp, topup_id).status == "refunded"
    assert [r.amount_pence for r in _refunds(acc_id)] == [-100, -99]


async def test_refund_never_emails_a_deleted_account():
    acc_id, addr, _cus = _account()
    _order_id, pi = _order(acc_id, addr)
    with session_scope() as db:
        accounts.delete_account(db, db.get(Account, acc_id), cancel_stripe=False)
    n = len(ConsoleEmailSender.sent)
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True)))
    assert [r.amount_pence for r in _refunds(acc_id)] == [-8498]  # still accounted for
    assert len(ConsoleEmailSender.sent) == n


async def test_partially_refunded_order_still_counts_on_the_thank_you_page():
    acc_id, addr, _cus = _account()
    order_id, pi = _order(acc_id, addr)
    await _handle(event("charge.refunded", _charge(pi, 1000)))
    with session_scope() as db:
        assert billing.checkout_status(db, db.get(Order, order_id).stripe_session_id)["state"] == "paid"


# --- a refunded order and ola Care -----------------------------------------------------------------------------


async def _bought(c, fake, gw) -> tuple[str, str, int]:
    addr, cus = await _buy(c, fake)
    with session_scope() as db:
        acc = db.exec(select(Account).where(Account.email == addr)).one()
        order = db.exec(select(Order).where(Order.account_id == acc.id)).one()
        return addr, order.stripe_payment_intent, acc.id


async def test_recovery_does_not_start_care_for_a_refunded_order(fake, gw):
    """The refund arrives while the row is stuck: the row must not turn into a subscription."""
    c = _client()
    addr, pi, acc_id = await _bought(c, fake, gw)
    _give_watch(acc_id, with_history=False)
    with session_scope() as db:
        row = db.exec(select(CareActivation).where(CareActivation.account_id == acc_id)).one()
        row.status, row.lease_until, row.attempts = "activating", utcnow() - timedelta(seconds=1), 1
        db.add(row)
        db.commit()
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True)))
    assert _activation(addr).status == "activating"  # in flight: the gate below decides
    assert care_activation.recover_sync() == 1
    row = _activation(addr)
    assert gw.created == [] and row.status == "not_eligible" and row.reason == "order_refunded"
    assert care_activation.recover_sync() == 0


async def test_failed_row_is_closed_by_the_refund_and_never_retried(fake, gw):
    c = _client()
    addr, pi, acc_id = await _bought(c, fake, gw)
    _give_watch(acc_id, with_history=False)
    with session_scope() as db:
        row = db.exec(select(CareActivation).where(CareActivation.account_id == acc_id)).one()
        row.status, row.next_retry_at = "failed", utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True)))
    assert _activation(addr).status == "not_eligible"
    assert care_activation.recover_sync() == 0 and gw.created == []


async def test_pairing_after_a_full_refund_does_not_start_care(fake, gw):
    c = _client()
    addr, pi, acc_id = await _bought(c, fake, gw)
    _sign_in_from_welcome(c, addr)
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True)))
    assert _activation(addr).reason == "order_refunded"
    _give_watch(acc_id, with_history=False)  # a watch the operator had assigned
    assert care_activation.activate_sync(acc_id) == "not_eligible"
    assert gw.created == [] and _subs(addr) == []


async def test_partial_refund_does_not_stop_care_starting(fake, gw):
    c = _client()
    addr, pi, _acc_id = await _bought(c, fake, gw)
    _sign_in_from_welcome(c, addr)
    await _handle(event("charge.refunded", _charge(pi, 1000)))
    assert _pair(c).json()["care"]["status"] == "active"
    assert len(gw.created) == 1


async def test_buying_again_after_a_refund_starts_care_again(fake, gw):
    c = _client()
    addr, pi, acc_id = await _bought(c, fake, gw)
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True)))
    assert _activation(addr).status == "not_eligible"
    await _buy(c, fake, addr=addr)
    row = _activation(addr)
    assert row.status == "awaiting_pairing" and row.reason == ""
    _give_watch(acc_id, with_history=False)
    assert care_activation.activate_sync(acc_id) == "active" and len(gw.created) == 1


async def test_refund_of_the_newer_order_falls_back_on_the_older_one(fake, gw):
    c = _client()
    addr, cus = await _buy(c, fake)
    acc_id = _activation(addr).account_id
    await _buy(c, fake, addr=addr, pm="pm_second")
    with session_scope() as db:
        orders = db.exec(select(Order).where(Order.account_id == acc_id).order_by(col(Order.id))).all()
        first_id, newest_pi = orders[0].id, orders[1].stripe_payment_intent
    await _handle(event("charge.refunded", _charge(newest_pi, 8498, full=True)))
    assert _activation(addr).status == "awaiting_pairing"
    _give_watch(acc_id, with_history=False)
    assert care_activation.activate_sync(acc_id) == "active"
    assert gw.created[0][0]["default_payment_method"] == "pm_card_visa"  # the card of the order that stands
    assert _activation(addr).order_id == first_id


async def test_full_refund_leaves_running_care_alone_and_tells_the_operator(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_alert_email", "ops@example.com")
    acc_id, addr, cus = _account()
    order_id, pi = _order(acc_id, addr)
    sub_id = await _live_sub(cus)
    await _handle(event("charge.refunded", _charge(pi, 8498, full=True)))
    with session_scope() as db:
        assert db.exec(select(Subscription).where(Subscription.stripe_subscription_id == sub_id)).one().status == "active"
    assert len(_audit("order.refunded_care_running", acc_id)) == 1
    alert = [m for m in _mails("ops@example.com", f"Order {order_id} refunded")]
    assert len(alert) == 1 and addr not in alert[0].text and sub_id in alert[0].text


async def test_refunded_order_stays_final_for_the_operator_api():
    from fastapi import HTTPException

    from app.api.shop import OrderUpdate, update_order

    acc_id, addr, _cus = _account()
    order_id, _pi = _order(acc_id, addr, status="refunded")

    class Req:
        session = {"admin": "tester"}

    with session_scope() as db:
        with pytest.raises(HTTPException) as exc:
            await update_order(order_id, OrderUpdate(status="paid"), Req(), db)
    assert exc.value.status_code == 409 and _order_status(order_id)[0] == "refunded"


async def test_partly_refunded_shipped_order_does_not_email_shipped_twice():
    from app.api.shop import OrderUpdate, update_order

    acc_id, addr, _cus = _account()
    order_id, pi = _order(acc_id, addr)
    with session_scope() as db:
        o = db.get(Order, order_id)
        o.status, o.shipped_at = "shipped", utcnow()
        db.add(o)
        db.commit()
    await _handle(event("charge.refunded", _charge(pi, 500)))

    class Req:
        session = {"admin": "tester"}

    with session_scope() as db:
        await update_order(order_id, OrderUpdate(status="shipped"), Req(), db)
    assert _order_status(order_id) == ("shipped", 500) and _mails(addr, "on its way") == []


# --- disputes -------------------------------------------------------------------------------------------------


def _dispute(pi: str | None, status: str = "needs_response") -> dict:
    return {"id": f"dp_{secrets.token_hex(5)}", "charge": f"ch_{secrets.token_hex(5)}", "payment_intent": pi, "amount": 8498,
            "currency": "gbp", "reason": "fraudulent", "status": status,
            "evidence_details": {"due_by": int(time.time()) + 7 * 86400}}


async def test_dispute_alerts_the_operator_and_touches_nothing(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_alert_email", "ops@example.com")
    acc_id, addr, cus = _account()
    order_id, pi = _order(acc_id, addr)
    sub_id = await _live_sub(cus)
    d = _dispute(pi)
    ev = event("charge.dispute.created", d)
    await _handle(ev)
    await _handle(ev)  # replayed
    alerts = [m for m in _mails("ops@example.com") if d["id"] in m.subject]
    assert len(alerts) == 1
    assert "£84.98" in alerts[0].text and "fraudulent" in alerts[0].text and f"Order {order_id}" in alerts[0].text
    assert "Evidence due by" in alerts[0].text and addr not in alerts[0].text
    audit = [a for a in _audit("dispute.created", acc_id) if d["id"] in a.detail]
    assert len(audit) == 1
    assert _order_status(order_id) == ("paid", 0) and _refunds(acc_id) == []
    with session_scope() as db:
        assert db.exec(select(Subscription).where(Subscription.stripe_subscription_id == sub_id)).one().status == "active"
    assert _mails(addr, "refund") == []


async def test_each_dispute_event_is_recorded_and_closed_is_told(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_alert_email", "ops@example.com")
    acc_id, addr, _cus = _account()
    _order_id, pi = _order(acc_id, addr)
    d = _dispute(pi)
    await _handle(event("charge.dispute.created", d))
    await _handle(event("charge.dispute.closed", d | {"status": "lost"}))
    assert len(_audit("dispute.closed", acc_id)) == 1
    assert any("closed" in m.subject and "lost" in m.text for m in _mails("ops@example.com") if d["id"] in m.subject)


async def test_dispute_without_alert_address_is_still_audited(monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "operator_alert_email", "")
    d = _dispute(None)
    await _handle(event("charge.dispute.created", d))
    assert any(d["id"] in a.detail for a in _audit("dispute.created"))
    assert any(d["id"] in r.getMessage() and r.levelname == "ERROR" for r in caplog.records)


async def test_failed_dispute_email_is_retried(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_alert_email", "ops@example.com")
    d = _dispute(None)
    monkeypatch.setattr(email, "get_sender", lambda: FailingSender())
    await _handle(event("charge.dispute.created", d))
    monkeypatch.undo()
    enable_billing(monkeypatch)
    monkeypatch.setattr(get_settings(), "operator_alert_email", "ops@example.com")
    assert await email_outbox.deliver_due(utcnow() + timedelta(minutes=2)) == 1
    assert any(d["id"] in m.subject for m in _mails("ops@example.com"))


# --- customer.subscription: the cancellation confirmation --------------------------------------------------------


CANCELLED = "ola Care subscription"


def _cancel_mails(addr: str) -> list[email.Email]:
    return [m for m in _mails(addr, CANCELLED) if "is cancelled" in m.subject or "has ended" in m.subject]


async def test_subscription_deleted_sends_one_email_and_ends_the_plan():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    ev = event("customer.subscription.deleted", _sub(sub_id, cus, "canceled", ended_at=int(time.time())))
    await _handle(ev)
    await _handle(ev)  # replayed
    await _handle(event("customer.subscription.deleted", _sub(sub_id, cus, "canceled")))  # repeated, new id
    mails = _cancel_mails(addr)
    assert len(mails) == 1 and "has ended" in mails[0].subject and mails[0].html
    with session_scope() as db:
        row = db.exec(select(Subscription).where(Subscription.stripe_subscription_id == sub_id)).one()
        assert row.status == "canceled" and not billing.is_entitled(row)


async def test_set_to_cancel_then_ended_is_one_email_in_total():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    ends = int(time.time()) + 20 * 86400
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at_period_end=True)))
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at_period_end=True)))  # any later change
    mails = _cancel_mails(addr)
    assert len(mails) == 1 and "is cancelled" in mails[0].subject
    assert datetime.fromtimestamp(ends, tz=timezone.utc).strftime("%d %B %Y") in mails[0].text
    await _handle(event("customer.subscription.deleted", _sub(sub_id, cus, "canceled")))
    assert len(_cancel_mails(addr)) == 1


async def test_cancel_at_from_the_portal_counts_and_its_date_is_used():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    at = int(time.time()) + 5 * 86400
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at=at)))
    mails = _cancel_mails(addr)
    assert len(mails) == 1 and datetime.fromtimestamp(at, tz=timezone.utc).strftime("%d %B %Y") in mails[0].text


async def test_taking_the_cancellation_back_allows_a_new_one():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at_period_end=True)))
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus)))  # renewed in the portal
    assert len(_cancel_mails(addr)) == 1
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at_period_end=True)))
    assert len(_cancel_mails(addr)) == 2


async def test_stale_event_older_than_the_email_does_not_re_arm():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at_period_end=True)))
    stale = event("customer.subscription.updated", _sub(sub_id, cus)) | {"created": int(time.time()) - 3600}
    await _handle(stale)
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at_period_end=True)))
    assert len(_cancel_mails(addr)) == 1


async def test_no_cancellation_email_for_a_deleted_account_or_a_normal_update():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    await _handle(event("customer.subscription.updated", _sub(sub_id, cus)))
    assert _cancel_mails(addr) == []
    with session_scope() as db:
        accounts.delete_account(db, db.get(Account, acc_id), cancel_stripe=False)
    await _handle(event("customer.subscription.deleted", _sub(sub_id, cus, "canceled")))
    assert _cancel_mails(addr) == []


# --- invoice.upcoming ------------------------------------------------------------------------------------------


def _upcoming(customer: str, sub_id: str, amount: int = 799, attempt_in_days: int = 7) -> dict:
    return {"customer": customer, "amount_due": amount, "currency": "gbp", "next_payment_attempt": int(time.time()) + attempt_in_days * 86400,
            "parent": {"subscription_details": {"subscription": sub_id}}}


async def test_renewal_reminder_states_amount_date_and_how_to_cancel():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    inv = _upcoming(cus, sub_id)
    await _handle(event("invoice.upcoming", inv))
    await _handle(event("invoice.upcoming", inv))  # sent again by Stripe
    mails = _mails(addr, "renews soon")
    assert len(mails) == 1
    date = datetime.fromtimestamp(inv["next_payment_attempt"], tz=timezone.utc).strftime("%d %B %Y")
    for body in (mails[0].text, mails[0].html):
        assert "£7.99" in body and date in body and "Manage subscription" in body
    assert "compl" not in mails[0].text.lower()  # states facts, claims nothing


@pytest.mark.parametrize("case", ["trialing", "set_to_cancel", "free", "unknown_customer"])
async def test_no_renewal_reminder(case):
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus, "trialing" if case == "trialing" else "active")
    if case == "set_to_cancel":
        await _handle(event("customer.subscription.updated", _sub(sub_id, cus, cancel_at_period_end=True)))
    inv = _upcoming(cus, sub_id, amount=0 if case == "free" else 799)
    if case == "unknown_customer":
        inv = _upcoming("cus_nobody_here", "sub_nobody")
    await _handle(event("invoice.upcoming", inv))
    assert _mails(addr, "renews soon") == []


async def test_renewal_reminder_with_the_older_invoice_shape():
    acc_id, addr, cus = _account()
    sub_id = await _live_sub(cus)
    await _handle(event("invoice.upcoming", {"customer": cus, "amount_due": 799, "currency": "gbp", "subscription": sub_id,
                                             "period_end": int(time.time()) + 86400}))
    assert len(_mails(addr, "renews soon")) == 1


# --- trial_ending ----------------------------------------------------------------------------------------------


async def test_trial_ending_amount_comes_from_the_configured_price():
    acc_id, addr, cus = _account()
    end = int(time.time()) + 3 * 86400
    await _handle(event("customer.subscription.trial_will_end", _sub(f"sub_{secrets.token_hex(4)}", cus, "trialing", trial_end=end)))
    mail = _mails(addr, "free period ends")[0]
    assert f"£{CARE_PENCE / 100:.2f} per month" in mail.text and "£7.99 per month" in mail.html


async def test_trial_ending_amount_follows_the_configured_price_when_it_changes(monkeypatch):
    from app import care_terms

    care_terms.clear_cache()
    monkeypatch.setattr(care_terms, "_fetch_price", lambda _p: {"unit_amount": 1049, "currency": "gbp", "recurring": {"interval": "month"}})
    acc_id, addr, cus = _account()
    await _handle(event("customer.subscription.trial_will_end", _sub(f"sub_{secrets.token_hex(4)}", cus, "trialing", trial_end=int(time.time()) + 86400)))
    care_terms.clear_cache()
    assert "£10.49 per month" in _mails(addr, "free period ends")[0].text


async def test_trial_ending_prefers_the_price_on_the_subscription():
    acc_id, addr, cus = _account()
    sub = _sub(f"sub_{secrets.token_hex(4)}", cus, "trialing", trial_end=int(time.time()) + 86400)
    sub["items"]["data"][0]["price"] = {"unit_amount": 699, "currency": "gbp", "recurring": {"interval": "month"}}
    await _handle(event("customer.subscription.trial_will_end", sub))
    assert "£6.99 per month" in _mails(addr, "free period ends")[0].text


async def test_trial_ending_is_not_sent_twice_on_a_repeat():
    acc_id, addr, cus = _account()
    sub = _sub(f"sub_{secrets.token_hex(4)}", cus, "trialing", trial_end=int(time.time()) + 86400)
    await _handle(event("customer.subscription.trial_will_end", sub))
    await _handle(event("customer.subscription.trial_will_end", sub))
    assert len(_mails(addr, "free period ends")) == 1


# --- templates -------------------------------------------------------------------------------------------------


def test_new_templates_have_text_and_html_and_escape():
    when = datetime(2027, 3, 1, tzinfo=timezone.utc)
    for mail in (
        email.trial_ending("a@x", when, 799, "gbp"),
        email.renewal_reminder("a@x", 799, "gbp", when),
        email.care_cancelled("a@x", when, ended=True),
        email.care_cancelled("a@x", when, ended=False),
        email.refund_confirmed("a@x", 500, "gbp", "your ola order #1"),
        email.care_price_change("a@x", 799, 899, "gbp", when),
    ):
        assert mail.text and mail.subject and mail.html and mail.html.startswith("<!doctype html>")
    assert "<b>" not in email.refund_confirmed("a@x", 500, "gbp", "<b>order</b>").html


# --- price change notice ---------------------------------------------------------------------------------------


async def _price_recipients() -> list[int]:
    with session_scope() as db:
        return [a.id for a in email_outbox.price_notice_recipients(db)]


async def test_price_notice_only_reaches_those_who_will_renew_and_is_not_automatic():
    live_id, live_addr, live_cus = _account()
    gone_id, gone_addr, gone_cus = _account()
    none_id, none_addr, _none_cus = _account()
    await _live_sub(live_cus)
    gone_sub = await _live_sub(gone_cus)
    await _handle(event("customer.subscription.updated", _sub(gone_sub, gone_cus, cancel_at_period_end=True)))
    ids = await _price_recipients()
    assert live_id in ids and gone_id not in ids and none_id not in ids
    assert _mails(live_addr, "price of ola Care") == []  # nothing happens by itself

    effective = utcnow() + timedelta(days=31)
    with session_scope() as db:
        queued = email_outbox.queue_price_notice(db, 799, 899, effective, "tester")
    assert queued >= 1
    while await email_outbox.deliver_due():
        pass
    mails = _mails(live_addr, "price of ola Care")
    assert len(mails) == 1 and "£7.99" in mails[0].text and "£8.99" in mails[0].text and effective.strftime("%d %B %Y") in mails[0].text
    assert mails[0].html and "Manage subscription" in mails[0].text
    assert _mails(gone_addr, "price of ola Care") == []
    with session_scope() as db:  # run again: nobody is told twice
        assert email_outbox.queue_price_notice(db, 799, 899, effective, "tester") == 0
    assert any("799 to 899" in a.detail for a in _audit("price_change.notice"))


def test_price_notice_refuses_less_than_30_days_notice():
    with session_scope() as db:
        with pytest.raises(ValueError, match="30 days"):
            email_outbox.queue_price_notice(db, 799, 899, utcnow() + timedelta(days=29), "tester")
        with pytest.raises(ValueError):
            email_outbox.queue_price_notice(db, 799, 799, utcnow() + timedelta(days=40), "tester")


async def test_price_notice_cli_is_a_dry_run_without_send(capsys):
    _acc_id, addr, cus = _account()
    await _live_sub(cus)
    day = (utcnow() + timedelta(days=40)).strftime("%Y-%m-%d")
    with session_scope() as db:
        cli._price_change_notice(db, 799, 999, day, send=False)
        assert db.exec(select(EmailOutbox).where(col(EmailOutbox.dedupe_key).contains(f"price-change:{day}:999"))).first() is None
    assert "Dry run" in capsys.readouterr().out
    with session_scope() as db:
        cli._price_change_notice(db, 799, 999, day, send=True)
        assert db.exec(select(EmailOutbox).where(col(EmailOutbox.dedupe_key).contains(f"price-change:{day}:999"))).first() is not None
        with pytest.raises(cli.OperatorError):
            cli._price_change_notice(db, 799, 999, "next week", send=True)
        with pytest.raises(cli.OperatorError):
            cli._price_change_notice(db, 799, 999, utcnow().strftime("%Y-%m-%d"), send=True)
        for row in db.exec(select(EmailOutbox).where(EmailOutbox.status == "pending")).all():
            db.delete(row)
        db.commit()


# --- the outbox ------------------------------------------------------------------------------------------------


def _outbox(key_part: str) -> EmailOutbox | None:
    with session_scope() as db:
        return db.exec(select(EmailOutbox).where(col(EmailOutbox.dedupe_key).contains(key_part))).first()


async def test_webhook_email_that_fails_is_retried_and_sent_once(monkeypatch, caplog):
    acc_id, addr, _cus = _account()
    _order_id, pi = _order(acc_id, addr)
    ch = f"ch_{secrets.token_hex(5)}"
    with monkeypatch.context() as m:
        m.setattr(email, "get_sender", lambda: FailingSender())
        assert await _handle(event("charge.refunded", _charge(pi, 1000, charge_id=ch))) is True  # the event still succeeds
        # While it is broken, nothing is due before the backoff ends.
        assert await email_outbox.deliver_due() == 0
    assert [r.amount_pence for r in _refunds(acc_id)] == [-1000]
    row = _outbox(ch)
    assert row.status == "pending" and row.attempts == 1 and row.last_error == "OSError"
    assert "secret-detail" not in caplog.text  # the provider's message may quote addresses
    assert _mails(addr, "refund") == []
    assert await email_outbox.deliver_due(utcnow() + timedelta(minutes=2)) == 1
    assert len(_mails(addr, "refund")) == 1
    row = _outbox(ch)
    assert row.status == "sent" and row.text_body == "" and row.html_body is None and row.to_addr == ""
    assert await email_outbox.deliver_due(utcnow() + timedelta(days=1)) == 0
    # A replay of the event after all that sends nothing more.
    await _handle(event("charge.refunded", _charge(pi, 1000, charge_id=ch)))
    assert len(_mails(addr, "refund")) == 1


async def test_row_left_by_a_crash_is_sent_after_its_lease():
    acc_id, addr, _cus = _account()
    with session_scope() as db:
        row_id = email_outbox.enqueue(db, email.payment_failed(addr), f"crash:{secrets.token_hex(4)}", acc_id)
    assert row_id and await email_outbox.deliver_due() == 0  # the lease: someone may be sending it now
    assert await email_outbox.deliver_due(utcnow() + email_outbox.LEASE + timedelta(seconds=1)) == 1
    assert len(_mails(addr, "Payment problem")) == 1


async def test_outbox_gives_up_clears_the_body_and_leaves_a_trace(monkeypatch):
    acc_id, addr, _cus = _account()
    key = f"giveup:{secrets.token_hex(4)}"
    monkeypatch.setattr(email, "get_sender", lambda: FailingSender())
    with session_scope() as db:
        email_outbox.enqueue(db, email.payment_failed(addr), key, acc_id, now_due=True)
    now = utcnow()
    for _ in range(email_outbox.MAX_ATTEMPTS):
        now += timedelta(days=2)
        assert await email_outbox.deliver_due(now) == 1
    assert await email_outbox.deliver_due(now + timedelta(days=9)) == 0
    row = _outbox(key)
    assert row.status == "gave_up" and row.text_body == "" and row.to_addr == "" and row.attempts == email_outbox.MAX_ATTEMPTS
    assert any(key in a.detail for a in _audit("email.gave_up", acc_id))


async def test_two_passes_cannot_take_the_same_row():
    acc_id, addr, _cus = _account()
    with session_scope() as db:
        email_outbox.enqueue(db, email.payment_failed(addr), f"race:{secrets.token_hex(4)}", acc_id, now_due=True)
    now = utcnow()
    assert await email_outbox.deliver_due(now) == 1
    assert await email_outbox.deliver_due(now) == 0
    assert len(_mails(addr, "Payment problem")) == 1


async def test_account_deletion_removes_its_queued_emails(monkeypatch):
    acc_id, addr, _cus = _account()
    k1, k2 = f"del:{secrets.token_hex(4)}", f"del:{secrets.token_hex(4)}"
    with session_scope() as db:
        email_outbox.enqueue(db, email.payment_failed(addr), k1, acc_id, now_due=True)
        email_outbox.enqueue(db, email.order_payment_failed(addr), k2, None, now_due=True)  # known by address only
        accounts.delete_account(db, db.get(Account, acc_id), cancel_stripe=False)
    assert _outbox(k1) is None and _outbox(k2) is None


async def test_email_for_an_account_deleted_meanwhile_is_dropped():
    acc_id, addr, _cus = _account()
    key = f"late:{secrets.token_hex(4)}"
    with session_scope() as db:
        email_outbox.enqueue(db, email.payment_failed(addr), key, acc_id, now_due=True)
        acc = db.get(Account, acc_id)
        acc.status = "deleted"
        db.add(acc)
        db.commit()
    await email_outbox.deliver_due()
    assert _outbox(key) is None and _mails(addr, "Payment problem") == []


async def test_paid_order_emails_survive_a_broken_mail_server(monkeypatch):
    """The welcome email is the only way in for a new customer: it must get there once the server is back."""
    c = _client()
    checkout = FakeCheckout(monkeypatch)
    addr, cus = f"late-{secrets.token_hex(4)}@example.com", f"cus_{secrets.token_hex(4)}"
    cs_id = start_checkout(c, checkout)
    with monkeypatch.context() as m:
        m.setattr(email, "get_sender", lambda: FailingSender())
        with session_scope() as db:
            await billing.handle_event(db, event("checkout.session.completed", session_obj(cs_id, addr, cus)), fetch_payment_intent=pi_fetch())
    assert _mails(addr) == []
    assert await email_outbox.deliver_due(utcnow() + timedelta(minutes=2)) == 2
    subjects = [m.subject for m in _mails(addr)]
    assert any("set your password" in s for s in subjects) and any("order" in s for s in subjects)
    assert _activation(addr).status == "awaiting_pairing"


def test_finished_rows_are_pruned_after_the_window():
    fresh, old = f"prune:{secrets.token_hex(4)}", f"prune:{secrets.token_hex(4)}"
    pend = f"prune:{secrets.token_hex(4)}"
    now = utcnow()
    with session_scope() as db:
        db.add(EmailOutbox(dedupe_key=fresh, to_addr="", subject="s", status="sent"))
        db.add(EmailOutbox(dedupe_key=old, to_addr="", subject="s", status="sent", created_at=now - timedelta(days=91)))
        db.add(EmailOutbox(dedupe_key=pend, to_addr="a@x", subject="s", status="pending", created_at=now - timedelta(days=91),
                           next_attempt_at=now + timedelta(days=1)))
        db.commit()
    assert retention.prune_email_outbox(retention.RetentionPolicy.from_settings(), now) >= 1
    assert _outbox(fresh) is not None and _outbox(old) is None and _outbox(pend) is not None
    with session_scope() as db:
        db.delete(db.exec(select(EmailOutbox).where(EmailOutbox.dedupe_key == pend)).one())
        db.commit()
