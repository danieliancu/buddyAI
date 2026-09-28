"""Stripe shop + "BuddyAI Care" subscription.

One Checkout Session sells the watch (one-time price, charged now) together with the monthly
subscription (free trial first). Stripe Tax computes UK VAT / EU VAT; addresses are collected
for UK + EU shipping. Webhooks keep orders, subscriptions and accounts in sync; each event id is
processed once.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import stripe
from sqlmodel import Session, select

from app import accounts, email
from app.config import get_settings
from app.db.models import Account, Order, StripeEvent, Subscription, utcnow

log = logging.getLogger(__name__)

ENTITLED_STATUSES = {"trialing", "active", "past_due"}  # past_due: Stripe is still retrying the card


class BillingError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None


def currency_for(country: str | None) -> str:
    return "gbp" if (country or "").upper() in ("GB", "UK") else "eur"


def _prices(currency: str) -> tuple[str, str, list[str]]:
    s = get_settings()
    watch = getattr(s, f"stripe_price_watch_{currency}")
    care = getattr(s, f"stripe_price_care_{currency}")
    rates = [r.strip() for r in getattr(s, f"stripe_shipping_rates_{currency}").split(",") if r.strip()]
    if not (watch and care):
        raise BillingError(503, f"the shop is not configured for {currency.upper()} yet")
    return watch, care, rates


def create_checkout(currency: str, customer_email: str | None = None) -> str:
    """Returns the Stripe Checkout URL."""
    s = get_settings()
    if not s.billing_enabled:
        raise BillingError(503, "the shop is not open yet")
    currency = currency.lower()
    if currency not in ("gbp", "eur"):
        raise BillingError(422, "currency must be GBP or EUR")
    watch, care, rates = _prices(currency)
    site = (s.site_url or s.app_url or f"http://localhost:{s.port}").rstrip("/")
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": care, "quantity": 1}, {"price": watch, "quantity": 1}],
        "subscription_data": {"trial_period_days": s.care_trial_days},
        "automatic_tax": {"enabled": True},
        "shipping_address_collection": {"allowed_countries": [c.strip() for c in s.ship_countries.split(",")]},
        "billing_address_collection": "required",
        "phone_number_collection": {"enabled": True},  # carriers need it for delivery
        "allow_promotion_codes": True,
        "consent_collection": {"terms_of_service": "required"},  # needs the ToS URL set in the Stripe dashboard
        "success_url": f"{site}/thank-you?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{site}/#buy",
        "metadata": {"source": "buddyai-site"},
    }
    if rates:
        params["shipping_options"] = [{"shipping_rate": r} for r in rates]
    if customer_email:
        params["customer_email"] = customer_email
    try:
        session = stripe.checkout.Session.create(api_key=s.stripe_secret_key, **params)
    except stripe.StripeError as exc:
        log.warning("checkout creation failed: %s", exc)
        raise BillingError(502, "payment provider unavailable, please try again") from exc
    return session["url"]


def portal_url(account: Account) -> str:
    s = get_settings()
    if not (s.billing_enabled and account.stripe_customer_id):
        raise BillingError(404, "no subscription for this account")
    base = (s.app_url or f"http://localhost:{s.port}").rstrip("/")
    try:
        portal = stripe.billing_portal.Session.create(
            api_key=s.stripe_secret_key, customer=account.stripe_customer_id, return_url=f"{base}/my/account"
        )
    except stripe.StripeError as exc:
        raise BillingError(502, "payment provider unavailable, please try again") from exc
    return portal["url"]


def verify_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    s = get_settings()
    if not s.stripe_webhook_secret:
        raise BillingError(503, "webhook secret not configured")
    try:
        stripe.WebhookSignature.verify_header(payload.decode("utf-8"), signature, s.stripe_webhook_secret)
    except (stripe.SignatureVerificationError, ValueError) as exc:
        raise BillingError(400, "invalid signature") from exc
    return json.loads(payload)


# --- webhook handling (plain dicts: testable without Stripe) --------------------------------------


def _fetch_subscription(sub_id: str) -> dict[str, Any]:
    s = get_settings()
    return stripe.Subscription.retrieve(sub_id, api_key=s.stripe_secret_key).to_dict()


def _period_end(sub: dict[str, Any]) -> datetime | None:
    if sub.get("current_period_end"):
        return _ts(sub["current_period_end"])
    items = (sub.get("items") or {}).get("data") or []  # newer API versions keep it on the item
    return _ts(items[0].get("current_period_end")) if items else None


def upsert_subscription(db: Session, sub: dict[str, Any]) -> Subscription:
    row = db.exec(select(Subscription).where(Subscription.stripe_subscription_id == sub["id"])).first()
    customer = sub.get("customer") or ""
    acc = db.exec(select(Account).where(Account.stripe_customer_id == customer)).first() if customer else None
    row = row or Subscription(stripe_subscription_id=sub["id"], stripe_customer_id=customer, status=sub.get("status", ""))
    row.stripe_customer_id = customer or row.stripe_customer_id
    row.account_id = acc.id if acc else row.account_id
    row.status = sub.get("status", row.status)
    row.trial_end = _ts(sub.get("trial_end"))
    row.current_period_end = _period_end(sub)
    row.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


async def _checkout_completed(db: Session, cs: dict[str, Any], fetch_subscription: Callable[[str], dict]) -> None:
    if db.exec(select(Order).where(Order.stripe_session_id == cs["id"])).first():
        return
    details = cs.get("customer_details") or {}
    addr_email = (details.get("email") or cs.get("customer_email") or "").lower()
    shipping = (cs.get("collected_information") or {}).get("shipping_details") or cs.get("shipping_details") or {}
    address = shipping.get("address") or details.get("address") or {}

    acc = accounts.by_email(db, addr_email) if addr_email else None
    new_account = acc is None
    if acc is None:
        acc = accounts.create(db, addr_email, None, details.get("name") or "", address.get("country"))
    if cs.get("customer") and acc.stripe_customer_id != cs["customer"]:
        acc.stripe_customer_id = cs["customer"]
        db.add(acc)
        db.commit()
        db.refresh(acc)

    totals = cs.get("total_details") or {}
    order = Order(
        stripe_session_id=cs["id"],
        stripe_payment_intent=cs.get("payment_intent"),
        account_id=acc.id,
        email=addr_email,
        currency=(cs.get("currency") or "").lower(),
        amount_total=int(cs.get("amount_total") or 0),
        amount_tax=int(totals.get("amount_tax") or 0),
        amount_shipping=int(totals.get("amount_shipping") or 0),
        shipping_name=shipping.get("name") or details.get("name") or "",
        shipping_address=address,
        country=address.get("country"),
    )
    db.add(order)
    db.commit()
    if cs.get("subscription"):
        upsert_subscription(db, fetch_subscription(cs["subscription"]))

    if new_account or acc.password_hash is None:
        token = accounts.issue_token(db, acc, "reset_password")
        await email.send(email.welcome_set_password(acc.email, accounts.link("/reset-password", token)))
    await email.send(email.order_confirmed(acc.email, order.id, order.amount_total, order.currency))


async def handle_event(
    db: Session, event: dict[str, Any], fetch_subscription: Callable[[str], dict] = _fetch_subscription
) -> bool:
    """Apply one webhook event. Returns False if it was already processed."""
    if db.get(StripeEvent, event["id"]):
        return False
    kind = event["type"]
    obj = event["data"]["object"]
    if kind == "checkout.session.completed":
        await _checkout_completed(db, obj, fetch_subscription)
    elif kind == "customer.subscription.trial_will_end":
        # Stripe sends this 3 days before the trial ends. UK subscription rules require a reminder
        # before the first paid period; the subscription terms promise it.
        row = upsert_subscription(db, obj)
        acc = db.get(Account, row.account_id) if row.account_id else None
        if acc and acc.status == "active" and not row.cancel_at_period_end:
            await email.send(email.trial_ending(acc.email, row.trial_end))
    elif kind.startswith("customer.subscription."):
        upsert_subscription(db, obj)
    elif kind == "invoice.payment_failed":
        acc = db.exec(select(Account).where(Account.stripe_customer_id == obj.get("customer"))).first()
        if acc:
            await email.send(email.payment_failed(acc.email))
    elif kind == "charge.refunded":
        order = db.exec(select(Order).where(Order.stripe_payment_intent == obj.get("payment_intent"))).first()
        if order and obj.get("refunded"):
            order.status = "refunded"
            db.add(order)
            db.commit()
    db.add(StripeEvent(id=event["id"], type=kind))
    db.commit()
    return True


# --- entitlement ------------------------------------------------------------------------------------


def active_subscription(db: Session, account_id: int) -> Subscription | None:
    subs = db.exec(select(Subscription).where(Subscription.account_id == account_id)).all()
    live = [s for s in subs if s.status in ENTITLED_STATUSES]
    return live[0] if live else (subs[0] if subs else None)
