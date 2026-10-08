"""AI usage operations (app/usage_ops.py): admission by AI interactions, lease, idempotent costs, settlement
and recovery. Runs on SQLite by default and on PostgreSQL with BUDDYAI_DATABASE_URL (tests/pg adds the
multi-process cases)."""

from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from app import allowance as allowance_mod
from app import billing, entitlements, usage_notices, usage_ops
from app.db.models import Account, PricingRule, TopUp, UsageNotice, UsageOperation, UsageRecord, utcnow
from app.db.session import session_scope
from app.money import usd_to_micro_gbp
from app.providers.base import UsageItem
from tests.test_billing_v2 import _account, _grant, _limit, _spend, _use, enforce  # noqa: F401 - fixture

PROV, MODEL, UNIT = "testprov", "m1", "unit"
PRICE_USD = 0.04  # one unit = £0.03 at the default 0.75 rate


@pytest.fixture(autouse=True)
def _price():
    with session_scope() as db:
        if not db.exec(select(PricingRule).where(PricingRule.provider == PROV)).first():
            db.add(PricingRule(provider=PROV, model=MODEL, unit=UNIT, price_usd=PRICE_USD))
            db.commit()


UNIT_MICRO = usd_to_micro_gbp(PRICE_USD, __import__("decimal").Decimal("0.75"))


def item(q: float = 1.0, kind: str = "llm") -> UsageItem:
    return UsageItem(kind, PROV, MODEL, UNIT, q)


def req(acc_id: int | None, key: str | None = None, device: str = "dev-1", fp: str = "fp", **kw):
    return usage_ops.AdmitRequest(device_id=device, request_key=f"r:{key or secrets.token_hex(8)}",
                                  request_kind="client", kind="chat", account_id=acc_id, fingerprint=fp, **kw)


def op_row(op_id: int) -> UsageOperation:
    with session_scope() as db:
        op = db.get(UsageOperation, op_id)
        db.expunge(op)
        return op


def records(op_id: int) -> list[UsageRecord]:
    with session_scope() as db:
        rows = db.exec(select(UsageRecord).where(UsageRecord.operation_id == op_id)).all()
        for r in rows:
            db.expunge(r)
        return list(rows)


def expire_lease(op_id: int) -> None:
    with session_scope() as db:
        op = db.get(UsageOperation, op_id)
        op.lease_expires_at = utcnow() - timedelta(seconds=5)
        db.add(op)
        db.commit()


def paid_account(allowance_pence: int | None = None) -> int:
    acc_id = _account()
    _grant(acc_id, allowance_pence=allowance_pence)
    return acc_id


# --- admission ------------------------------------------------------------------------------------------


def test_same_request_is_one_operation(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc, "same"))
    assert a.allowed
    again = usage_ops.admit(req(acc, "same"))  # resent by the watch: never a second operation
    assert not again.allowed and again.code == "duplicate" and again.duplicate == "active"
    usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True)
    done = usage_ops.admit(req(acc, "same"))
    assert done.duplicate == "finished" and done.finished_status == "completed"
    with session_scope() as db:
        assert len(db.exec(select(UsageOperation).where(UsageOperation.account_id == acc)).all()) == 1


def test_lost_reply_after_commit_returns_the_same_operation(enforce):
    acc = paid_account()
    r = req(acc, "lost")
    first = usage_ops.admit(r)
    retry = usage_ops.admit(r)  # same exec token: our own admit retried after its reply was lost
    assert retry.allowed and retry.op_id == first.op_id and retry.exec_token == first.exec_token


def test_identical_utterances_are_two_operations(enforce):
    acc = paid_account()
    a, b = usage_ops.admit(req(acc)), usage_ops.admit(req(acc))
    assert a.allowed and b.allowed and a.op_id != b.op_id


def test_request_id_reused_for_another_request_conflicts(enforce):
    acc = paid_account()
    assert usage_ops.admit(req(acc, "k", fp="chat")).allowed
    c = usage_ops.admit(req(acc, "k", fp="note-7"))
    assert not c.allowed and c.code == "request_conflict"


def test_request_keys_are_per_account(enforce):
    a1, a2 = paid_account(), paid_account()
    assert usage_ops.admit(req(a1, "shared-id")).allowed
    other = usage_ops.admit(req(a2, "shared-id"))  # another account's id (forged or not) changes nothing
    assert other.allowed


def test_last_interaction_one_admitted_others_busy_or_limit(enforce):
    acc = paid_account()
    _limit(acc, 5)
    _use(acc, 4)  # room for one more
    first = usage_ops.admit(req(acc, device="w1"))
    second = usage_ops.admit(req(acc, device="w2"))
    assert first.allowed and second.code == "busy_concurrent"
    assert "other conversations" in second.message
    usage_ops.settle(first.op_id, first.exec_token, status="completed", billable=True, items=[(0, item(2))],
                     interaction=True)
    third = usage_ops.admit(req(acc, device="w2"))
    assert not third.allowed and third.code == "limit_reached"  # 5 of 5


def test_accounts_are_independent(enforce):
    a1, a2 = paid_account(), paid_account()
    _limit(a1, 3)
    _use(a1, 3)
    assert usage_ops.admit(req(a1)).code == "limit_reached"
    assert usage_ops.admit(req(a2)).allowed


def test_internal_unenforced_and_unowned_are_recorded_without_budget(enforce):
    internal = _account(internal=True)
    _spend(internal, 100)
    a = usage_ops.admit(req(internal))
    assert a.allowed and op_row(a.op_id).source == "internal" and op_row(a.op_id).reserved_micro == 0
    u = usage_ops.admit(req(None, device="stock-1"))
    assert u.allowed and op_row(u.op_id).source == "unowned"
    free = usage_ops.admit(req(paid_account(), budget=False))
    assert free.allowed and op_row(free.op_id).source == "unenforced"


def test_no_subscription_and_inactive_account(enforce):
    acc = _account()
    assert usage_ops.admit(req(acc)).code == "subscription_required"
    with session_scope() as db:
        a = db.get(Account, acc)
        a.status = "suspended"
        db.add(a)
        db.commit()
    assert usage_ops.admit(req(acc)).code == "account_inactive"


def test_database_unavailable_refuses(enforce, monkeypatch):
    acc = paid_account()

    def down():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(usage_ops, "billing_scope", down)
    a = usage_ops.admit(req(acc))
    assert not a.allowed and a.code == "service_unavailable"


def test_admission_paused_drains(enforce):
    acc = paid_account()
    with session_scope() as db:
        from app.db.models import BillingSettings

        row = db.get(BillingSettings, 1)
        row.admission_paused = True
        db.add(row)
        db.commit()
    try:
        assert usage_ops.admit(req(acc)).code == "service_unavailable"
    finally:
        with session_scope() as db:
            row = db.get(BillingSettings, 1)
            row.admission_paused = False
            db.add(row)
            db.commit()


# --- costs ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("units", [0.5, 1.0, 300.0])  # cheap / normal / very expensive: always one interaction
def test_cost_is_recorded_and_one_request_is_one_interaction(enforce, units):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    assert op_row(a.op_id).reserved_micro == 0  # no money reservation any more
    with session_scope() as db:
        assert entitlements.allowance(db, db.get(Account, acc)).active == 1
    view = usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, items=[(0, item(units))],
                            interaction=True)
    assert view.state == "settled" and view.cost_certainty == "exact" and view.interaction is True
    assert view.recorded_cost_micro == records(a.op_id)[0].cost_micro_gbp == round(units * UNIT_MICRO)
    with session_scope() as db:
        al = entitlements.allowance(db, db.get(Account, acc))
    assert (al.used, al.active) == (1, 0)


def test_intermediate_costs_are_written_once(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    items = [(0, item(1, "stt")), (1, item(2))]
    assert usage_ops.record_costs(a.op_id, items[:1]) == 1
    assert usage_ops.record_costs(a.op_id, items) == 1  # only the new one
    assert usage_ops.record_costs(a.op_id, items) == 0  # a repeat changes nothing
    usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, items=items)
    usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, items=items)  # idempotent
    rows = records(a.op_id)
    assert len(rows) == 2 and all(r.billable for r in rows)
    assert op_row(a.op_id).recorded_cost_micro == 3 * UNIT_MICRO


def test_cost_never_limits_admission(enforce):
    acc = paid_account()
    _spend(acc, 40.00)  # far beyond every internal cost threshold
    a = usage_ops.admit(req(acc))
    usage_ops.record_costs(a.op_id, [(0, item(400))])  # an expensive answer still running
    assert usage_ops.admit(req(acc)).allowed  # money is monitored, never enforced
    with session_scope() as db:
        assert entitlements.allowance(db, db.get(Account, acc)).used == 0


def test_error_turn_keeps_cost_not_billable(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    view = usage_ops.settle(a.op_id, a.exec_token, status="error", billable=False, items=[(0, item(2))])
    assert view.billable is False and records(a.op_id)[0].billable is False and view.interaction is False
    with session_scope() as db:
        assert entitlements.allowance(db, db.get(Account, acc)).used == 0


def test_nothing_spent_is_cancelled(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    assert usage_ops.settle(a.op_id, a.exec_token, status="error", billable=False, reason="start_failed").state == "cancelled"


def test_unpriced_cost_is_flagged(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    view = usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True,
                            items=[(0, UsageItem("llm", PROV, "no-price", UNIT, 5))])
    assert view.cost_certainty == "unpriced"


# --- lease, expiry, recovery ------------------------------------------------------------------------------


def test_heartbeat_extends_until_expired(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    assert usage_ops.mark_running(a.op_id, a.exec_token)
    assert op_row(a.op_id).state == "running"
    assert usage_ops.heartbeat(a.op_id, a.exec_token)
    assert not usage_ops.heartbeat(a.op_id, "someone-else")
    expire_lease(a.op_id)
    assert not usage_ops.heartbeat(a.op_id, a.exec_token)  # an expired lease is never revived


def test_recovery_expires_and_is_not_billed(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    usage_ops.mark_running(a.op_id, a.exec_token)
    usage_ops.record_costs(a.op_id, [(0, item(2))])
    expire_lease(a.op_id)
    assert usage_ops.recover_expired() >= 1
    op = op_row(a.op_id)
    assert op.state == "expired" and op.billable is False and op.cost_certainty == "uncertain"
    assert op.reason == "lease_expired" and records(a.op_id)[0].billable is False and op.interaction is False
    # the executing process comes back late: its settle cannot revive, bill or count it
    view = usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, items=[(0, item(2)), (1, item(1))],
                            interaction=True)
    assert view.state == "expired" and view.billable is False and view.interaction is False
    assert len(records(a.op_id)) == 2 and not any(r.billable for r in records(a.op_id))  # late cost kept
    assert usage_ops.recover_expired() == 0  # never twice


def test_recovery_of_an_unstarted_operation_is_exact(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    expire_lease(a.op_id)
    usage_ops.recover_expired()
    assert op_row(a.op_id).cost_certainty == "exact"


def test_settle_before_recovery_wins(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, items=[(0, item(1))])
    expire_lease(a.op_id)  # irrelevant now
    usage_ops.recover_expired()
    assert op_row(a.op_id).state == "settled" and op_row(a.op_id).billable is True


# --- periods, top-ups, limits ------------------------------------------------------------------------------


def test_late_cost_stays_in_its_period(enforce):
    acc = paid_account()
    a = usage_ops.admit(req(acc))
    old_key = op_row(a.op_id).period_key
    usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, items=[(0, item(1))])
    with session_scope() as db:  # a new period starts (new grant)
        billing.revoke_complimentary(db, db.get(Account, acc), "t")
    _grant(acc)
    usage_ops.record_costs(a.op_id, [(1, item(2))])  # a provider reports late
    assert {r.period_key for r in records(a.op_id)} == {old_key}
    with session_scope() as db:
        al = entitlements.allowance(db, db.get(Account, acc))
        new_key = allowance_mod.period_identity(billing.active_subscription(db, acc)).key
    assert new_key != old_key and al.used == 0


def test_period_keys_are_stable():
    from datetime import datetime, timezone

    from app.db.models import Subscription

    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    sub = Subscription(id=7, source="stripe", status="active", current_period_start=start,
                       current_period_end=datetime(2026, 10, 10, tzinfo=timezone.utc))
    k1 = allowance_mod.period_identity(sub, datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert k1.key == f"stripe:7:{int(start.timestamp())}"
    late = allowance_mod.period_identity(sub, datetime(2026, 10, 12, tzinfo=timezone.utc))  # renewal not here yet
    assert late.key == f"stripe:7:{int(datetime(2026, 10, 10, tzinfo=timezone.utc).timestamp())}"
    assert allowance_mod.period_identity(None, datetime(2026, 9, 20, tzinfo=timezone.utc)).key == "cal:2026-09"


def test_topup_and_refund_change_admission(enforce):
    acc = paid_account()
    _limit(acc, 2)
    _use(acc, 2)
    assert usage_ops.admit(req(acc)).code == "limit_reached"
    with session_scope() as db:
        sub = billing.active_subscription(db, acc)
        pid = allowance_mod.period_identity(sub)
        t = TopUp(account_id=acc, amount_pence=199, allowance_pence=0, interactions=1, period_start=pid.period.start,
                  period_end=pid.period.end, period_key=pid.key, status="paid", stripe_payment_intent=f"pi_{secrets.token_hex(3)}")
        db.add(t)
        db.commit()
        intent = t.stripe_payment_intent
    a = usage_ops.admit(req(acc))
    assert a.allowed
    usage_ops.settle(a.op_id, a.exec_token, status="aborted", billable=True)  # e.g. cancelled before understood
    assert usage_ops.admit(req(acc)).allowed  # not counted: the top-up's interaction is still there
    with session_scope() as db:
        billing._refund(db, {"payment_intent": intent, "refunded": True, "id": "ch_1", "amount_refunded": 199})
    assert usage_ops.admit(req(acc)).code == "limit_reached"


def test_limit_reduced_while_running(enforce):
    acc = paid_account()
    _use(acc, 3)
    a = usage_ops.admit(req(acc))
    _limit(acc, 3)  # an operator lowers the account's interactions
    assert usage_ops.admit(req(acc)).code == "limit_reached"
    # the running turn still finishes and counts (a started answer is never cut off)
    view = usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, items=[(0, item(1))],
                            interaction=True)
    assert view.state == "settled" and view.interaction


def test_threshold_notices_once(enforce):
    import asyncio

    acc = paid_account()
    _limit(acc, 10)
    _use(acc, 8)
    for _ in range(3):
        asyncio.run(usage_notices.evaluate(acc))
    with session_scope() as db:
        rows = db.exec(select(UsageNotice).where(UsageNotice.account_id == acc)).all()
    assert [r.threshold for r in rows] == [80]


# --- non-turn operations ------------------------------------------------------------------------------------


def test_free_operation_is_recorded_not_billed(enforce):
    acc = paid_account()
    _limit(acc, 2)
    _use(acc, 2)  # even with no interactions left: a voice sample is free
    view = usage_ops.record_free_operation("voice_sample", "", acc, [item(1, "tts")])
    assert view is not None and view.billable is False and view.state == "settled" and view.interaction is False
    with session_scope() as db:
        assert entitlements.allowance(db, db.get(Account, acc)).used == 2
