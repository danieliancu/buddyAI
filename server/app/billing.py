"""Stripe shop, "ola Care" subscription, extra-usage top-ups, complimentary grants.

- Shop: one Checkout Session (mode=payment) sells the watch (one-time price, charged now) and saves the
  card for ola Care with the buyer's recorded consent (BillingConsent). No subscription is created at
  purchase: the Care trial starts when the watch is paired (app/care_activation.py). Orders and accounts
  are created only from verified webhooks of paid sessions; delayed payment methods wait for
  async_payment_succeeded. Stripe Tax computes UK VAT / EU VAT; addresses are collected for UK + EU
  shipping. Legacy sessions (watch + Care trial in one subscription Checkout) are still handled.
- Existing accounts can subscribe from the app (subscription-only Checkout).
- Top-ups: one-time Checkout (mode=payment, never recurring) for extra allowance in the current
  period. A pending TopUp row is created first; it is granted only by a verified webhook whose
  session is paid, matches the row's account and amount, and flips it from pending exactly once.
  Refund policy: a refunded top-up's allowance is withdrawn (docs/BILLING.md).
- Complimentary grants: operator-assigned pilot/test entitlements without any Stripe object.
- Webhooks keep orders, subscriptions, top-ups, revenue and accounts in sync. Each event id is claimed
  before it is handled (a unique row), so concurrent or repeated deliveries run it once; a failed
  handler releases the claim so Stripe's retry can apply it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import stripe
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app import accounts, care_activation, care_terms, email
from app.account_lock import lock_account
from app.config import get_settings
from app.db.models import Account, BillingConsent, CareActivation, Order, RevenueEvent, StripeEvent, Subscription, TopUp, utcnow
from app.plan import get_plan

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


@dataclass(frozen=True)
class SiteConsent:
    """What the buyer ticked on the site before checkout (the request that started it)."""

    version: str
    sha256: str
    ip: str = ""
    user_agent: str = ""


def create_checkout(db: Session, currency: str, consent: SiteConsent, customer_email: str | None = None) -> str:
    """Watch-only Checkout: the watch is charged once now and the card is saved (off-session) for ola Care,
    whose trial starts when the watch is paired (app/care_activation.py). No subscription is created here.
    The buyer must have accepted the current Care terms on the site (version + hash checked). Returns the URL."""
    s = get_settings()
    if not s.billing_enabled:
        raise BillingError(503, "the shop is not open yet")
    currency = currency.lower()
    if currency not in ("gbp", "eur"):
        raise BillingError(422, "currency must be GBP or EUR")
    watch, _care, rates = _prices(currency)
    try:
        terms = care_terms.current(currency)
    except (LookupError, stripe.StripeError) as exc:
        log.warning("ola Care terms unavailable for %s: %s", currency, type(exc).__name__)
        raise BillingError(503, "the shop is not configured for this currency yet") from exc
    if consent.version != terms.version or consent.sha256 != terms.sha256:
        raise BillingError(409, "the ola Care terms have changed, please reload the page and review them")
    site = (s.site_url or s.app_url or f"http://localhost:{s.port}").rstrip("/")
    meta = {
        "source": "buddyai-site",
        "kind": "watch",
        "care_terms_version": terms.version,
        "care_terms_sha256": terms.sha256,
    }
    params: dict[str, Any] = {
        "mode": "payment",  # the watch, once; ola Care is a separate subscription created at pairing
        "line_items": [{"price": watch, "quantity": 1}],
        "customer_creation": "always",  # the saved card belongs to a Stripe Customer
        "payment_intent_data": {
            "setup_future_usage": "off_session",
            "description": "ola watch (card saved for ola Care, trial starts when the watch is paired)",
            "metadata": meta,
        },
        "automatic_tax": {"enabled": s.stripe_tax_on},
        "shipping_address_collection": {"allowed_countries": [c.strip() for c in s.ship_countries.split(",")]},
        "billing_address_collection": "required",
        "phone_number_collection": {"enabled": True},  # carriers need it for delivery
        "allow_promotion_codes": True,
        "success_url": f"{site}/thank-you?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{site}/#buy",
        "metadata": meta,
    }
    submit_note = "You pay for the watch today. ola Care is not charged today: its free trial starts when you pair your watch."
    if s.stripe_tos_on:
        params["consent_collection"] = {"terms_of_service": "required"}  # needs the ToS URL in the Stripe dashboard
        params["custom_text"] = {"terms_of_service_acceptance": {"message": terms.text}, "submit": {"message": submit_note}}
    else:  # local tests without a ToS URL: the agreed terms stay visible above the Pay button
        params["custom_text"] = {"submit": {"message": f"{terms.text} {submit_note}"}}
    if rates:
        params["shipping_options"] = [{"shipping_rate": r} for r in rates]
    if customer_email:
        params["customer_email"] = customer_email
    try:
        session = stripe.checkout.Session.create(api_key=s.stripe_secret_key, **params)
    except stripe.StripeError as exc:
        log.warning("checkout creation failed: %s", type(exc).__name__)
        raise BillingError(502, "payment provider unavailable, please try again") from exc
    db.add(
        BillingConsent(
            stripe_checkout_session_id=session["id"],
            terms_version=terms.version,
            terms_text=terms.text,
            terms_sha256=terms.sha256,
            amount_minor=terms.price.amount_minor,
            currency=currency,
            interval=terms.price.interval,
            trial_days=terms.trial_days,
            site_accepted_at=utcnow(),
            site_ip=consent.ip[:64],
            site_user_agent=consent.user_agent[:300],
        )
    )
    db.commit()
    return session["url"]


def _app_base() -> str:
    s = get_settings()
    return (s.app_url or f"http://localhost:{s.port}").rstrip("/")


def _stripe_customer(account: Account) -> dict[str, Any]:
    return {"customer": account.stripe_customer_id} if account.stripe_customer_id else {"customer_email": account.email}


def create_subscription_checkout(db: Session, account: Account) -> str:
    """ola Care for an existing account (no watch in the basket), paid from the first month: the
    free trial belongs to the watch + Care bundle sold in the shop. Returns the Checkout URL."""
    s = get_settings()
    if not s.billing_enabled or not s.stripe_price_care_gbp:
        raise BillingError(503, "subscriptions are not available yet")
    current = active_subscription(db, account.id)
    if current is not None and is_entitled(current) and current.source == "stripe":
        raise BillingError(409, "this account already has ola Care")
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": s.stripe_price_care_gbp, "quantity": 1}],
        "client_reference_id": str(account.id),
        "metadata": {"kind": "subscription", "account_id": str(account.id)},
        "subscription_data": {"metadata": {"account_id": str(account.id)}},
        "automatic_tax": {"enabled": s.stripe_tax_on},
        "success_url": f"{_app_base()}/my/account?subscribed=1",
        "cancel_url": f"{_app_base()}/my/account",
        **_stripe_customer(account),
    }
    try:
        session = stripe.checkout.Session.create(api_key=s.stripe_secret_key, **params)
    except stripe.StripeError as exc:
        log.warning("subscription checkout failed: %s", exc)
        raise BillingError(502, "payment provider unavailable, please try again") from exc
    return session["url"]


def create_topup_checkout(db: Session, account: Account) -> tuple[TopUp, str]:
    """One-time purchase of extra allowance for the current period. Nothing is granted here."""
    from app import allowance as allowance_mod  # avoid an import cycle

    s = get_settings()
    if not s.billing_enabled:
        raise BillingError(503, "extra usage cannot be bought yet")
    sub = active_subscription(db, account.id)
    if sub is None or not is_entitled(sub):
        raise BillingError(409, "extra usage needs an active ola Care plan")
    plan = get_plan(db)
    pid = allowance_mod.period_identity(sub)
    topup = TopUp(
        account_id=account.id,
        amount_pence=plan.topup_price_pence,
        allowance_pence=plan.topup_allowance_pence,
        period_start=pid.period.start,
        period_end=pid.period.end,
        period_key=pid.key,  # the period it tops up, even if it is paid after the period rolled over
    )
    db.add(topup)
    db.commit()
    db.refresh(topup)
    meta = {"kind": "topup", "topup_id": str(topup.id), "account_id": str(account.id)}
    params: dict[str, Any] = {
        "mode": "payment",  # one-time: never a recurring charge
        "line_items": [
            {
                "price_data": {
                    "currency": "gbp",
                    "unit_amount": plan.topup_price_pence,
                    "tax_behavior": "inclusive",
                    "product_data": {
                        "name": "ola extra usage",
                        "description": "One-off extra AI usage for your current ola Care period. Not recurring.",
                    },
                },
                "quantity": 1,
            }
        ],
        "client_reference_id": str(account.id),
        "metadata": meta,
        "payment_intent_data": {"metadata": meta},
        "automatic_tax": {"enabled": s.stripe_tax_on},
        "success_url": f"{_app_base()}/my/account?topup=success",
        "cancel_url": f"{_app_base()}/my/account?topup=cancel",
        **_stripe_customer(account),
    }
    if account.stripe_customer_id:
        params["customer_update"] = {"address": "auto"}
    try:
        session = stripe.checkout.Session.create(api_key=s.stripe_secret_key, **params)
    except stripe.StripeError as exc:
        topup.status = "expired"
        db.add(topup)
        db.commit()
        log.warning("top-up checkout failed: %s", exc)
        raise BillingError(502, "payment provider unavailable, please try again") from exc
    topup.stripe_session_id = session["id"]
    db.add(topup)
    db.commit()
    db.refresh(topup)
    return topup, session["url"]


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


def _period(sub: dict[str, Any], field: str) -> datetime | None:
    if sub.get(field):
        return _ts(sub[field])
    items = (sub.get("items") or {}).get("data") or []  # newer API versions keep it on the item
    return _ts(items[0].get(field)) if items else None


def _period_end(sub: dict[str, Any]) -> datetime | None:
    return _period(sub, "current_period_end")


def upsert_subscription(db: Session, sub: dict[str, Any]) -> Subscription:
    row = db.exec(select(Subscription).where(Subscription.stripe_subscription_id == sub["id"])).first()
    customer = sub.get("customer") or ""
    acc = db.exec(select(Account).where(Account.stripe_customer_id == customer)).first() if customer else None
    if acc is None and (sub.get("metadata") or {}).get("account_id"):  # subscribed from the app
        acc = db.get(Account, int(sub["metadata"]["account_id"]))
        lock_account(db, acc.id if acc else None)
        if acc is not None and customer and not acc.stripe_customer_id:
            acc.stripe_customer_id = customer  # billing portal + invoices find the account from now on
            db.add(acc)
            db.commit()
            db.refresh(acc)
    row = row or Subscription(stripe_subscription_id=sub["id"], stripe_customer_id=customer, status=sub.get("status", ""))
    lock_account(db, acc.id if acc else row.account_id)  # periods / status change the budget: admissions wait
    row.source = "stripe"
    row.stripe_customer_id = customer or row.stripe_customer_id
    row.account_id = acc.id if acc else row.account_id
    row.status = sub.get("status", row.status)
    row.trial_end = _ts(sub.get("trial_end"))
    row.current_period_start = _period(sub, "current_period_start")
    row.current_period_end = _period_end(sub)
    row.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _session_parties(cs: dict[str, Any]) -> tuple[str, dict, dict, dict]:
    details = cs.get("customer_details") or {}
    addr_email = (details.get("email") or cs.get("customer_email") or "").lower()
    shipping = (cs.get("collected_information") or {}).get("shipping_details") or cs.get("shipping_details") or {}
    address = shipping.get("address") or details.get("address") or {}
    return addr_email, details, shipping, address


def _account_for_session(db: Session, cs: dict[str, Any]) -> tuple[Account, bool]:
    addr_email, details, _shipping, address = _session_parties(cs)
    acc = accounts.by_email(db, addr_email) if addr_email else None
    if acc is not None:
        return acc, False
    return accounts.create(db, addr_email, None, details.get("name") or "", address.get("country")), True


def _new_order(cs: dict[str, Any], account_id: int | None, status: str = "paid", flow: str | None = None) -> Order:
    addr_email, details, shipping, address = _session_parties(cs)
    totals = cs.get("total_details") or {}
    return Order(
        stripe_session_id=cs["id"],
        stripe_payment_intent=cs.get("payment_intent") if isinstance(cs.get("payment_intent"), str) else None,
        account_id=account_id,
        email=addr_email,
        currency=(cs.get("currency") or "").lower(),
        amount_total=int(cs.get("amount_total") or 0),
        amount_tax=int(totals.get("amount_tax") or 0),
        amount_shipping=int(totals.get("amount_shipping") or 0),
        shipping_name=shipping.get("name") or details.get("name") or "",
        shipping_address=address,
        country=address.get("country"),
        status=status,
        checkout_flow=flow,
    )


async def _checkout_completed(db: Session, cs: dict[str, Any], fetch_subscription: Callable[[str], dict]) -> None:
    """Legacy bundle session (watch + Care subscription with the trial started at purchase)."""
    if db.exec(select(Order).where(Order.stripe_session_id == cs["id"])).first():
        return
    acc, new_account = _account_for_session(db, cs)
    if cs.get("customer") and acc.stripe_customer_id != cs["customer"]:
        acc.stripe_customer_id = cs["customer"]
        db.add(acc)
        db.commit()
        db.refresh(acc)

    order = _new_order(cs, acc.id)
    db.add(order)
    db.commit()
    _revenue(db, cs["id"], acc.id, "watch", order.amount_total, order.currency, order.amount_tax)
    if cs.get("subscription"):
        upsert_subscription(db, fetch_subscription(cs["subscription"]))

    if new_account or acc.password_hash is None:
        await email.send(email.welcome_set_password(acc.email, accounts.welcome_link(db, acc)))
    await email.send(email.order_confirmed(acc.email, order.id, order.amount_total, order.currency))


# --- watch-only sessions (card saved, ola Care started at pairing) ----------------------------------


def _fetch_payment_intent(pi_id: str) -> dict[str, Any]:
    s = get_settings()
    return stripe.PaymentIntent.retrieve(pi_id, api_key=s.stripe_secret_key).to_dict()


def _payment_method_of(cs: dict[str, Any], fetch_payment_intent: Callable[[str], dict]) -> str | None:
    pi = cs.get("payment_intent")
    if isinstance(pi, dict):
        data = pi
    elif pi:
        data = fetch_payment_intent(pi)  # raises on Stripe errors: the event is released and retried
    else:
        return None
    pm = data.get("payment_method")
    return pm.get("id") if isinstance(pm, dict) else pm


def _link_customer(db: Session, acc: Account, customer: str | None) -> None:
    """The Stripe customer that owns the saved card. An account that already pays through another Stripe
    customer keeps it (billing portal, invoices); the activation row stores the card's customer."""
    if not customer or acc.stripe_customer_id == customer:
        return
    has_stripe_sub = db.exec(
        select(Subscription.id).where(Subscription.account_id == acc.id, Subscription.source == "stripe")
    ).first() is not None
    if acc.stripe_customer_id is None or not has_stripe_sub:
        acc.stripe_customer_id = customer
        db.add(acc)
        db.commit()
        db.refresh(acc)


def _complete_consent(
    db: Session, cs: dict[str, Any], acc: Account, order: Order, pm: str | None
) -> BillingConsent | None:
    row = db.exec(select(BillingConsent).where(BillingConsent.stripe_checkout_session_id == cs["id"])).first()
    if row is None:
        return None
    meta = cs.get("metadata") or {}
    tos = (cs.get("consent") or {}).get("terms_of_service")
    row.account_id, row.order_id = acc.id, order.id
    row.stripe_customer_id = cs.get("customer") or row.stripe_customer_id
    row.stripe_payment_method_id = pm or row.stripe_payment_method_id
    tos_required = get_settings().stripe_tos_on
    row.stripe_tos_consent = tos if tos_required else (tos or "not_required")  # local tests: site checkbox only
    row.stripe_consent_at = _ts(cs.get("created")) or utcnow()
    matches = meta.get("care_terms_version") == row.terms_version and meta.get("care_terms_sha256") == row.terms_sha256
    row.status = "accepted" if matches and (tos == "accepted" or not tos_required) else "missing"
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _watch_order_pending(db: Session, cs: dict[str, Any]) -> None:
    """Completed but not paid yet (delayed payment method): an order row only, no account, no email."""
    if db.exec(select(Order).where(Order.stripe_session_id == cs["id"])).first():
        return
    db.add(_new_order(cs, None, status="payment_pending", flow="watch"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


async def _watch_order_paid(db: Session, cs: dict[str, Any], fetch_payment_intent: Callable[[str], dict]) -> None:
    if cs.get("payment_status") not in ("paid", "no_payment_required"):
        _watch_order_pending(db, cs)
        return
    pm = _payment_method_of(cs, fetch_payment_intent)  # before any write: a failure leaves nothing half done
    order = db.exec(select(Order).where(Order.stripe_session_id == cs["id"])).first()
    newly_paid = False
    acc: Account | None = None
    new_account = False
    if order is None or order.status == "payment_pending":
        acc, new_account = _account_for_session(db, cs)
        if order is None:
            order = _new_order(cs, acc.id, status="paid", flow="watch")
            db.add(order)
            try:
                db.commit()
                newly_paid = True
            except IntegrityError:  # a concurrent delivery created it
                db.rollback()
                order = db.exec(select(Order).where(Order.stripe_session_id == cs["id"])).one()
        if not newly_paid and order.status == "payment_pending":
            result = db.exec(  # type: ignore[call-overload]
                update(Order)
                .where(col(Order.id) == order.id, col(Order.status) == "payment_pending")
                .values(status="paid", account_id=acc.id)
            )
            db.commit()
            newly_paid = result.rowcount == 1
        db.refresh(order)
    if order.account_id is None or order.status not in care_activation.ELIGIBLE_ORDER_STATUSES:
        return  # refunded / cancelled meanwhile: nothing to set up
    acc = acc if acc is not None and acc.id == order.account_id else db.get(Account, order.account_id)
    if acc is None:
        return
    _link_customer(db, acc, cs.get("customer"))
    consent = _complete_consent(db, cs, acc, order, pm)
    care_activation.ensure_for_paid_order(db, acc, order, consent, cs.get("customer"), pm)
    if not newly_paid:
        return
    _revenue(db, cs["id"], acc.id, "watch", order.amount_total, order.currency, order.amount_tax)
    accounts.audit(db, "system", "order.paid", acc.id, detail=f"order {order.id} (watch; ola Care starts at pairing)")
    if new_account or acc.password_hash is None:
        await email.send(email.welcome_set_password(acc.email, accounts.welcome_link(db, acc)))
    terms = consent.terms_text if consent is not None and consent.status == "accepted" else None
    await email.send(email.order_confirmed(acc.email, order.id, order.amount_total, order.currency, terms))


async def _watch_order_failed(db: Session, cs: dict[str, Any]) -> None:
    order = db.exec(select(Order).where(Order.stripe_session_id == cs["id"])).first()
    if order is None:
        order = _new_order(cs, None, status="payment_failed", flow="watch")
        db.add(order)
        db.commit()
        db.refresh(order)
        changed = True
    else:
        result = db.exec(  # type: ignore[call-overload]
            update(Order).where(col(Order.id) == order.id, col(Order.status) == "payment_pending").values(status="payment_failed")
        )
        db.commit()
        changed = result.rowcount == 1
    if changed and order.email:
        await email.send(email.order_payment_failed(order.email))


def _watch_session_expired(db: Session, cs: dict[str, Any]) -> None:
    db.exec(  # type: ignore[call-overload]
        update(Order).where(col(Order.stripe_session_id) == cs["id"], col(Order.status) == "payment_pending").values(status="cancelled")
    )
    db.commit()


def checkout_status(db: Session, session_id: str) -> dict[str, Any]:
    """For the thank-you page: only what our verified webhooks recorded, never the redirect itself."""
    order = db.exec(select(Order).where(Order.stripe_session_id == session_id)).first()
    if order is None:
        known = db.exec(select(BillingConsent.id).where(BillingConsent.stripe_checkout_session_id == session_id)).first()
        return {"state": "processing" if known is not None else "unknown", "needs_password": False}
    state = {
        "paid": "paid", "shipped": "paid", "delivered": "paid",
        "payment_pending": "processing", "payment_failed": "failed",
        "cancelled": "cancelled", "refunded": "cancelled",
    }.get(order.status, "unknown")
    acc = db.get(Account, order.account_id) if order.account_id else None
    return {"state": state, "needs_password": bool(state == "paid" and acc is not None and acc.password_hash is None)}


def _revenue(
    db: Session, source_id: str, account_id: int | None, kind: str, amount: int, currency: str, tax: int = 0
) -> None:
    """Record money received (or refunded, negative) once per Stripe object. Amounts are gross; `tax` is
    the VAT included, so the operator's contribution uses net revenue."""
    if db.exec(select(RevenueEvent).where(RevenueEvent.source_id == source_id)).first():
        return
    db.add(RevenueEvent(source_id=source_id, account_id=account_id, kind=kind, amount_pence=int(amount),
                        tax_pence=int(tax or 0), currency=(currency or "gbp").lower()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _topup_for_session(db: Session, cs: dict[str, Any]) -> TopUp | None:
    meta = cs.get("metadata") or {}
    try:
        topup = db.get(TopUp, int(meta.get("topup_id") or 0))
    except (TypeError, ValueError):
        return None
    if topup is None or topup.stripe_session_id not in (None, cs.get("id")):
        return None
    if str(topup.account_id) != str(meta.get("account_id")):
        log.warning("top-up %s: session account %s does not match", topup.id, meta.get("account_id"))
        return None
    return topup


def _topup_paid(db: Session, cs: dict[str, Any]) -> bool:
    """Grant a top-up for a paid session, exactly once. Returns True if this call granted it."""
    topup = _topup_for_session(db, cs)
    if topup is None:
        return False
    if cs.get("payment_status") != "paid":
        return False  # e.g. a delayed payment method: wait for async_payment_succeeded
    if int(cs.get("amount_total") or 0) < topup.amount_pence or (cs.get("currency") or "").lower() != "gbp":
        log.warning("top-up %s: paid amount %s %s does not match", topup.id, cs.get("amount_total"), cs.get("currency"))
        return False
    lock_account(db, topup.account_id)
    result = db.exec(  # type: ignore[call-overload]
        update(TopUp)
        .where(col(TopUp.id) == topup.id, col(TopUp.status) == "pending")
        .values(status="paid", paid_at=utcnow(), stripe_session_id=cs.get("id"), stripe_payment_intent=cs.get("payment_intent"))
    )
    db.commit()
    if result.rowcount != 1:
        return False
    tax = int((cs.get("total_details") or {}).get("amount_tax") or 0)
    _revenue(db, cs["id"], topup.account_id, "topup", int(cs.get("amount_total") or 0), "gbp", tax)
    accounts.audit(db, "system", "topup.paid", topup.account_id, detail=f"top-up {topup.id}: +{topup.allowance_pence}p allowance")
    return True


def _topup_failed(db: Session, cs: dict[str, Any]) -> None:
    topup = _topup_for_session(db, cs)
    if topup is not None:
        lock_account(db, topup.account_id)
        db.exec(update(TopUp).where(col(TopUp.id) == topup.id, col(TopUp.status) == "pending").values(status="expired"))  # type: ignore[call-overload]
        db.commit()


def _refund(db: Session, charge: dict[str, Any]) -> None:
    intent = charge.get("payment_intent")
    if not intent:
        return
    order = db.exec(select(Order).where(Order.stripe_payment_intent == intent)).first()
    if order and charge.get("refunded"):
        order.status = "refunded"
        db.add(order)
        db.commit()
    topup = db.exec(select(TopUp).where(TopUp.stripe_payment_intent == intent)).first()
    if topup is not None:
        lock_account(db, topup.account_id)
        db.refresh(topup)
    if topup and topup.status == "paid":
        # Policy: a refunded top-up's extra allowance is withdrawn for the period (docs/BILLING.md).
        topup.status, topup.refunded_at = "refunded", utcnow()
        db.add(topup)
        db.commit()
        accounts.audit(db, "system", "topup.refunded", topup.account_id, detail=f"top-up {topup.id}")
    account_id = order.account_id if order else (topup.account_id if topup else None)
    if order or topup:
        _revenue(db, f"refund:{charge.get('id')}", account_id, "refund", -int(charge.get("amount_refunded") or 0), charge.get("currency") or "gbp")


def _invoice_paid(db: Session, inv: dict[str, Any]) -> None:
    amount = int(inv.get("amount_paid") or 0)
    if amount <= 0 or not inv.get("id"):
        return  # trial invoices are £0
    acc = db.exec(select(Account).where(Account.stripe_customer_id == inv.get("customer"))).first()
    if acc is None:  # invoice.paid can arrive before the subscription event that links the customer
        meta = (
            ((inv.get("parent") or {}).get("subscription_details") or {}).get("metadata")
            or (inv.get("subscription_details") or {}).get("metadata")
            or {}
        )
        if str(meta.get("account_id") or "").isdigit():
            acc = db.get(Account, int(meta["account_id"]))
    tax = int(inv.get("tax") or sum(int(t.get("amount") or 0) for t in inv.get("total_taxes") or []) or 0)
    _revenue(db, inv["id"], acc.id if acc else None, "subscription", amount, inv.get("currency") or "gbp", tax)


async def _checkout_session(
    db: Session, cs: dict[str, Any], fetch_subscription: Callable[[str], dict], fetch_payment_intent: Callable[[str], dict]
) -> None:
    kind = (cs.get("metadata") or {}).get("kind")
    if kind == "watch":
        await _watch_order_paid(db, cs, fetch_payment_intent)  # waits (pending order) unless paid
        return
    if kind == "topup":
        _topup_paid(db, cs)
        return
    if kind == "subscription":  # an existing account subscribed from the app
        acc = db.get(Account, int(cs.get("client_reference_id") or 0))
        if acc and cs.get("customer") and acc.stripe_customer_id != cs["customer"]:
            acc.stripe_customer_id = cs["customer"]
            db.add(acc)
            db.commit()
        if cs.get("subscription"):
            upsert_subscription(db, fetch_subscription(cs["subscription"]))
        return
    await _checkout_completed(db, cs, fetch_subscription)


def _claim(db: Session, event: dict[str, Any]) -> bool:
    db.add(StripeEvent(id=event["id"], type=event["type"]))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _release(db: Session, event_id: str) -> None:
    db.rollback()
    claimed = db.get(StripeEvent, event_id)
    if claimed:
        db.delete(claimed)
        db.commit()


async def handle_event(
    db: Session,
    event: dict[str, Any],
    fetch_subscription: Callable[[str], dict] = _fetch_subscription,
    fetch_payment_intent: Callable[[str], dict] = _fetch_payment_intent,
) -> bool:
    """Apply one webhook event. Returns False if it was already processed (or is being processed)."""
    if not _claim(db, event):
        return False
    try:
        await _apply(db, event, fetch_subscription, fetch_payment_intent)
    except Exception:
        _release(db, event["id"])  # let Stripe's retry apply it
        raise
    return True


async def _apply(
    db: Session, event: dict[str, Any], fetch_subscription: Callable[[str], dict], fetch_payment_intent: Callable[[str], dict]
) -> None:
    kind = event["type"]
    obj = event["data"]["object"]
    watch = kind.startswith("checkout.session.") and (obj.get("metadata") or {}).get("kind") == "watch"
    if kind == "checkout.session.completed":
        await _checkout_session(db, obj, fetch_subscription, fetch_payment_intent)
    elif watch and kind == "checkout.session.async_payment_succeeded":
        await _watch_order_paid(db, obj, fetch_payment_intent)
    elif watch and kind == "checkout.session.async_payment_failed":
        await _watch_order_failed(db, obj)
    elif watch and kind == "checkout.session.expired":
        _watch_session_expired(db, obj)
    elif kind == "checkout.session.async_payment_succeeded":
        _topup_paid(db, obj)
    elif kind in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        _topup_failed(db, obj)
    elif kind == "invoice.paid":
        _invoice_paid(db, obj)
    elif kind == "customer.subscription.trial_will_end":
        # Stripe sends this 3 days before the trial ends. UK subscription rules require a reminder
        # before the first paid period; the subscription terms promise it.
        row = upsert_subscription(db, obj)
        care_activation.complete_from_subscription(db, obj)
        acc = db.get(Account, row.account_id) if row.account_id else None
        if acc and acc.status == "active" and not row.cancel_at_period_end:
            await email.send(email.trial_ending(acc.email, row.trial_end))
    elif kind.startswith("customer.subscription."):
        upsert_subscription(db, obj)
        care_activation.complete_from_subscription(db, obj)
    elif kind == "invoice.payment_failed":
        acc = db.exec(select(Account).where(Account.stripe_customer_id == obj.get("customer"))).first()
        if acc:
            await email.send(email.payment_failed(acc.email))
    elif kind == "charge.refunded":
        _refund(db, obj)


# --- entitlement ------------------------------------------------------------------------------------


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def is_entitled(sub: Subscription, now: datetime | None = None) -> bool:
    if sub.source == "complimentary":
        end = _aware(sub.current_period_end)
        return sub.status == "active" and (end is None or (now or datetime.now(timezone.utc)) < end)
    return sub.status in ENTITLED_STATUSES


def active_subscription(db: Session, account_id: int) -> Subscription | None:
    """The subscription that counts: an entitled one (latest period end first), else the newest."""
    subs = db.exec(select(Subscription).where(Subscription.account_id == account_id).order_by(col(Subscription.id).desc())).all()
    live = [s for s in subs if is_entitled(s)]
    if live:
        far = datetime.max.replace(tzinfo=timezone.utc)
        return max(live, key=lambda s: (_aware(s.current_period_end) or far, s.id or 0))
    return subs[0] if subs else None


# --- complimentary / test access (operator) -----------------------------------------------------------


def grant_complimentary(
    db: Session,
    account: Account,
    days: int,
    actor: str,
    note: str = "Complimentary pilot - no payment",
    allowance_pence: int | None = None,
    extend: bool = False,
) -> tuple[Subscription, bool]:
    """Give an account ola Care without payment. Idempotent: an unexpired grant is returned as is
    (created=False) unless extend=True. Never creates Stripe objects. Refuses when the account already
    pays for Care (a Stripe subscription is entitled)."""
    if not 1 <= days <= 366:
        raise BillingError(422, "days must be between 1 and 366")
    lock_account(db, account.id)
    now = utcnow()
    current = active_subscription(db, account.id)
    if current is not None and is_entitled(current, now) and current.source == "stripe":
        raise BillingError(409, f"account already has a Stripe subscription ({current.status})")
    if current is not None and current.source == "complimentary" and is_entitled(current, now):
        if not extend:
            return current, False
        current.current_period_end = _aware(current.current_period_end) + timedelta(days=days)  # type: ignore[operator]
        current.updated_at = now
        db.add(current)
        db.commit()
        db.refresh(current)
        accounts.audit(db, actor, "subscription.complimentary.extend", account.id, detail=f"+{days} days, until {current.current_period_end:%Y-%m-%d}")
        db.refresh(current)
        return current, True
    sub = Subscription(
        source="complimentary",
        account_id=account.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=days),
        allowance_pence=allowance_pence,
        granted_by=actor[:80],
        note=note[:200],
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    accounts.audit(
        db,
        actor,
        "subscription.complimentary",
        account.id,
        detail=f"{days} days until {sub.current_period_end:%Y-%m-%d}; allowance "
        f"{'plan default' if allowance_pence is None else str(allowance_pence) + 'p'}; {note}",
    )
    db.refresh(sub)  # the audit commit expired it
    return sub, True


def revoke_complimentary(db: Session, account: Account, actor: str) -> bool:
    lock_account(db, account.id)
    now = utcnow()
    rows = db.exec(
        select(Subscription).where(
            Subscription.account_id == account.id, Subscription.source == "complimentary", Subscription.status == "active"
        )
    ).all()
    for sub in rows:
        sub.status, sub.current_period_end, sub.updated_at = "canceled", now, now
        db.add(sub)
    db.commit()
    if rows:
        accounts.audit(db, actor, "subscription.complimentary.revoke", account.id)
    return bool(rows)
