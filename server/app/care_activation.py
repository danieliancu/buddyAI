"""ola Care starts when the watch is paired, not at purchase.

A watch-only order (Checkout mode=payment, card saved for off-session use with the buyer's recorded
consent) creates one CareActivation row per account in `awaiting_pairing`. The first successful server-side
pairing of a watch to that account (DeviceHub.pair from the customer or operator endpoint) calls
`activate()`, which creates the Stripe subscription with the configured trial on the saved card.

Safety:
- Claim with a lease: one conditional UPDATE moves the row to `activating` (from awaiting_pairing,
  failed, or an `activating` row whose lease expired). Double clicks, concurrent pairing, a second watch
  and refreshes get 0 rows and do nothing. A crash while `activating` is recovered once the lease ends.
- Stripe is checked before every create: a subscription already carrying this activation id (created
  just before a crash, or by an earlier attempt) is adopted instead of creating another. The create call
  also carries an idempotency key.
- The `customer.subscription.*` webhook completes the row too (`complete_from_subscription`).
- Eligibility is re-checked on each attempt: complimentary access, internal accounts and accounts that
  already had a Stripe subscription (trial used / already paying) get no new trial (`not_eligible`).
- A failure never reports an active plan: the row is `failed` with a short code, the watch stays paired,
  the customer can retry, and `recover()` retries with backoff (max MAX_AUTO_ATTEMPTS).
Displayed plan status always comes from the Subscription rows that webhooks keep in sync.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import stripe
from sqlalchemy import and_, or_, update
from sqlmodel import Session, col, select

from app import accounts, email
from app.config import get_settings
from app.db.models import Account, BillingConsent, CareActivation, Device, Order, Subscription, utcnow
from app.db.session import session_scope

log = logging.getLogger(__name__)

LEASE = timedelta(seconds=120)
MAX_AUTO_ATTEMPTS = 5
RETRY_BASE = timedelta(minutes=5)
RECOVER_INTERVAL_S = 300.0
RECOVER_FIRST_DELAY_S = 60.0
ELIGIBLE_ORDER_STATUSES = ("paid", "shipped", "delivered")


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


# --- Stripe calls (one object so tests can replace it) ------------------------------------------------


class StripeGateway:
    def _key(self) -> str:
        return get_settings().stripe_secret_key

    def find_subscription(self, customer: str, activation_id: int) -> dict | None:
        subs = stripe.Subscription.list(customer=customer, status="all", limit=100, api_key=self._key())
        for sub in subs.auto_paging_iter():
            if str((sub.get("metadata") or {}).get("activation_id") or "") == str(activation_id):
                return sub.to_dict()
        return None

    def default_payment_method(self, customer: str) -> str | None:
        cus = stripe.Customer.retrieve(customer, api_key=self._key())
        pm = (cus.get("invoice_settings") or {}).get("default_payment_method")
        return pm if isinstance(pm, str) else (pm or {}).get("id") if pm else None

    def set_default_payment_method(self, customer: str, pm: str) -> None:
        stripe.Customer.modify(customer, invoice_settings={"default_payment_method": pm}, api_key=self._key())

    def create_subscription(self, params: dict[str, Any], idempotency_key: str) -> dict:
        return stripe.Subscription.create(api_key=self._key(), idempotency_key=idempotency_key, **params).to_dict()


gateway: StripeGateway = StripeGateway()


# --- eligibility -----------------------------------------------------------------------------------


def paid_watch_order(db: Session, account_id: int) -> Order | None:
    return db.exec(
        select(Order).where(Order.account_id == account_id, col(Order.status).in_(ELIGIBLE_ORDER_STATUSES)).order_by(col(Order.id).desc())
    ).first()


def can_set_up_watch(db: Session, acc: Account) -> bool:
    """Watch setup (onboarding + customer pairing) needs a paid watch order. Accounts that already have a
    watch, any ola Care subscription (incl. complimentary pilots) or are internal keep working as before.
    Without Stripe (development / private use) nothing is gated."""
    if not get_settings().billing_enabled or acc.internal:
        return True
    if paid_watch_order(db, acc.id) is not None:
        return True
    if db.exec(select(Subscription.id).where(Subscription.account_id == acc.id)).first() is not None:
        return True
    return db.exec(select(Device.id).where(Device.account_id == acc.id, col(Device.revoked_at).is_(None))).first() is not None


def has_paired_watch(db: Session, account_id: int) -> bool:
    return db.exec(
        select(Device.id).where(Device.account_id == account_id, col(Device.paired_at).is_not(None), col(Device.revoked_at).is_(None))
    ).first() is not None


def ineligible_reason(db: Session, acc: Account | None) -> str | None:
    """Why this account must not get a new ola Care trial (None = eligible)."""
    from app import billing

    if acc is None or acc.status != "active":
        return "account_inactive"
    if acc.internal:
        return "internal"
    subs = db.exec(select(Subscription).where(Subscription.account_id == acc.id)).all()
    if any(s.source == "complimentary" and billing.is_entitled(s) for s in subs):
        return "complimentary"
    if any(s.source == "stripe" for s in subs):
        return "trial_used"  # already paying, or had a Stripe subscription (and its trial) before
    return None


# --- created by the paid-order webhook ------------------------------------------------------------------


def ensure_for_paid_order(
    db: Session, acc: Account, order: Order, consent: BillingConsent | None, customer: str | None, pm: str | None
) -> CareActivation:
    """Called for every paid watch-only order. Never creates a Stripe subscription."""
    row = db.exec(select(CareActivation).where(CareActivation.account_id == acc.id)).first()
    consented = consent is not None and consent.status == "accepted"
    if row is None:
        row = CareActivation(
            account_id=acc.id,
            order_id=order.id,
            consent_id=consent.id if consent else None,
            stripe_customer_id=customer or "",
            payment_method_id=pm,
            currency=order.currency or "gbp",
        )
        if not consented:
            row.status, row.reason = "not_eligible", "no_consent"
        elif not customer:
            row.status, row.reason = "not_eligible", "no_customer"
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    if row.status in ("awaiting_pairing", "failed") and consented and customer:
        # A second watch bought before the first was paired: use the newest card and consent.
        row.order_id, row.consent_id, row.stripe_customer_id = order.id, consent.id, customer  # type: ignore[union-attr]
        row.payment_method_id, row.currency, row.updated_at = pm, order.currency or row.currency, utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


# --- activation -----------------------------------------------------------------------------------------


def claim(db: Session, account_id: int, now: datetime | None = None) -> CareActivation | None:
    now = now or utcnow()
    result = db.exec(  # type: ignore[call-overload]
        update(CareActivation)
        .where(
            col(CareActivation.account_id) == account_id,
            or_(
                col(CareActivation.status).in_(("awaiting_pairing", "failed")),
                and_(col(CareActivation.status) == "activating", col(CareActivation.lease_until) < now),
            ),
        )
        .values(status="activating", lease_until=now + LEASE, attempts=CareActivation.attempts + 1, updated_at=now)
    )
    db.commit()
    if result.rowcount != 1:
        return None
    row = db.exec(select(CareActivation).where(CareActivation.account_id == account_id)).first()
    if row is not None:
        db.refresh(row)
    return row


def _finish(db: Session, row: CareActivation, status: str, *, reason: str = "", error: str = "") -> None:
    now = utcnow()
    row.status, row.lease_until, row.updated_at = status, None, now
    if reason:
        row.reason = reason
    row.last_error_code = error
    row.next_retry_at = now + RETRY_BASE * (2 ** max(0, row.attempts - 1)) if status == "failed" else None
    if status == "active":
        row.activated_at = row.activated_at or now
    db.add(row)
    db.commit()
    db.refresh(row)


def _subscription_params(db: Session, row: CareActivation, pm: str) -> dict[str, Any]:
    s = get_settings()
    price = getattr(s, f"stripe_price_care_{row.currency}", "") or s.stripe_price_care_gbp
    if not price:
        raise LookupError("no ola Care price configured")
    meta = {"account_id": str(row.account_id), "activation_id": str(row.id), "source": "care-activation"}
    if row.consent_id:
        meta["consent_id"] = str(row.consent_id)
    return {
        "customer": row.stripe_customer_id,
        "items": [{"price": price, "quantity": 1}],
        "trial_period_days": s.care_trial_days,
        "default_payment_method": pm,
        "off_session": True,
        "payment_settings": {"save_default_payment_method": "on_subscription"},
        "trial_settings": {"end_behavior": {"missing_payment_method": "cancel"}},
        "automatic_tax": {"enabled": True},
        "metadata": meta,
    }


def _adopt(db: Session, row: CareActivation, sub: dict) -> None:
    from app import billing

    billing.upsert_subscription(db, sub | {"metadata": (sub.get("metadata") or {}) | {"account_id": str(row.account_id)}})
    db.refresh(row)
    row.stripe_subscription_id = sub["id"]
    _finish(db, row, "active")


def activate_sync(account_id: int, *, require_watch: bool = True) -> str:
    """Try to start ola Care for this account. Returns the row status afterwards ("" if none)."""
    with session_scope() as db:
        row = claim(db, account_id)
        if row is None:
            current = db.exec(select(CareActivation).where(CareActivation.account_id == account_id)).first()
            return current.status if current else ""
        if require_watch and not has_paired_watch(db, account_id):
            row.status, row.lease_until, row.attempts = "awaiting_pairing", None, max(0, row.attempts - 1)
            db.add(row)
            db.commit()
            return row.status
        if not row.stripe_customer_id:
            _finish(db, row, "not_eligible", reason="no_customer")
            return row.status
        # 1. Anything Stripe already created for this activation wins (crash / ambiguous failure).
        try:
            existing = gateway.find_subscription(row.stripe_customer_id, row.id)
        except stripe.StripeError as exc:
            log.warning("care activation %s: Stripe lookup failed (%s)", row.id, type(exc).__name__)
            _finish(db, row, "failed", error="stripe_unreachable")
            return row.status
        if existing is not None:
            _adopt(db, row, existing)
            return row.status
        # 2. No new trial for complimentary, internal or trial-used accounts.
        reason = ineligible_reason(db, db.get(Account, account_id))
        if reason:
            _finish(db, row, "not_eligible", reason=reason)
            return row.status
        # 3. Create, on the card saved at purchase (or the customer's current default card).
        try:
            pm = row.payment_method_id or gateway.default_payment_method(row.stripe_customer_id)
            if not pm:
                _finish(db, row, "failed", error="missing_payment_method")
                return row.status
            params = _subscription_params(db, row, pm)
            gateway.set_default_payment_method(row.stripe_customer_id, pm)
            sub = gateway.create_subscription(params, idempotency_key=f"care-activation-{row.id}-{pm}")
        except stripe.CardError:
            _finish(db, row, "failed", error="card_error")
            return row.status
        except stripe.InvalidRequestError as exc:
            log.warning("care activation %s: request refused (%s)", row.id, getattr(exc, "code", None))
            _finish(db, row, "failed", error="payment_method_invalid" if "payment_method" in str(getattr(exc, "param", "") or "") else "stripe_error")
            return row.status
        except (stripe.StripeError, LookupError) as exc:
            log.warning("care activation %s: Stripe error (%s)", row.id, type(exc).__name__)
            _finish(db, row, "failed", error="stripe_error")
            return row.status
        _adopt(db, row, sub)
        acc = db.get(Account, account_id)
        trial_end = datetime.fromtimestamp(int(sub["trial_end"]), tz=timezone.utc) if sub.get("trial_end") else None
        to, currency = (acc.email if acc else ""), row.currency
    if to:
        asyncio_send(email.care_trial_started(to, trial_end, *_price_for(currency)))
    return "active"


def _price_for(currency: str) -> tuple[int, str, str]:
    from app import care_terms

    try:
        p = care_terms.care_price(currency)
        return p.amount_minor, p.currency, p.interval
    except Exception:  # noqa: BLE001 - the email falls back to the plan price
        with session_scope() as db:
            from app.plan import get_plan

            return get_plan(db).care_price_pence, "gbp", "month"


def asyncio_send(message: email.Email) -> None:
    """Send from sync code without breaking the caller (worker thread, or a thread with a running loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(email.send(message))
        return
    t = threading.Thread(target=lambda: asyncio.run(email.send(message)), daemon=True)
    t.start()
    t.join(timeout=30)


async def activate(account_id: int, *, require_watch: bool = True) -> str:
    return await asyncio.to_thread(activate_sync, account_id, require_watch=require_watch)


def complete_from_subscription(db: Session, sub: dict[str, Any]) -> None:
    """Webhook: a subscription created by an activation (metadata.activation_id) completes the row."""
    raw = str((sub.get("metadata") or {}).get("activation_id") or "")
    if not raw.isdigit():
        return
    row = db.get(CareActivation, int(raw))
    if row is None or row.status == "active":
        return
    row.stripe_subscription_id = sub.get("id")
    _finish(db, row, "active")


def recover_sync(now: datetime | None = None) -> int:
    """Retry stuck (`activating` with an expired lease) and failed activations of paired watches."""
    now = now or utcnow()
    with session_scope() as db:
        rows = db.exec(
            select(CareActivation).where(
                or_(
                    and_(col(CareActivation.status) == "activating", col(CareActivation.lease_until) < now),
                    and_(
                        col(CareActivation.status) == "failed",
                        col(CareActivation.attempts) < MAX_AUTO_ATTEMPTS,
                        or_(col(CareActivation.next_retry_at).is_(None), col(CareActivation.next_retry_at) <= now),
                    ),
                )
            )
        ).all()
        ids = [r.account_id for r in rows if has_paired_watch(db, r.account_id)]
    for account_id in ids:
        try:
            activate_sync(account_id)
        except Exception:  # noqa: BLE001
            log.warning("care activation recovery failed for account %s", account_id, exc_info=True)
    return len(ids)


async def recover_loop() -> None:
    await asyncio.sleep(RECOVER_FIRST_DELAY_S)  # shortly after start-up (a crashed activation's lease has ended)
    while True:
        try:
            if get_settings().billing_enabled:
                n = await asyncio.to_thread(recover_sync)
                if n:
                    log.info("care activation: %s activation(s) retried", n)
        except Exception:  # noqa: BLE001
            log.warning("care activation recovery loop failed", exc_info=True)
        await asyncio.sleep(RECOVER_INTERVAL_S)


# --- customer view --------------------------------------------------------------------------------------


def public_state(db: Session, account_id: int) -> dict[str, Any] | None:
    row = db.exec(select(CareActivation).where(CareActivation.account_id == account_id)).first()
    if row is None:
        return None
    now = utcnow()
    status = row.status
    if status == "activating" and (_aware(row.lease_until) or now) < now:
        status = "failed"  # stuck: shown as needing a retry, recovered in the background
    return {
        "status": status,
        "reason": row.reason or None,
        "error": row.last_error_code or None,
        "can_retry": status == "failed",
        "trial_days": get_settings().care_trial_days,
        "currency": row.currency,
        "activated_at": row.activated_at,
    }


def audit_retry(db: Session, actor: str, account_id: int) -> None:
    accounts.audit(db, actor, "care.activation.retry", account_id)
