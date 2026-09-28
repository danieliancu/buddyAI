"""May this watch start a turn? Subscription status + monthly fair-use allowance.

Billing disabled (no Stripe key) or a watch without an owner (operator stock): always allowed.
Allowance = AI cost this calendar month (display currency) vs the plan cap or a per-account override.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app import billing, email
from app.config import get_settings
from app.db.models import Account
from app.db.repositories import UsageRepo
from app.db.session import session_scope
from app.pricing.currency import get_currency

WARN_AT = 0.8


@dataclass
class Allowance:
    used: float
    limit: float
    currency: str

    @property
    def fraction(self) -> float:
        return 0.0 if self.limit <= 0 else self.used / self.limit


@dataclass
class Decision:
    allowed: bool
    code: str | None = None  # subscription_required | limit_reached
    message: str = ""


def month_start() -> datetime:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def allowance(db, acc: Account) -> Allowance:
    cur = get_currency()
    used_usd = sum(u.cost_usd or 0.0 for u in UsageRepo(db).since(month_start(), account_id=acc.id))
    limit = acc.allowance_override if acc.allowance_override is not None else get_settings().care_allowance
    return Allowance(round(used_usd * cur["usd_rate"], 4), limit, cur["currency"])


async def check(account_id: int | None) -> Decision:
    if account_id is None or not get_settings().billing_enabled:
        return Decision(True)
    warn_to: str | None = None
    with session_scope() as db:
        acc = db.get(Account, account_id)
        if acc is None:
            return Decision(True)
        sub = billing.active_subscription(db, acc.id)
        if sub is None or sub.status not in billing.ENTITLED_STATUSES:
            return Decision(False, "subscription_required", "BuddyAI Care subscription needed")
        a = allowance(db, acc)
        if a.fraction >= 1.0:
            return Decision(False, "limit_reached", "monthly allowance used up")
        month = month_start().strftime("%Y-%m")
        if a.fraction >= WARN_AT and acc.allowance_warned_month != month:
            acc.allowance_warned_month = month
            db.add(acc)
            db.commit()
            warn_to = acc.email
    if warn_to:
        await email.send(email.allowance_warning(warn_to))
    return Decision(True)
