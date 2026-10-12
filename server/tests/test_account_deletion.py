"""Account deletion: live ola Care subscriptions are cancelled in Stripe first (a Stripe failure deletes
nothing), private data goes, billing records stay, the deletion is audited, and late Stripe webhooks never
email a deleted account. Export includes the billing records."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import stripe
from sqlmodel import select

from app import billing
from app.db.models import (Account, AuditLog, Order, SearchCache, Subscription, Turn, UsageNotice, VoiceOperation)
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from tests.test_accounts import _customer, _give_watch, _no_rate_limits_between_tests  # noqa: F401 - fixture
from tests.test_shop import billing_on  # noqa: F401 - fixture

PASSWORD = {"password": "correct-horse-1"}


def _data(account_id: int, status: str = "active") -> dict:
    """A Stripe subscription, an order, a private search answer, a voice operation and a usage notice."""
    sub_id = f"sub_{secrets.token_hex(6)}"
    key = secrets.token_hex(16)
    with session_scope() as db:
        acc = db.get(Account, account_id)
        acc.stripe_customer_id = f"cus_{secrets.token_hex(6)}"
        db.add(acc)
        db.add(Subscription(stripe_subscription_id=sub_id, stripe_customer_id=acc.stripe_customer_id,
                            account_id=account_id, status=status))
        order = Order(stripe_session_id=f"cs_{secrets.token_hex(6)}", account_id=account_id, email=acc.email,
                      currency="gbp", amount_total=9900, shipping_name="Ana Pop",
                      shipping_address={"line1": "1 High St", "city": "London", "postal_code": "N1 1AA"})
        db.add(order)
        db.add(SearchCache(key=key, scope=f"account:{account_id}", category="other", query="private",
                           answer="private answer", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.add(VoiceOperation(op_key=secrets.token_hex(16), account_id=account_id, device_id="d", session_id="s",
                              turn_id=1, tool="note_edit", summary="note_edit: private"))
        db.add(UsageNotice(account_id=account_id, period_start=datetime.now(timezone.utc), threshold=80))
        db.commit()
        db.refresh(order)
        return {"sub_id": sub_id, "order_id": order.id, "cache_key": key, "customer": acc.stripe_customer_id}


def _fake_cancel(monkeypatch, fail: bool = False) -> list[str]:
    calls: list[str] = []

    def cancel(sub_id, **kw):
        calls.append(sub_id)
        if fail:
            raise stripe.APIConnectionError("network down")
        return {"id": sub_id, "status": "canceled"}

    monkeypatch.setattr(stripe.Subscription, "cancel", staticmethod(cancel))
    return calls


def test_delete_cancels_the_subscription_and_erases_private_data(billing_on, monkeypatch) -> None:  # noqa: F811
    c, me = _customer()
    dev = _give_watch(me["id"])
    d = _data(me["id"])
    calls = _fake_cancel(monkeypatch)
    r = c.request("DELETE", "/api/me", json=PASSWORD)
    assert r.status_code == 200, r.text
    assert calls == [d["sub_id"]]
    with session_scope() as db:
        acc = db.get(Account, me["id"])
        assert acc.status == "deleted" and acc.email.endswith("@deleted.invalid")
        sub = db.exec(select(Subscription).where(Subscription.stripe_subscription_id == d["sub_id"])).one()
        assert sub.status == "canceled"
        assert db.exec(select(SearchCache).where(SearchCache.scope == f"account:{me['id']}")).all() == []
        assert db.exec(select(VoiceOperation).where(VoiceOperation.account_id == me["id"])).all() == []
        assert db.exec(select(UsageNotice).where(UsageNotice.account_id == me["id"])).all() == []
        assert db.exec(select(Turn).where(Turn.device_id == dev)).all() == []
        order = db.get(Order, d["order_id"])  # kept: accounting record
        assert order is not None and order.shipping_name == "Ana Pop"
        audit = db.exec(select(AuditLog).where(AuditLog.account_id == me["id"], AuditLog.action == "account.delete")).all()
        assert len(audit) == 1 and audit[0].actor == f"account:{me['id']}" and audit[0].detail == "self-service"


def test_stripe_failure_deletes_nothing(billing_on, monkeypatch) -> None:  # noqa: F811
    c, me = _customer()
    dev = _give_watch(me["id"])
    d = _data(me["id"])
    calls = _fake_cancel(monkeypatch, fail=True)
    r = c.request("DELETE", "/api/me", json=PASSWORD)
    assert r.status_code == 502
    assert r.json()["detail"] == ("We couldn't cancel your olácare subscription, so nothing was deleted. "
                                  "Please try again, or contact us.")
    assert calls == [d["sub_id"]]
    assert c.get("/api/me").status_code == 200  # still signed in, still active
    with session_scope() as db:
        acc = db.get(Account, me["id"])
        assert acc.status == "active" and acc.email == me["email"]
        sub = db.exec(select(Subscription).where(Subscription.stripe_subscription_id == d["sub_id"])).one()
        assert sub.status == "active"
        assert db.exec(select(Turn).where(Turn.device_id == dev)).all() != []
        assert db.exec(select(SearchCache).where(SearchCache.key == d["cache_key"])).first() is not None
        assert db.exec(select(VoiceOperation).where(VoiceOperation.account_id == me["id"])).all() != []
        assert db.exec(select(AuditLog).where(AuditLog.account_id == me["id"],
                                              AuditLog.action == "account.delete")).all() == []


def test_already_cancelled_subscriptions_are_not_sent_to_stripe(billing_on, monkeypatch) -> None:  # noqa: F811
    c, me = _customer()
    _data(me["id"], status="canceled")
    calls = _fake_cancel(monkeypatch, fail=True)
    assert c.request("DELETE", "/api/me", json=PASSWORD).status_code == 200
    assert calls == []


def test_without_stripe_configured_deletion_does_not_call_stripe(monkeypatch) -> None:
    c, me = _customer()
    _data(me["id"])
    calls = _fake_cancel(monkeypatch, fail=True)
    assert c.request("DELETE", "/api/me", json=PASSWORD).status_code == 200
    assert calls == []


def test_webhook_payment_failed_sends_no_email_to_a_deleted_account(billing_on, monkeypatch) -> None:  # noqa: F811
    c, me = _customer()
    d = _data(me["id"])
    _fake_cancel(monkeypatch)
    assert c.request("DELETE", "/api/me", json=PASSWORD).status_code == 200
    before = len(ConsoleEmailSender.sent)
    event = {"id": f"evt_{secrets.token_hex(6)}", "type": "invoice.payment_failed",
             "data": {"object": {"id": f"in_{secrets.token_hex(4)}", "customer": d["customer"]}}}
    with session_scope() as db:
        assert asyncio.run(billing.handle_event(db, event)) is True
    assert len(ConsoleEmailSender.sent) == before


def test_webhook_payment_failed_still_emails_an_active_account(billing_on) -> None:  # noqa: F811
    _c, me = _customer()
    d = _data(me["id"])
    event = {"id": f"evt_{secrets.token_hex(6)}", "type": "invoice.payment_failed",
             "data": {"object": {"id": f"in_{secrets.token_hex(4)}", "customer": d["customer"]}}}
    with session_scope() as db:
        assert asyncio.run(billing.handle_event(db, event)) is True
    assert ConsoleEmailSender.sent[-1].to == me["email"]


def test_export_includes_billing_records_and_is_audited() -> None:
    c, me = _customer()
    d = _data(me["id"])
    data = c.get("/api/me/export").json()
    for key in ("orders", "subscriptions", "billing_consents", "top_ups", "monthly_usage", "account_events"):
        assert key in data
    assert [o["id"] for o in data["orders"]] == [d["order_id"]]
    assert data["orders"][0]["shipping_address"]["city"] == "London"
    assert data["subscriptions"][0]["status"] == "active"
    with session_scope() as db:
        assert db.exec(select(AuditLog).where(AuditLog.account_id == me["id"], AuditLog.action == "account.export")).first()


def test_memory_clear_is_audited() -> None:
    c, me = _customer()
    r = c.post("/api/me/memories/clear", json=PASSWORD)
    assert r.status_code == 200, r.text
    with session_scope() as db:
        assert db.exec(select(AuditLog).where(AuditLog.account_id == me["id"], AuditLog.action == "memory.clear")).first()
