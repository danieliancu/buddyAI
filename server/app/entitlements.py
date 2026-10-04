"""May this watch start a turn? Subscription status + the account's AI allowance.

The decision and the reservation live in the database (app/usage_ops.py: one usage_operations row per AI
operation, admitted under the account row lock), so every server process sees the same budget. This module
keeps the read-only helpers used by screens and tools.

Always allowed: a watch without an owner (operator stock), an internal account, or when enforcement is
off (no Stripe keys and the operator has not enabled it). Otherwise the account needs an entitled
subscription (Stripe trialing/active/past_due, or an unexpired complimentary grant) and allowance left.
A turn that has started always finishes (the last answer may go slightly over; it is never cut off).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app import allowance as allowance_mod
from app import billing, usage_notices, usage_ops
from app.db.models import Account
from app.db.session import session_scope

Allowance = allowance_mod.Allowance


@dataclass
class Decision:
    allowed: bool
    code: str | None = None  # subscription_required | limit_reached | account_inactive | service_unavailable
    message: str = ""


def allowance(db, acc: Account) -> Allowance:
    """The account's current allowance (operator views, customer plan); reserved = running operations."""
    sub = billing.active_subscription(db, acc.id)
    return allowance_mod.compute(db, acc, sub)


def reserved_micro(account_id: int) -> int:
    with session_scope() as db:
        acc = db.get(Account, account_id)
        return allowance(db, acc).reserved_micro if acc is not None else 0


async def check(account_id: int | None) -> Decision:
    """Entitlement preview without a reservation (never used to admit an operation)."""
    try:
        a = await asyncio.to_thread(usage_ops.preview, account_id)
    except Exception:  # noqa: BLE001 - database unavailable
        return Decision(False, usage_ops.SERVICE_UNAVAILABLE, usage_ops.MESSAGES[usage_ops.SERVICE_UNAVAILABLE])
    if a.allowed and account_id is not None:
        await usage_notices.evaluate(account_id)  # thresholds reached meanwhile (recorded once per period)
    return Decision(a.allowed, a.code, a.message)
