"""ola Care plan settings (operator-editable, table billing_settings, one row).

The customer's allowance is a number of AI interactions per billing period (interaction_limit, plus
topup_interactions per extra-usage purchase): that is what admission enforces. The cost thresholds
(cost_*_pence) are internal monitoring of what the AI providers cost us: they never refuse anything.
The legacy money allowance fields (care_allowance_pence, topup_allowance_pence, reserve_pence) are kept for
history and no longer used. Nothing here talks to Stripe: the Stripe price ids live in the environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlmodel import Session

from app.config import get_settings
from app.db.models import BillingSettings, utcnow


@dataclass(frozen=True)
class Plan:
    enforce: bool
    care_price_pence: int
    care_allowance_pence: int
    topup_price_pence: int
    topup_allowance_pence: int
    thresholds: tuple[int, ...]
    usd_gbp_rate: Decimal
    reserve_pence: int
    interaction_limit: int = 1000
    topup_interactions: int = 250
    cost_warn_pence: int = 200
    cost_target_pence: int = 250
    cost_critical_pence: int = 500

    @property
    def enforced(self) -> bool:
        """Subscriptions and allowances are checked when the operator enabled it, or Stripe is live."""
        return self.enforce or get_settings().billing_enabled


def parse_thresholds(text: str) -> tuple[int, ...]:
    out = sorted({int(p) for p in text.replace(" ", "").split(",") if p})
    if not out or any(t < 1 or t > 100 for t in out):
        raise ValueError("thresholds must be percentages between 1 and 100")
    return tuple(out)


def parse_rate(text: str) -> Decimal:
    try:
        rate = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("usd_gbp_rate must be a number") from exc
    if not (Decimal("0.1") <= rate <= Decimal("10")):
        raise ValueError("usd_gbp_rate out of range")
    return rate


def row(db: Session) -> BillingSettings:
    r = db.get(BillingSettings, 1)
    if r is None:  # databases created before migration 0007 seeded it
        r = BillingSettings(id=1)
        db.add(r)
        db.commit()
        db.refresh(r)
    return r


def get_plan(db: Session) -> Plan:
    r = row(db)
    return Plan(
        enforce=r.enforce,
        care_price_pence=r.care_price_pence,
        care_allowance_pence=r.care_allowance_pence,
        topup_price_pence=r.topup_price_pence,
        topup_allowance_pence=r.topup_allowance_pence,
        thresholds=parse_thresholds(r.thresholds),
        usd_gbp_rate=parse_rate(r.usd_gbp_rate),
        reserve_pence=r.reserve_pence,
        interaction_limit=r.interaction_limit,
        topup_interactions=r.topup_interactions,
        cost_warn_pence=r.cost_warn_pence,
        cost_target_pence=r.cost_target_pence,
        cost_critical_pence=r.cost_critical_pence,
    )


EDITABLE = (
    "enforce",
    "care_price_pence",
    "topup_price_pence",
    "thresholds",
    "usd_gbp_rate",
    "interaction_limit",
    "topup_interactions",
    "cost_warn_pence",
    "cost_target_pence",
    "cost_critical_pence",
)
MAX_INTERACTIONS = 1_000_000


def update(db: Session, changes: dict, actor: str) -> BillingSettings:
    r = row(db)
    for key, value in changes.items():
        if key not in EDITABLE or value is None:
            continue
        if key == "thresholds":
            value = ",".join(str(t) for t in parse_thresholds(str(value)))
        elif key == "usd_gbp_rate":
            value = str(parse_rate(str(value)))
        elif key in ("interaction_limit", "topup_interactions"):
            value = int(value)
            if not (1 <= value <= MAX_INTERACTIONS):
                raise ValueError(f"{key} must be between 1 and {MAX_INTERACTIONS}")
        elif key != "enforce" and int(value) < 0:
            raise ValueError(f"{key} must not be negative")
        setattr(r, key, value)
    if not (r.cost_warn_pence <= r.cost_target_pence <= r.cost_critical_pence):
        db.rollback()
        raise ValueError("cost thresholds must be in order: warning <= target <= critical")
    r.updated_at = utcnow()
    r.updated_by = actor[:80]
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def public(r: BillingSettings) -> dict:
    return {k: getattr(r, k) for k in EDITABLE} | {"updated_at": r.updated_at, "updated_by": r.updated_by}
