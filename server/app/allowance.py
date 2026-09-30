"""The account's AI allowance for the current period, in micro-pounds (integers).

Period: the subscription's billing period (Stripe current_period_start..end), the complimentary
grant's 30-day cycle, or the calendar month when there is neither. All watches of the account share
it. Used = sum of billable, non-mock usage recorded in the period (costs frozen in GBP when recorded);
usage without a pricing rule cannot be counted and is flagged to the operator instead.
Limit = the plan allowance (or the grant's / the per-account override) + paid top-ups of the period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import Account, Subscription, TopUp, UsageRecord
from app.money import micro_to_float, pence_to_micro, pounds_to_micro
from app.plan import Plan, get_plan

COMP_CYCLE = timedelta(days=30)


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    kind: str  # stripe | complimentary | calendar


@dataclass
class Allowance:
    period: Period
    used_micro: int
    included_micro: int
    topup_micro: int
    reserved_micro: int = 0
    unpriced_rows: int = 0
    currency: str = "GBP"

    @property
    def limit_micro(self) -> int:
        return self.included_micro + self.topup_micro

    @property
    def fraction(self) -> float:
        return 1.0 if self.limit_micro <= 0 else self.used_micro / self.limit_micro

    @property
    def fraction_with_reserved(self) -> float:
        return 1.0 if self.limit_micro <= 0 else (self.used_micro + self.reserved_micro) / self.limit_micro

    @property
    def used_pct(self) -> int:
        """What the customer sees: whole percent, 0..100 (floor, so 100 means really used up)."""
        if self.limit_micro <= 0:
            return 100
        return min(100, self.used_micro * 100 // self.limit_micro)

    # Operator-facing (GBP, display only)
    @property
    def used(self) -> float:
        return micro_to_float(self.used_micro)

    @property
    def limit(self) -> float:
        return micro_to_float(self.limit_micro)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def calendar_month(now: datetime) -> Period:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return Period(start, end, "calendar")


def _month_before(dt: datetime) -> datetime:
    year, month = (dt.year, dt.month - 1) if dt.month > 1 else (dt.year - 1, 12)
    for day in (dt.day, 30, 29, 28):
        try:
            return dt.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise ValueError(dt)


def period_for(sub: Subscription | None, now: datetime | None = None) -> Period:
    now = now or datetime.now(timezone.utc)
    if sub is None:
        return calendar_month(now)
    start, end = _aware(sub.current_period_start), _aware(sub.current_period_end)
    if sub.source == "complimentary" and start and end:
        # A grant of N days is split into 30-day allowance cycles from its start.
        cycles = max(0, int((min(now, end - timedelta(microseconds=1)) - start) / COMP_CYCLE))
        c_start = start + cycles * COMP_CYCLE
        return Period(c_start, min(end, c_start + COMP_CYCLE), "complimentary")
    if end:
        start = start or _month_before(end)
        if start <= now < end:
            return Period(start, end, "stripe")
    return calendar_month(now)


def included_micro(acc: Account, sub: Subscription | None, plan: Plan) -> int:
    if sub is not None and sub.source == "complimentary" and sub.allowance_pence is not None:
        return pence_to_micro(sub.allowance_pence)
    if acc.allowance_override is not None:
        return pounds_to_micro(acc.allowance_override)
    return pence_to_micro(plan.care_allowance_pence)


def used_micro(db: Session, account_id: int, period: Period) -> tuple[int, int]:
    """(used micro-GBP, unpriced rows) of billable, non-mock usage in the period."""
    base = (
        UsageRecord.account_id == account_id,
        UsageRecord.billable == True,  # noqa: E712 - SQL expression
        UsageRecord.mock == False,  # noqa: E712
        UsageRecord.created_at >= period.start,
        UsageRecord.created_at < period.end,
    )
    used = db.exec(select(func.coalesce(func.sum(UsageRecord.cost_micro_gbp), 0)).where(*base)).one()
    unpriced = db.exec(select(func.count()).select_from(UsageRecord).where(*base, UsageRecord.cost_micro_gbp.is_(None))).one()  # type: ignore[union-attr]
    return int(used or 0), int(unpriced or 0)


def topups_micro(db: Session, account_id: int, now: datetime) -> int:
    rows = db.exec(
        select(TopUp).where(TopUp.account_id == account_id, TopUp.status == "paid")
    ).all()
    return sum(
        pence_to_micro(t.allowance_pence)
        for t in rows
        if _aware(t.period_start) <= now < _aware(t.period_end)  # type: ignore[operator]
    )


def activity_count(db: Session, account_id: int, period: Period) -> int:
    """Completed conversations in the period (usage rows keep turn_uid/status after history deletion)."""
    return int(
        db.exec(
            select(func.count(func.distinct(UsageRecord.turn_uid))).where(
                UsageRecord.account_id == account_id,
                UsageRecord.turn_status == "completed",
                UsageRecord.created_at >= period.start,
                UsageRecord.created_at < period.end,
            )
        ).one()
        or 0
    )


def compute(
    db: Session,
    acc: Account,
    sub: Subscription | None,
    plan: Plan | None = None,
    reserved_micro: int = 0,
    now: datetime | None = None,
) -> Allowance:
    now = now or datetime.now(timezone.utc)
    plan = plan or get_plan(db)
    period = period_for(sub, now)
    used, unpriced = used_micro(db, acc.id, period)
    return Allowance(
        period=period,
        used_micro=used,
        included_micro=included_micro(acc, sub, plan),
        topup_micro=topups_micro(db, acc.id, now),
        reserved_micro=reserved_micro,
        unpriced_rows=unpriced,
    )
