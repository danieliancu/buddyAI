"""Monthly request budget of a paid search provider (Brave), safe under concurrent load.

reserve() counts a request before it is sent, with one conditional UPDATE (requests = requests + 1 WHERE
requests < cap): the database serialises concurrent writers, so two turns can never both take the last
request of the month. A request that then fails is not refunded (Brave may have billed it).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import SearchProviderUsage
from app.db.session import session_scope


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def monthly_cap(budget_usd: float, price_per_request_usd: float) -> int:
    if budget_usd <= 0:
        return 0
    if price_per_request_usd <= 0:
        return 10**9
    return math.floor(budget_usd / price_per_request_usd + 1e-9)


def reserve(provider: str, cap: int, now: datetime | None = None) -> bool:
    """Take one request of this month's budget; False when it is used up."""
    if cap <= 0:
        return False
    month = month_key(now)
    with session_scope() as db:
        if db.exec(select(SearchProviderUsage.id).where(
                SearchProviderUsage.month == month, SearchProviderUsage.provider == provider)).first() is None:
            try:
                db.add(SearchProviderUsage(month=month, provider=provider, requests=0))
                db.commit()
            except IntegrityError:  # created by a concurrent request
                db.rollback()
        res = db.exec(  # type: ignore[call-overload]
            update(SearchProviderUsage)
            .where(SearchProviderUsage.month == month, SearchProviderUsage.provider == provider,
                   SearchProviderUsage.requests < cap)
            .values(requests=SearchProviderUsage.requests + 1)
        )
        db.commit()
        return bool(getattr(res, "rowcount", 0))


def used(provider: str, now: datetime | None = None) -> int:
    with session_scope() as db:
        row = db.exec(select(SearchProviderUsage).where(
            SearchProviderUsage.month == month_key(now), SearchProviderUsage.provider == provider)).first()
        return row.requests if row else 0
