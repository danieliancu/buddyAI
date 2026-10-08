"""Internal AI cost monitoring (app/cost_monitor.py): thresholds, alerts once per level and period, projections,
background costs, the operator endpoint - and proof that money never limits a customer's interactions."""

from __future__ import annotations

import asyncio
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import allowance as allowance_mod
from app import billing, cost_monitor, usage_ops
from app.db.models import Account, AuditLog, CostAlert, UsageOperation, UsageRecord
from app.db.session import session_scope
from app.main import app
from app.plan import get_plan
from tests.test_billing_v2 import _account, _grant, _use, enforce  # noqa: F401 - fixture


def _cost(acc: int, pounds: float | None, kind: str = "llm", unit: str = "output_token", op_kind: str = "chat",
          interaction: bool = False, mock: bool = False) -> None:
    """A settled operation of the current period with one cost row (None = unpriced)."""
    with session_scope() as db:
        pid = allowance_mod.period_identity(billing.active_subscription(db, acc))
        op = UsageOperation(op_uid=secrets.token_hex(16), account_id=acc, device_id="d", request_key=f"s:{secrets.token_hex(8)}",
                            request_kind="server", request_fingerprint="f", kind=op_kind, period_key=pid.key,
                            period_kind=pid.period.kind, period_start=pid.period.start, period_end=pid.period.end,
                            source=pid.source, state="settled", billable=True, interaction=interaction)
        db.add(op)
        db.flush()
        db.add(UsageRecord(device_id="d", account_id=acc, kind=kind, provider="p", model="m", unit=unit, quantity=1,
                           cost_usd=None if pounds is None else pounds / 0.75,
                           cost_micro_gbp=None if pounds is None else round(pounds * 1_000_000), mock=mock,
                           operation_id=op.id, period_key=pid.key, dedup_key=secrets.token_hex(20)))
        db.commit()


def _view(acc: int) -> dict:
    with session_scope() as db:
        a = db.get(Account, acc)
        plan = get_plan(db)
        (c,) = cost_monitor.account_costs(db, [a], plan)
        return cost_monitor.view(c, plan, a)


def _paid() -> int:
    acc = _account()
    _grant(acc)
    return acc


def _admit(acc: int):
    return usage_ops.admit(usage_ops.AdmitRequest(device_id="d", request_key=f"r:{secrets.token_hex(8)}",
                                                  request_kind="client", kind="chat", account_id=acc, fingerprint="f"))


def test_thresholds_alert_once_and_never_block(enforce):
    acc = _paid()
    assert cost_monitor.evaluate(acc) == [] and _view(acc)["status"] == "within"
    _cost(acc, 2.00)
    assert [a["level"] for a in cost_monitor.evaluate(acc)] == ["approaching"]
    assert _admit(acc).allowed  # monitoring only
    _cost(acc, 0.50)
    assert [a["level"] for a in cost_monitor.evaluate(acc)] == ["over"]
    assert _view(acc)["status"] == "over" and _admit(acc).allowed
    _cost(acc, 2.50)
    assert [a["level"] for a in cost_monitor.evaluate(acc)] == ["critical"]
    assert _admit(acc).allowed  # even critical cost never suspends the customer
    for _ in range(3):
        assert cost_monitor.evaluate(acc) == []  # each level once per period
    with session_scope() as db:
        assert len(db.exec(select(CostAlert).where(CostAlert.account_id == acc)).all()) == 3
        audits = db.exec(select(AuditLog).where(AuditLog.account_id == acc, AuditLog.action == "cost.alert")).all()
        assert len(audits) == 3 and "£5.00" in audits[-1].detail


def test_customer_reaches_all_interactions_despite_high_cost(enforce):
    acc = _paid()
    _use(acc, 999)
    _cost(acc, 6.00)  # well above the £5 critical level
    cost_monitor.evaluate(acc)
    a = _admit(acc)
    assert a.allowed  # the 1,000th is honoured
    usage_ops.settle(a.op_id, a.exec_token, status="completed", billable=True, interaction=True)
    assert _admit(acc).code == "limit_reached"  # refused for interactions, never for money


def test_projection_maths_and_background_costs(enforce):
    acc = _paid()
    _use(acc, 299)
    _cost(acc, 0.60, interaction=True)  # 300 interactions
    _cost(acc, 0.20, kind="stt", unit="audio_second", interaction=False)
    _cost(acc, 0.10, kind="llm", op_kind="memory")  # background learning
    _cost(acc, 9.00, mock=True)  # mock providers are never a cost
    v = _view(acc)
    assert v["interactions"] == 300 and v["cost"] == pytest.approx(0.90)
    assert v["interactive_cost"] == pytest.approx(0.80) and v["background_cost"] == pytest.approx(0.10)
    assert v["average_cost"] == pytest.approx(0.003)
    assert v["projected_cost"] == pytest.approx(3.00) and v["projection"] == "estimate"
    assert v["by_group"] == {"llm": pytest.approx(0.70), "stt": pytest.approx(0.20)}
    assert v["target"] == 2.5 and v["status"] == "within" and v["limit"] == 1000


def test_zero_and_insufficient_data_are_not_projected(enforce):
    acc = _paid()
    v = _view(acc)
    assert v["interactions"] == 0 and v["average_cost"] is None and v["projected_cost"] is None
    assert v["projection"] == "insufficient_data"
    _use(acc, 5)
    _cost(acc, 0.50)
    v = _view(acc)
    assert v["average_cost"] == pytest.approx(0.1) and v["projected_cost"] is None  # 5 < 20: would mislead


def test_requests_from_before_the_count_flag_the_projection(enforce):
    """The period running when 0027 is deployed has costs of requests that were never counted: the average
    would be too high, so the projection is marked incomplete (never presented as exact)."""
    acc = _paid()
    _use(acc, 30)
    _cost(acc, 0.30)
    with session_scope() as db:  # one request settled before migration 0027
        op = db.exec(select(UsageOperation).where(UsageOperation.account_id == acc)).first()
        op.interaction = None
        db.add(op)
        db.commit()
    v = _view(acc)
    assert v["interactions"] == 29 and v["projection"] == "incomplete"


def test_cost_rows_without_an_operation_are_interactive(enforce):
    acc = _paid()
    with session_scope() as db:
        pid = allowance_mod.period_identity(billing.active_subscription(db, acc))
        db.add(UsageRecord(device_id="d", account_id=acc, kind="llm", provider="p", model="m", unit="u", quantity=1,
                           cost_usd=0.4, cost_micro_gbp=300_000, period_key=pid.key, dedup_key=secrets.token_hex(20)))
        db.commit()
    v = _view(acc)
    assert v["interactive_cost"] == pytest.approx(0.30) and v["background_cost"] == 0


def test_cost_thresholds_must_be_positive_and_ordered():
    from tests.test_api import _client as _admin_client

    c = _admin_client()
    assert c.put("/api/billing/settings", json={"cost_warn_pence": 0}).status_code == 422  # would alert everyone
    assert c.put("/api/billing/settings", json={"cost_critical_pence": 100}).status_code == 422  # below the target


def test_unpriced_usage_is_flagged_not_free(enforce):
    acc = _paid()
    _use(acc, 30)
    _cost(acc, 0.30)
    _cost(acc, None)  # no pricing rule
    v = _view(acc)
    assert v["unpriced_rows"] == 1 and v["projection"] == "incomplete" and v["cost"] == pytest.approx(0.30)


def test_operator_event_is_published_operator_only(enforce):
    acc = _paid()
    _cost(acc, 2.60)
    sent = []

    class Hub:
        def publish(self, event, operator_only=False):
            sent.append((event, operator_only))

    asyncio.run(cost_monitor.evaluate_async(Hub(), acc))
    assert [(e["type"], e["level"], op) for e, op in sent] == [("cost_alert", "approaching", True), ("cost_alert", "over", True)]


def test_finance_accounts_endpoint_is_operator_only_filters_and_sorts(enforce):
    from tests.test_api import _client as _admin_client

    cheap, dear = _paid(), _paid()
    _use(cheap, 25)
    _cost(cheap, 0.05)
    _cost(dear, 5.20)
    c = _admin_client()
    body = c.get("/api/finance/accounts").json()
    ids = [r["account_id"] for r in body["accounts"]]
    assert ids.index(dear) < ids.index(cheap)  # most expensive first
    assert body["thresholds"] == {"warn": 2.0, "target": 2.5, "critical": 5.0}
    assert body["status_counts"].get("critical", 0) >= 1
    crit = c.get("/api/finance/accounts", params={"status": "critical"}).json()["accounts"]
    assert dear in [r["account_id"] for r in crit] and cheap not in [r["account_id"] for r in crit]
    row = next(r for r in body["accounts"] if r["account_id"] == cheap)
    assert "***@" in row["account"]  # masked
    assert c.get("/api/finance/accounts", params={"sort": "nope"}).status_code == 422
    with TestClient(app) as anon:
        assert anon.get("/api/finance/accounts").status_code == 401


def test_customer_never_sees_cost_fields(enforce):
    from tests.test_accounts import _customer

    client, me = _customer()
    _grant(me["id"])
    _cost(me["id"], 3.00)
    cost_monitor.evaluate(me["id"])
    for path in ("/api/me/plan", "/api/me/usage", "/api/me/subscription", "/api/me/usage-notice"):
        text = client.get(path).text.lower()
        for word in ("cost", "£2.50", "target", "critical", "approaching", "micro"):
            assert word not in text, (path, word)
