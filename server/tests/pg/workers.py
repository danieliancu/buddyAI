"""Code that runs in separate (spawned) processes for the multi-process usage tests.

Each process gets its own interpreter, engine and connection pool - like separate server workers or hosts.
Nothing from app/ is imported at module level: the environment (database URL, lease) is set first.
"""

from __future__ import annotations

import os
import secrets
import time
import traceback
from typing import Any


def entry(name: str, env: dict[str, str], args: tuple, out, barrier=None) -> None:
    """Process target: set the environment, run workers.<name>(*args), put ("ok", result) or ("err", tb)."""
    os.environ.update(env)
    try:
        fn = globals()[name]
        result = fn(*args, barrier=barrier) if barrier is not None else fn(*args)
        out.put((name, "ok", result))
    except BaseException:  # noqa: BLE001
        out.put((name, "err", traceback.format_exc()))


# --- setup ------------------------------------------------------------------------------------------------


def reset_schema() -> str:
    from sqlalchemy import text

    from app.db.session import get_engine, is_postgres, run_migrations

    if is_postgres():
        with get_engine().begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    run_migrations()
    from app import plan as plan_mod
    from app.db.models import PricingRule
    from app.db.session import session_scope

    with session_scope() as db:
        plan_mod.update(db, {"enforce": True}, "mp-test")
        db.add(PricingRule(provider="testprov", model="m1", unit="unit", price_usd=0.04))
        db.commit()
    return "ok"


def make_account(used: int = 0, limit: int | None = None) -> int:
    """An account with a complimentary plan, `used` interactions already counted this period and (optionally)
    an interaction override of `limit`."""
    from app import allowance as allowance_mod
    from app import billing
    from app.db.models import Account, UsageOperation
    from app.db.session import session_scope

    with session_scope() as db:
        acc = Account(email=f"mp-{secrets.token_hex(5)}@example.com", interaction_limit_override=limit)
        db.add(acc)
        db.commit()
        db.refresh(acc)
        sub, _ = billing.grant_complimentary(db, acc, 30, "mp-test")
        pid = allowance_mod.period_identity(sub)
        db.add_all([UsageOperation(
            op_uid=secrets.token_hex(16), account_id=acc.id, device_id="d", request_key=f"s:{secrets.token_hex(8)}",
            request_kind="server", request_fingerprint="fp", kind="chat", period_key=pid.key, period_kind=pid.period.kind,
            period_start=pid.period.start, period_end=pid.period.end, source=pid.source, state="settled",
            billable=True, interaction=True, result_status="completed") for _ in range(used)])
        db.commit()
        return acc.id


# --- workers ----------------------------------------------------------------------------------------------


def _req(account_id, key=None, device="mp-dev", fp="fp"):
    from app import usage_ops

    return usage_ops.AdmitRequest(device_id=device, request_key=f"r:{key or secrets.token_hex(8)}",
                                  request_kind="client", kind="chat", account_id=account_id, fingerprint=fp)


def admit(account_id: int, key: str | None = None, device: str = "mp-dev", barrier=None) -> dict[str, Any]:
    from app import usage_ops

    assert usage_ops.database_ready()  # imported and connected before the barrier
    if barrier is not None:
        barrier.wait(30)
    a = usage_ops.admit(_req(account_id, key, device))
    return {"allowed": a.allowed, "code": a.code, "op_id": a.op_id, "token": a.exec_token, "pid": os.getpid()}


def admit_cold(account_id: int) -> dict[str, Any]:
    from app import usage_ops

    a = usage_ops.admit(_req(account_id, device="cold"))
    return {"allowed": a.allowed, "code": a.code}


def admit_holding_lock(account_id: int, hold_s: float, flag_path: str) -> dict[str, Any]:
    """Admit, pausing inside the transaction right after the account row is locked (test hook)."""
    from app import usage_ops

    def pause(**_kw):
        open(flag_path, "w").close()
        time.sleep(hold_s)

    usage_ops.HOOKS["admit_locked"] = pause
    a = usage_ops.admit(_req(account_id, device="holder"))
    return {"allowed": a.allowed, "code": a.code, "t_done": time.time()}


def admit_after_flag(account_id: int, flag_path: str) -> dict[str, Any]:
    from app import usage_ops

    while not os.path.exists(flag_path):
        time.sleep(0.01)
    t0 = time.time()
    a = usage_ops.admit(_req(account_id, device="waiter"))
    return {"allowed": a.allowed, "code": a.code, "t_start": t0, "t_done": time.time()}


def run_then_hang(account_id: int, flag_path: str) -> None:
    """Admit, start, record a cost - then hang (the test kills this process like kill -9)."""
    from app import usage_ops
    from app.providers.base import UsageItem

    a = usage_ops.admit(_req(account_id, device="doomed"))
    assert a.allowed
    usage_ops.mark_running(a.op_id, a.exec_token)
    usage_ops.record_costs(a.op_id, [(0, UsageItem("stt", "testprov", "m1", "unit", 1))])
    with open(flag_path, "w") as f:
        f.write(f"{a.op_id}:{a.exec_token}")
    time.sleep(3600)


def recover() -> int:
    from app import usage_ops

    return usage_ops.recover_expired()


def settle(op_id: int, token: str, units: float = 1.0, barrier=None) -> dict[str, Any]:
    from app import usage_ops
    from app.providers.base import UsageItem

    if barrier is not None:
        barrier.wait(30)
    v = usage_ops.settle(op_id, token, status="completed", billable=True,
                         items=[(0, UsageItem("llm", "testprov", "m1", "unit", units))])
    return {"state": v.state if v else None, "billable": v.billable if v else None}


def recover_race(barrier=None) -> int:
    from app import usage_ops

    if barrier is not None:
        barrier.wait(30)
    return usage_ops.recover_expired()


def record(op_id: int, n_items: int, barrier=None) -> int:
    from app import usage_ops
    from app.providers.base import UsageItem

    items = [(i, UsageItem("llm", "testprov", "m1", "unit", 1)) for i in range(n_items)]
    if barrier is not None:
        barrier.wait(30)
    return usage_ops.record_costs(op_id, items)


def refund_topup(account_id: int, barrier=None) -> str:
    from app import billing
    from app.db.session import session_scope

    if barrier is not None:
        barrier.wait(30)
    with session_scope() as db:
        billing._refund(db, {"payment_intent": f"pi_mp_{account_id}", "refunded": True, "id": f"ch_{account_id}",
                             "amount_refunded": 199})
    return "refunded"


def add_paid_topup(account_id: int) -> int:
    from app import allowance as allowance_mod
    from app import billing
    from app.db.models import TopUp
    from app.db.session import session_scope

    with session_scope() as db:
        sub = billing.active_subscription(db, account_id)
        pid = allowance_mod.period_identity(sub)
        t = TopUp(account_id=account_id, amount_pence=199, allowance_pence=0, interactions=1, period_start=pid.period.start,
                  period_end=pid.period.end, period_key=pid.key, status="paid", stripe_payment_intent=f"pi_mp_{account_id}")
        db.add(t)
        db.commit()
        return t.id


def expire(op_id: int) -> None:
    from datetime import timedelta

    from app.db.models import UsageOperation, utcnow
    from app.db.session import session_scope

    with session_scope() as db:
        op = db.get(UsageOperation, op_id)
        op.lease_expires_at = utcnow() - timedelta(seconds=5)
        db.add(op)
        db.commit()


def op_state(op_id: int) -> dict[str, Any]:
    from sqlmodel import select

    from app.db.models import UsageOperation, UsageRecord
    from app.db.session import session_scope

    with session_scope() as db:
        op = db.get(UsageOperation, op_id)
        recs = db.exec(select(UsageRecord).where(UsageRecord.operation_id == op_id)).all()
        return {"state": op.state, "billable": op.billable, "certainty": op.cost_certainty, "reason": op.reason,
                "records": len(recs), "billable_records": sum(1 for r in recs if r.billable),
                "recorded": op.recorded_cost_micro}


def count_ops(account_id: int) -> int:
    from sqlmodel import select

    from app.db.models import UsageOperation
    from app.db.session import session_scope

    with session_scope() as db:
        return len(db.exec(select(UsageOperation).where(UsageOperation.account_id == account_id)).all())
