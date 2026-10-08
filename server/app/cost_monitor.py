"""Internal AI cost monitoring per account and billing period (operator only - never shown to customers and
never used to refuse a request: the customer's contractual allowance is a number of interactions, enforced
by app/usage_ops.py).

For each account's current period:
  total cost        every priced, non-mock provider cost recorded in the period (micro-GBP), split into
                    interactive (the customer's requests) and background (memory learning, embeddings,
                    voice samples), and by component (stt / llm / tts / search / embedding)
  average           total cost / interactions counted in the period
  projection        average x the account's interaction limit - an estimate, only shown from MIN_SAMPLE
                    interactions on, flagged "incomplete" when some usage has no price yet (unpriced rows are
                    never treated as free)
  status            within (< warn) | approaching (>= warn) | over (>= target) | critical (>= critical),
                    thresholds from Plan settings (defaults £2.00 / £2.50 / £5.00)

evaluate() records each level an account reaches once per period (table cost_alerts), writes an audit entry
and the gateway publishes an operator-only live event. Nothing here suspends or slows an account.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app import accounts as accounts_mod
from app import allowance as allowance_mod
from app import billing
from app.db.models import Account, CostAlert, Subscription, UsageOperation, UsageRecord
from app.db.session import session_scope
from app.interactions import INTERACTION_KINDS
from app.money import pence_to_micro
from app.plan import Plan, get_plan

log = logging.getLogger(__name__)

MIN_SAMPLE = 20  # interactions before a projection is shown (fewer would be misleading)
LEVELS = ("approaching", "over", "critical")
GROUPS = ("stt", "llm", "tts", "search", "embedding", "other")
_tasks: set[asyncio.Task] = set()


def group_of(kind: str, unit: str) -> str:
    if unit in ("web_search_call", "cache_hit"):
        return "search"
    return kind if kind in GROUPS else "other"


def status_for(total_micro: int, plan: Plan) -> str:
    if total_micro >= pence_to_micro(plan.cost_critical_pence):
        return "critical"
    if total_micro >= pence_to_micro(plan.cost_target_pence):
        return "over"
    if total_micro >= pence_to_micro(plan.cost_warn_pence):
        return "approaching"
    return "within"


@dataclass
class AccountCost:
    account_id: int
    period_key: str
    period_start: datetime
    period_end: datetime
    limit: int
    interactions: int = 0
    interactive_micro: int = 0
    background_micro: int = 0
    unpriced_rows: int = 0
    uncounted: int = 0  # requests of the period from before the interaction count (migration 0027)
    by_group: dict[str, int] = field(default_factory=dict)

    @property
    def total_micro(self) -> int:
        return self.interactive_micro + self.background_micro

    @property
    def average_micro(self) -> float | None:
        return self.total_micro / self.interactions if self.interactions else None

    @property
    def projection_quality(self) -> str:
        if self.interactions < MIN_SAMPLE:
            return "insufficient_data"
        # unpriced usage, or costs of requests made before interactions were counted (the average is too high)
        return "incomplete" if self.unpriced_rows or self.uncounted else "estimate"

    @property
    def projected_micro(self) -> int | None:
        if self.interactions < MIN_SAMPLE or self.average_micro is None:
            return None
        return round(self.average_micro * self.limit)


def _period_ids(db: Session, accounts: list[Account], now: datetime) -> dict[int, allowance_mod.PeriodId]:
    ids = [a.id for a in accounts if a.id is not None]
    subs: dict[int, list[Subscription]] = {}
    if ids:
        for s in db.exec(select(Subscription).where(col(Subscription.account_id).in_(ids))).all():
            subs.setdefault(s.account_id, []).append(s)  # type: ignore[arg-type]
    return {a.id: allowance_mod.period_identity(billing.pick_active(subs.get(a.id, [])), now)  # type: ignore[misc]
            for a in accounts if a.id is not None}


def account_costs(db: Session, accounts: list[Account], plan: Plan | None = None,
                  now: datetime | None = None) -> list[AccountCost]:
    """Cost and interactions of each account's current period: three grouped queries for all accounts."""
    now = now or datetime.now(timezone.utc)
    plan = plan or get_plan(db)
    pids = _period_ids(db, accounts, now)
    if not pids:
        return []
    extra = _topups(db, list(pids), pids, now, plan)
    out = {
        a.id: AccountCost(a.id, pids[a.id].key, pids[a.id].period.start, pids[a.id].period.end,  # type: ignore[index]
                          allowance_mod.interaction_limit(a, plan) + extra.get(a.id, 0))  # type: ignore[arg-type]
        for a in accounts if a.id in pids
    }
    keys = {p.key for p in pids.values()}
    ids = list(out)
    # costs without an operation (older rows) belong to turns: interactive
    background = case((col(UsageOperation.kind).is_(None), False),
                      (col(UsageOperation.kind).in_(INTERACTION_KINDS), False), else_=True)
    rows = db.exec(
        select(UsageRecord.account_id, UsageRecord.period_key, UsageRecord.kind, UsageRecord.unit, background,
               func.coalesce(func.sum(UsageRecord.cost_micro_gbp), 0),
               func.sum(case((col(UsageRecord.cost_micro_gbp).is_(None), 1), else_=0)))
        .select_from(UsageRecord).outerjoin(UsageOperation, UsageOperation.id == UsageRecord.operation_id)
        .where(col(UsageRecord.account_id).in_(ids), col(UsageRecord.period_key).in_(keys),
               UsageRecord.mock == False)  # noqa: E712 - SQL expression
        .group_by(UsageRecord.account_id, UsageRecord.period_key, UsageRecord.kind, UsageRecord.unit, background)
    ).all()
    for acc_id, key, kind, unit, is_bg, cost, unpriced in rows:
        c = out.get(acc_id)
        if c is None or c.period_key != key:
            continue  # the same calendar key of another account
        cost = int(cost or 0)
        if is_bg:
            c.background_micro += cost
        else:
            c.interactive_micro += cost
        c.unpriced_rows += int(unpriced or 0)
        g = group_of(kind, unit)
        c.by_group[g] = c.by_group.get(g, 0) + cost
    counted = case((UsageOperation.interaction == True, 1), else_=0)  # noqa: E712
    legacy = case((col(UsageOperation.interaction).is_(None) & col(UsageOperation.state).in_(("settled", "cancelled"))
                   & col(UsageOperation.kind).in_(INTERACTION_KINDS), 1), else_=0)
    counts = db.exec(
        select(UsageOperation.account_id, UsageOperation.period_key, func.sum(counted), func.sum(legacy))
        .where(col(UsageOperation.account_id).in_(ids), col(UsageOperation.period_key).in_(keys))
        .group_by(UsageOperation.account_id, UsageOperation.period_key)
    ).all()
    for acc_id, key, n, old in counts:
        c = out.get(acc_id)
        if c is not None and c.period_key == key:
            c.interactions, c.uncounted = int(n or 0), int(old or 0)
    return list(out.values())


def _topups(db: Session, ids: list[int], pids: dict[int, allowance_mod.PeriodId], now: datetime,
            plan: Plan) -> dict[int, int]:
    """Top-up interactions of each account's current period, in one query (as allowance.topup_interactions)."""
    from app.db.models import TopUp

    out: dict[int, int] = {}
    if not ids:
        return out
    for t in db.exec(select(TopUp).where(col(TopUp.account_id).in_(ids), TopUp.status == "paid")).all():
        pid = pids.get(t.account_id)
        if pid is None:
            continue
        start, end = allowance_mod._aware(t.period_start), allowance_mod._aware(t.period_end)
        if (t.period_key == pid.key) if t.period_key is not None else (start <= now < end):  # type: ignore[operator]
            out[t.account_id] = out.get(t.account_id, 0) + (t.interactions if t.interactions is not None
                                                            else plan.topup_interactions)
    return out


def view(c: AccountCost, plan: Plan, account: Account | None = None) -> dict[str, Any]:
    """Operator view of one account (GBP floats for display; integers stay the source of truth)."""
    gbp = lambda m: None if m is None else round(m / 1_000_000, 4)  # noqa: E731
    return {
        "account_id": c.account_id,
        "account": account.email if account else None,
        "internal": bool(account.internal) if account else False,
        "period_start": c.period_start,
        "period_end": c.period_end,
        "limit": c.limit,
        "interactions": c.interactions,
        "used_pct": 100 if c.limit <= 0 else min(100, c.interactions * 100 // c.limit),
        "cost": gbp(c.total_micro),
        "interactive_cost": gbp(c.interactive_micro),
        "background_cost": gbp(c.background_micro),
        "by_group": {k: gbp(v) for k, v in sorted(c.by_group.items())},
        "average_cost": None if c.average_micro is None else round(c.average_micro / 1_000_000, 6),
        "projected_cost": gbp(c.projected_micro),
        "projection": c.projection_quality,
        "unpriced_rows": c.unpriced_rows,
        "target": gbp(pence_to_micro(plan.cost_target_pence)),
        "status": status_for(c.total_micro, plan),
    }


# --- operator alerts (monitoring only) ---------------------------------------------------------------------


def evaluate(account_id: int) -> list[dict[str, Any]]:
    """Record the cost levels this account reached in its current period for the first time. Returns the new
    alerts (for the operator live event). Never refuses or limits anything."""
    with session_scope() as db:
        acc = db.get(Account, account_id)
        if acc is None or acc.internal:
            return []
        plan = get_plan(db)
        (c,) = account_costs(db, [acc], plan) or [None]
        if c is None:
            return []
        reached = [lv for lv, pence in zip(LEVELS, (plan.cost_warn_pence, plan.cost_target_pence,
                                                     plan.cost_critical_pence)) if c.total_micro >= pence_to_micro(pence)]
        if not reached:
            return []
        have = set(db.exec(select(CostAlert.level).where(CostAlert.account_id == account_id,
                                                          CostAlert.period_key == c.period_key)).all())
        new: list[dict[str, Any]] = []
        for lv in reached:
            if lv in have:
                continue
            try:
                db.add(CostAlert(account_id=account_id, period_key=c.period_key, level=lv, cost_micro=c.total_micro))
                db.commit()
            except IntegrityError:
                db.rollback()  # another process recorded it first
                continue
            detail = (f"{lv}: £{c.total_micro / 1_000_000:.2f} AI cost this period, {c.interactions} interactions"
                      f"{' (some usage unpriced)' if c.unpriced_rows else ''}")
            accounts_mod.audit(db, "system", "cost.alert", account_id, detail=detail)
            log.warning("cost alert account=%s %s", account_id, detail)
            new.append({"level": lv, "cost": round(c.total_micro / 1_000_000, 4), "interactions": c.interactions,
                        "period_key": c.period_key})
        return new


async def evaluate_async(hub: Any, account_id: int) -> None:
    try:
        new = await asyncio.to_thread(evaluate, account_id)
    except Exception:  # noqa: BLE001 - monitoring must never affect the customer
        log.warning("cost monitoring of account %s failed", account_id, exc_info=True)
        return
    if hub is not None:
        for a in new:
            hub.publish({"type": "cost_alert", "account_id": account_id, **a}, operator_only=True)


def evaluate_soon(hub: Any, account_id: int) -> None:
    """Fire-and-forget evaluate (after a settle)."""
    try:
        task = asyncio.get_running_loop().create_task(evaluate_async(hub, account_id))
    except RuntimeError:  # no running loop (a thread): evaluate inline, without the live event
        try:
            evaluate(account_id)
        except Exception:  # noqa: BLE001
            log.warning("cost monitoring of account %s failed", account_id, exc_info=True)
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
