"""May this watch start a turn? Subscription status + the account's AI allowance.

Always allowed: a watch without an owner (operator stock), an internal account, or when enforcement is
off (no Stripe keys and the operator has not enabled it). Otherwise the account needs an entitled
subscription (Stripe trialing/active/past_due, or an unexpired complimentary grant) and allowance left.

The allowance is per account, shared by its watches. Costs are written when a turn ends, so a running
turn holds a small reservation (plan.reserve_pence): the check + reservation happen under one lock per
account. A single watch is refused only when the allowance is used up (100 %); while other turns of the
account are running, a new one needs room for all their reservations plus its own. A turn that has
started always finishes (the last answer may go slightly over; it is never cut off).
Single-process state: running several server workers would need a shared store (docs/BILLING.md).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app import allowance as allowance_mod
from app import billing, usage_notices
from app.db.models import Account
from app.db.session import session_scope
from app.money import pence_to_micro
from app.plan import get_plan

Allowance = allowance_mod.Allowance

_locks: dict[int, asyncio.Lock] = {}
_reserved: dict[int, dict[str, int]] = {}  # account id -> {turn key: micro-GBP}


@dataclass
class Decision:
    allowed: bool
    code: str | None = None  # subscription_required | limit_reached
    message: str = ""


def allowance(db, acc: Account) -> Allowance:
    """The account's current allowance (operator views, customer plan)."""
    sub = billing.active_subscription(db, acc.id)
    return allowance_mod.compute(db, acc, sub, reserved_micro=sum(_reserved.get(acc.id, {}).values()))


def reserved_micro(account_id: int) -> int:
    return sum(_reserved.get(account_id, {}).values())


async def begin_turn(account_id: int | None, key: str | None) -> Decision:
    """Check the entitlement and, when allowed, reserve allowance for this turn (release with end_turn)."""
    if account_id is None:
        return Decision(True)
    lock = _locks.setdefault(account_id, asyncio.Lock())
    async with lock:
        with session_scope() as db:
            acc = db.get(Account, account_id)
            if acc is None or acc.internal:
                return Decision(True)
            plan = get_plan(db)
            if not plan.enforced:
                return Decision(True)
            sub = billing.active_subscription(db, acc.id)
            if sub is None or not billing.is_entitled(sub):
                return Decision(False, "subscription_required", "ola Care subscription needed")
            others = sum(v for k, v in _reserved.get(account_id, {}).items() if k != key)
            a = allowance_mod.compute(db, acc, sub, plan, reserved_micro=others)
            own = pence_to_micro(plan.reserve_pence)
            if a.used_micro >= a.limit_micro or (others and a.used_micro + others + own > a.limit_micro):
                return Decision(False, "limit_reached", "allowance for this period used up")
            if key is not None:
                _reserved.setdefault(account_id, {})[key] = pence_to_micro(plan.reserve_pence)
    await usage_notices.evaluate(account_id)
    return Decision(True)


def end_turn(account_id: int | None, key: str) -> None:
    if account_id is not None:
        held = _reserved.get(account_id)
        if held is not None:
            held.pop(key, None)
            if not held:
                _reserved.pop(account_id, None)


async def check(account_id: int | None) -> Decision:
    """Entitlement check without a reservation."""
    return await begin_turn(account_id, None)
