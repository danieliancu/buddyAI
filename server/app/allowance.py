"""The account's AI allowance for the current period, in micro-pounds (integers).

Period: the subscription's billing period (Stripe current_period_start..end), the complimentary
grant's 30-day cycle, or the calendar month when there is neither. All watches of the account share
it. Used = sum of billable, non-mock usage recorded in the period (costs frozen in GBP when recorded);
usage without a pricing rule cannot be counted and is flagged to the operator instead.
Limit = the plan allowance (or the grant's / the per-account override) + paid top-ups of the period.

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
    """The allowance shown to the customer and the operator: used = costs of finished operations (never a
    reservation); reserved_micro = what running operations hold (from the database)."""
    now = now or datetime.now(timezone.utc)
    plan = plan or get_plan(db)
    pid = period_identity(sub, now)
    used, unpriced = used_final(db, acc.id, pid)
    _n, _rec, exposure = active_exposure(db, acc.id, pid.key)
    return Allowance(
        period=pid.period,
        used_micro=used,
        included_micro=included_micro(acc, sub, plan),
        topup_micro=topups_micro(db, acc.id, now, pid.key),
        reserved_micro=exposure if reserved_micro == 0 else reserved_micro,
        unpriced_rows=unpriced,
    )
