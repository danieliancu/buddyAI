"""The account's allowance for the current period: a number of AI interactions (ola Care: 1,000).

Period: the subscription's billing period (Stripe current_period_start..end); a Stripe period longer than a
month - a long free trial, whose Stripe period is the whole trial - is split into monthly cycles from its start,
so the interactions renew every month during the trial too. A complimentary grant uses 30-day cycles; the
calendar month when there is neither. All watches of the account
share it; nothing rolls over (counts are per period key).
Used = operations of the period settled as an interaction (usage_operations.interaction, decided once by
app/interactions.py). Limit = the plan's interaction_limit (or the account's authorised override) + the
interactions of the period's paid top-ups.

What the AI providers cost is recorded separately (usage_records, micro-GBP) and monitored by
app/cost_monitor.py: costs never limit the customer. The money functions below (used_final, active_exposure,
topups_micro, included_micro) describe the pre-0027 money allowance and are kept for history and reports.

Every period has a stable identity (period_identity: "stripe:<subscription id>:<start>", "comp:<grant id>:
<cycle start>", "cal:<YYYY-MM>"). An AI operation keeps the period it was accepted in, and so do its costs,
even when they arrive after the period rolled over (usage_records.period_key). Rows written before
operations existed have no period key and are counted by their created_at window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from app.db.models import Account, Subscription, TopUp, UsageOperation, UsageRecord
from app.interactions import INTERACTION_KINDS
from app.money import pence_to_micro, pounds_to_micro
from app.plan import Plan, get_plan

COMP_CYCLE = timedelta(days=30)


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    kind: str  # stripe | complimentary | calendar


@dataclass
class Allowance:
    """AI interactions of one account in one period (what the customer sees, and what admission enforces)."""

    period: Period
    used: int  # interactions counted in the period
    included: int  # the plan's limit, or the account's override
    extra: int = 0  # interactions added by the period's paid top-ups
    active: int = 0  # admitted requests still running (not counted yet)
    period_key: str = ""

    @property
    def limit(self) -> int:
        return self.included + self.extra

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def used_pct(self) -> int:
        """What the customer sees: whole percent, 0..100 (floor, so 100 means really used up)."""
        if self.limit <= 0:
            return 100
        return min(100, self.used * 100 // self.limit)


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


def _month_after(dt: datetime) -> datetime:
    year, month = (dt.year, dt.month + 1) if dt.month < 12 else (dt.year + 1, 1)
    for day in (dt.day, 30, 29, 28):
        try:
            return dt.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise ValueError(dt)


@dataclass(frozen=True)
class PeriodId:
    key: str
    period: Period
    source: str  # stripe | complimentary | calendar
    subscription_id: int | None


def period_identity(sub: Subscription | None, now: datetime | None = None) -> PeriodId:
    """The current allowance period with a stable key. Keys use the start only, so extending a grant or a
    late renewal never changes the identity of a period that already has operations."""
    now = now or datetime.now(timezone.utc)
    if sub is not None:
        start, end = _aware(sub.current_period_start), _aware(sub.current_period_end)
        if sub.source != "complimentary" and end and now >= end:
            # Renewal webhook not here yet: predict the next monthly period from the end, so its key matches
            # the real one when it arrives (instead of falling into the calendar month).
            from app import billing  # local: billing imports this module

            if billing.is_entitled(sub):
                p_start = end
                while _month_after(p_start) <= now:
                    p_start = _month_after(p_start)
                return PeriodId(f"stripe:{sub.id}:{int(p_start.timestamp())}", Period(p_start, _month_after(p_start), "stripe"),
                                "stripe", sub.id)
    period = period_for(sub, now)
    if period.kind == "calendar":
        return PeriodId(f"cal:{period.start:%Y-%m}", period, "calendar", None)
    prefix = "comp" if period.kind == "complimentary" else "stripe"
    assert sub is not None
    return PeriodId(f"{prefix}:{sub.id}:{int(period.start.timestamp())}", period, period.kind, sub.id)


def add_months(dt: datetime, n: int) -> datetime:
    """dt + n calendar months, keeping dt's day where the month has it (31 Jan + 1 -> 28/29 Feb)."""
    year, month = divmod(dt.month - 1 + n, 12)
    for day in (dt.day, 30, 29, 28):
        try:
            return dt.replace(year=dt.year + year, month=month + 1, day=day)
        except ValueError:
            continue
    raise ValueError(dt)


# A Stripe period longer than a month by more than this is split into monthly allowance cycles (a long free
# trial: Stripe's current period is the whole trial). Normal monthly periods and trials of up to 31 days (even
# when they start in February) stay one cycle; a remainder this short is added to the last monthly cycle.
MONTH_SLACK = timedelta(days=3)


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
            if end - add_months(start, 1) <= MONTH_SLACK:
                return Period(start, end, "stripe")
            # Longer than a month (a long free trial): the interactions renew every month from its start. Cycle
            # starts are computed from the period start (no drift); a last remainder of a few days is part of the
            # last monthly cycle, which then ends with the period.
            n = 0
            while add_months(start, n + 1) <= now and end - add_months(start, n + 1) > MONTH_SLACK:
                n += 1
            c_end = add_months(start, n + 1)
            if end - c_end <= MONTH_SLACK:
                c_end = end
            return Period(add_months(start, n), c_end, "stripe")
    return calendar_month(now)


def interaction_limit(acc: Account, plan: Plan) -> int:
    """The account's interactions per period: its authorised override, else the plan's."""
    return acc.interaction_limit_override if acc.interaction_limit_override is not None else plan.interaction_limit


def topup_interactions(db: Session, account_id: int, now: datetime, period_key: str | None, plan: Plan) -> int:
    """Interactions added by paid top-ups of the period. A top-up bought before 0027 (interactions NULL)
    counts as the plan's current top-up size, so nothing already bought is lost."""
    rows = db.exec(select(TopUp).where(TopUp.account_id == account_id, TopUp.status == "paid")).all()
    return sum(
        (t.interactions if t.interactions is not None else plan.topup_interactions)
        for t in rows
        if (t.period_key is not None and t.period_key == period_key)
        or (t.period_key is None and _aware(t.period_start) <= now < _aware(t.period_end))  # type: ignore[operator]
    )


def interactions_used(db: Session, account_id: int, period_key: str) -> int:
    """Operations of the period settled as an interaction (index ix_usage_ops_interactions)."""
    q = select(func.count()).select_from(UsageOperation).where(
        UsageOperation.account_id == account_id, UsageOperation.period_key == period_key,
        UsageOperation.interaction == True,  # noqa: E712 - SQL expression
    )
    return int(db.exec(q).one() or 0)


def interactions_active(db: Session, account_id: int, period_key: str) -> int:
    """Admitted requests of the period still running: each may still become an interaction."""
    q = select(func.count()).select_from(UsageOperation).where(
        UsageOperation.account_id == account_id, UsageOperation.period_key == period_key,
        UsageOperation.state.in_(_ACTIVE), UsageOperation.kind.in_(INTERACTION_KINDS),  # type: ignore[attr-defined]
    )
    return int(db.exec(q).one() or 0)


def included_micro(acc: Account, sub: Subscription | None, plan: Plan) -> int:
    """Legacy (pre-0027 money allowance): kept for reports only."""
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


def topups_micro(db: Session, account_id: int, now: datetime, period_key: str | None = None) -> int:
    """Paid top-ups of the period: by period key, plus older top-ups (no key) whose window contains now."""
    rows = db.exec(
        select(TopUp).where(TopUp.account_id == account_id, TopUp.status == "paid")
    ).all()
    return sum(
        pence_to_micro(t.allowance_pence)
        for t in rows
        if (t.period_key is not None and t.period_key == period_key)
        or (t.period_key is None and _aware(t.period_start) <= now < _aware(t.period_end))  # type: ignore[operator]
    )


_TERMINAL = ("settled", "cancelled", "expired")
_ACTIVE = ("reserved", "running")


def used_final(db: Session, account_id: int, pid: PeriodId) -> tuple[int, int]:
    """(used micro-GBP, unpriced rows) of the period, counting only costs whose operation has ended (or that
    have no operation): billable, non-mock, period_key = the period - plus legacy rows in its time window.
    Costs of running operations are counted on the admission side (active_exposure), never twice."""
    p = pid.period
    in_period = or_(
        UsageRecord.period_key == pid.key,
        (UsageRecord.period_key.is_(None)) & (UsageRecord.created_at >= p.start) & (UsageRecord.created_at < p.end),  # type: ignore[union-attr]
    )
    finished = or_(UsageRecord.operation_id.is_(None), UsageOperation.state.in_(_TERMINAL))  # type: ignore[union-attr,attr-defined]
    base = (
        UsageRecord.account_id == account_id,
        UsageRecord.billable == True,  # noqa: E712 - SQL expression
        UsageRecord.mock == False,  # noqa: E712
        in_period,
        finished,
    )
    q = select(func.coalesce(func.sum(UsageRecord.cost_micro_gbp), 0)).select_from(UsageRecord).outerjoin(
        UsageOperation, UsageOperation.id == UsageRecord.operation_id).where(*base)
    used = db.exec(q).one()
    qn = select(func.count()).select_from(UsageRecord).outerjoin(
        UsageOperation, UsageOperation.id == UsageRecord.operation_id).where(*base, UsageRecord.cost_micro_gbp.is_(None))  # type: ignore[union-attr]
    return int(used or 0), int(db.exec(qn).one() or 0)


def active_exposure(db: Session, account_id: int, period_key: str, exclude_id: int | None = None) -> tuple[int, int, int]:
    """(count, sum of recorded cost, sum of exposure) of the account's running operations in the period.
    exposure = max(reserved, recorded): an operation's recorded cost and its reservation are never both counted."""
    exposure = case((UsageOperation.reserved_micro > UsageOperation.recorded_cost_micro, UsageOperation.reserved_micro),
                    else_=UsageOperation.recorded_cost_micro)
    q = select(func.count(), func.coalesce(func.sum(UsageOperation.recorded_cost_micro), 0),
               func.coalesce(func.sum(exposure), 0)).where(
        UsageOperation.account_id == account_id, UsageOperation.period_key == period_key,
        UsageOperation.state.in_(_ACTIVE),  # type: ignore[attr-defined]
    )
    if exclude_id is not None:
        q = q.where(UsageOperation.id != exclude_id)
    n, rec, exp = db.exec(q).one()
    return int(n or 0), int(rec or 0), int(exp or 0)


def activity_count(db: Session, account_id: int, period: Period) -> int:
    """Legacy: completed conversations in a time window, from usage rows (kept for reports)."""
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
    now: datetime | None = None,
) -> Allowance:
    """The interactions shown to the customer and the operator: used = settled interactions of the period
    (never a running request); active = requests still running."""
    now = now or datetime.now(timezone.utc)
    plan = plan or get_plan(db)
    pid = period_identity(sub, now)
    assert acc.id is not None
    return Allowance(
        period=pid.period,
        used=interactions_used(db, acc.id, pid.key),
        included=interaction_limit(acc, plan),
        extra=topup_interactions(db, acc.id, now, pid.key, plan),
        active=interactions_active(db, acc.id, pid.key),
        period_key=pid.key,
    )
