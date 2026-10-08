"""Operator finance: estimated provider cost vs revenue.

Provider costs are the application's estimates (usage x editable pricing rules, frozen in GBP when
recorded), not provider invoices. Mock-provider usage is excluded. Usage without a pricing rule is
listed as "unpriced" (never counted as free). Revenue is what Stripe reported (gross, with the VAT
included kept apart); gross contribution = net revenue - estimated provider cost, before every other
operating expense (hardware, hosting, support, payment fees).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.db.models import Account, Device, RevenueEvent, UsageRecord, utcnow
from app.db.session import get_session
from app.money import micro_to_float
from app.pipeline.metrics import percentile
from app.security import require_admin

router = APIRouter(prefix="/api/finance", tags=["finance"], dependencies=[Depends(require_admin)])

PENCE_MICRO = 10_000


def _group(u: UsageRecord) -> str:
    if u.unit in ("web_search_call", "cache_hit"):
        return "search"
    if u.kind == "llm":
        return {"input_token": "llm_input", "cached_input_token": "llm_cached_input", "output_token": "llm_output"}.get(u.unit, "llm_other")
    return u.kind  # stt | tts | search


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:3]}***@{domain}"


@router.get("")
def finance(days: int = Query(30, ge=1, le=366), db: Session = Depends(get_session)) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    rows = db.exec(select(UsageRecord).where(UsageRecord.created_at >= since, UsageRecord.mock == False)).all()  # noqa: E712

    total = 0
    by_group: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    by_month: dict[str, int] = defaultdict(int)
    by_account: dict[int | None, int] = defaultdict(int)
    by_device: dict[str, int] = defaultdict(int)
    per_turn: dict[str, int] = defaultdict(int)
    turn_status: dict[str, str] = {}
    unpriced: dict[tuple[str, str, str], dict[str, float]] = {}
    searches = cache_hits = 0
    search_cost = 0
    non_billable = 0
    for u in rows:
        if u.unit == "web_search_call":
            searches += int(u.quantity)
        elif u.unit == "cache_hit":
            cache_hits += int(u.quantity)
        if u.cost_micro_gbp is None:
            key = (u.provider, u.model, u.unit)
            entry = unpriced.setdefault(key, {"rows": 0, "quantity": 0.0})
            entry["rows"] += 1
            entry["quantity"] += u.quantity
            continue
        c = u.cost_micro_gbp
        total += c
        by_group[_group(u)] += c
        by_day[u.created_at.date().isoformat()] += c
        by_month[u.created_at.strftime("%Y-%m")] += c
        by_account[u.account_id] += c
        by_device[u.device_id] += c
        if u.unit == "web_search_call":
            search_cost += c
        if not u.billable:
            non_billable += c
        if u.turn_uid:
            per_turn[u.turn_uid] += c
            if u.turn_status:
                turn_status[u.turn_uid] = u.turn_status

    # Per turn (by the turn's final status)
    status_cost: dict[str, list[int]] = defaultdict(list)
    for uid, c in per_turn.items():
        status_cost[turn_status.get(uid, "unknown")].append(c)
    completed = sorted(status_cost.get("completed", []))

    def stats(values: list[int]) -> dict[str, Any]:
        if not values:
            return {"count": 0}
        return {
            "count": len(values),
            "total": micro_to_float(sum(values)),
            "mean": micro_to_float(sum(values) // len(values), 5),
            "p50": micro_to_float(int(percentile(values, 50)), 5),
            "p90": micro_to_float(int(percentile(values, 90)), 5),
            "p95": micro_to_float(int(percentile(values, 95)), 5),
            "max": micro_to_float(max(values), 5),
        }

    avg_search = search_cost // searches if searches else 0

    # Revenue (Stripe), GBP only in the totals; other currencies listed apart.
    revenue = db.exec(select(RevenueEvent).where(RevenueEvent.created_at >= since)).all()
    rev_gbp: dict[str, dict[str, int]] = defaultdict(lambda: {"gross": 0, "tax": 0})
    other_currency: dict[str, int] = defaultdict(int)
    for r in revenue:
        if r.currency == "gbp":
            rev_gbp[r.kind]["gross"] += r.amount_pence
            rev_gbp[r.kind]["tax"] += r.tax_pence
        else:
            other_currency[r.currency] += r.amount_pence
    net_pence = sum(v["gross"] - v["tax"] for v in rev_gbp.values())

    names = {a.id: _mask(a.email) for a in db.exec(select(Account)).all()}
    device_names = {d.id: d.name for d in db.exec(select(Device)).all()}
    return {
        "days": days,
        "currency": "GBP",
        "note": "Estimated provider costs (usage x pricing rules), not provider invoices. Mock usage excluded.",
        "provider_cost": micro_to_float(total),
        "by_group": {k: micro_to_float(v) for k, v in sorted(by_group.items())},
        "by_day": [{"day": d, "cost": micro_to_float(v)} for d, v in sorted(by_day.items())],
        "by_month": [{"month": m, "cost": micro_to_float(v)} for m, v in sorted(by_month.items())],
        "by_account": sorted(
            ({"account_id": k, "account": names.get(k, "(no owner)") if k is not None else "(no owner)", "cost": micro_to_float(v)}
             for k, v in by_account.items()),
            key=lambda r: -r["cost"],
        ),
        "by_device": sorted(
            ({"device_id": k, "name": device_names.get(k, k), "cost": micro_to_float(v)} for k, v in by_device.items()),
            key=lambda r: -r["cost"],
        ),
        "turns": {
            "completed": stats(completed),
            "aborted": stats(sorted(status_cost.get("aborted", []))),
            "error": stats(sorted(status_cost.get("error", []))),
            "no_speech": stats(sorted(status_cost.get("no_speech", []))),
            "not_charged_to_customers": micro_to_float(non_billable),
        },
        "search": {
            "searches": searches,
            "cost": micro_to_float(search_cost),
            "cache_hits": cache_hits,
            "hit_rate": round(cache_hits / (cache_hits + searches), 3) if (cache_hits + searches) else None,
            # Search-call fees avoided by the cache (the search request's own tokens come on top).
            "avoided_at_least": micro_to_float(cache_hits * avg_search),
        },
        "unpriced": [
            {"provider": p, "model": m, "unit": un, "rows": v["rows"], "quantity": round(v["quantity"], 3)}
            for (p, m, un), v in sorted(unpriced.items())
        ],
        "revenue": {
            "by_kind": {k: {"gross": v["gross"] / 100, "vat": v["tax"] / 100, "net": (v["gross"] - v["tax"]) / 100} for k, v in rev_gbp.items()},
            "net": net_pence / 100,
            "other_currencies": {k: v / 100 for k, v in other_currency.items()},
        },
        # Before hardware, hosting, support, payment fees and every other operating expense.
        "gross_contribution": round((net_pence * PENCE_MICRO - total) / 1_000_000, 4),
    }


@router.get("/accounts")
def account_costs(
    status: str | None = Query(None, pattern="^(within|approaching|over|critical)$"),
    sort: str = Query("cost", pattern="^(cost|projected|average|interactions|used_pct)$"),
    include_internal: bool = False,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Internal AI cost per customer account in its current billing period: interactions used of the
    account's allowance, actual AI cost (interactive and background), average per interaction, the projected
    cost at the allowance (an estimate) and the monitoring status against the internal target. Monitoring
    only: none of this limits a customer (app/cost_monitor.py)."""
    from app import cost_monitor
    from app.plan import get_plan

    plan = get_plan(db)
    q = select(Account).where(Account.status != "deleted")
    if not include_internal:
        q = q.where(Account.internal == False)  # noqa: E712 - SQL expression
    accounts = list(db.exec(q).all())
    by_id = {a.id: a for a in accounts}
    rows = [cost_monitor.view(c, plan, by_id.get(c.account_id)) for c in cost_monitor.account_costs(db, accounts, plan)]
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        r["account"] = _mask(r["account"]) if r["account"] else None
        counts[r["status"]] += 1  # every status, before the filter (for the operator's tabs)
    if status:
        rows = [r for r in rows if r["status"] == status]
    key = {"cost": "cost", "projected": "projected_cost", "average": "average_cost", "interactions": "interactions",
           "used_pct": "used_pct"}[sort]
    rows.sort(key=lambda r: (r[key] is not None, r[key] or 0), reverse=True)
    return {
        "thresholds": {"warn": plan.cost_warn_pence / 100, "target": plan.cost_target_pence / 100,
                       "critical": plan.cost_critical_pence / 100},
        "min_sample": cost_monitor.MIN_SAMPLE,
        "status_counts": dict(counts),
        "accounts": rows[:limit],
    }
