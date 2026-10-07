"""Shop: public checkout, Stripe webhook, customer plan / top-ups / usage notices, operator orders,
plan settings and complimentary grants."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from datetime import datetime, timezone

from app import accounts, billing, care_activation, care_terms, email, entitlements, plan as plan_mod, usage_notices
from app import allowance as allowance_mod
from app.account_lock import lock_account
from app.api.me import current_account
from app.config import get_settings
from app.db.models import Account, Order, TopUp, utcnow
from app.db.session import get_session
from app.ratelimit import CHECKOUT_STATUS_PER_IP, SIGNUP_PER_IP, client_ip
from app.security import require_admin

router = APIRouter(tags=["shop"])


def _http(exc: billing.BillingError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


# --- public -------------------------------------------------------------------------------------


@router.get("/api/shop/status")
def shop_status() -> dict:
    """Shop availability and, per currency, the ola Care terms the buyer must accept before paying
    (the exact text, its version and hash; the monthly amount comes from the Stripe Care price)."""
    s = get_settings()
    currencies = [c for c in ("gbp", "eur") if getattr(s, f"stripe_price_watch_{c}") and getattr(s, f"stripe_price_care_{c}")]
    terms: dict[str, dict] = {}
    if s.billing_enabled:
        for c in currencies:
            try:
                t = care_terms.current(c)
            except Exception:  # noqa: BLE001 - a currency without readable terms cannot be sold
                continue
            terms[c] = {
                "version": t.version,
                "sha256": t.sha256,
                "text": t.text,
                "amount_minor": t.price.amount_minor,
                "interval": t.price.interval,
            }
        currencies = [c for c in currencies if c in terms]
    return {
        "open": s.billing_enabled and bool(currencies),
        "currencies": currencies,
        "trial_days": s.care_trial_days,
        "trial_starts": "on_pairing",
        "care_terms": terms,
    }


class CheckoutBody(BaseModel):
    currency: Literal["gbp", "eur"] | None = None
    country: str | None = Field(None, max_length=2)
    email: str | None = None
    # The buyer ticked "I agree ..." next to these exact ola Care terms (GET /api/shop/status).
    care_terms_accepted: bool = False
    care_terms_version: str = Field("", max_length=32)
    care_terms_sha256: str = Field("", max_length=64)


@router.post("/api/shop/checkout")
def checkout(body: CheckoutBody, request: Request, db: Session = Depends(get_session)) -> dict:
    SIGNUP_PER_IP.hit(f"checkout|{client_ip(request)}")
    if not get_settings().billing_enabled:
        raise HTTPException(503, "the shop is not open yet")
    if not (body.care_terms_accepted and body.care_terms_version and body.care_terms_sha256):
        raise HTTPException(400, "please accept the ola Care terms first")
    currency = body.currency or billing.currency_for(body.country)
    consent = billing.SiteConsent(
        body.care_terms_version, body.care_terms_sha256, client_ip(request), request.headers.get("user-agent", "")
    )
    try:
        return {"url": billing.create_checkout(db, currency, consent, body.email)}
    except billing.BillingError as exc:
        raise _http(exc) from exc


@router.get("/api/shop/checkout-status")
def checkout_status(session_id: str, request: Request, db: Session = Depends(get_session)) -> dict:
    """The thank-you page asks whether Stripe confirmed the payment (webhook), never trusting the redirect."""
    CHECKOUT_STATUS_PER_IP.hit(client_ip(request))
    if not (session_id.startswith("cs_") and 10 <= len(session_id) <= 255):
        raise HTTPException(422, "invalid session id")
    return billing.checkout_status(db, session_id)


@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_session)) -> dict:
    payload = await request.body()
    try:
        event = billing.verify_webhook(payload, request.headers.get("stripe-signature", ""))
    except billing.BillingError as exc:
        raise _http(exc) from exc
    processed = await billing.handle_event(db, event)
    return {"received": True, "processed": processed}


# --- customer -------------------------------------------------------------------------------------


@router.get("/api/me/subscription")
def my_subscription(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    s = get_settings()
    sub = billing.active_subscription(db, acc.id) if s.billing_enabled else None
    a = entitlements.allowance(db, acc)
    return {
        "billing_enabled": s.billing_enabled,
        "subscription": None
        if sub is None
        else {
            "status": sub.status,
            "trial_end": sub.trial_end,
            "current_period_end": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
        },
        # Customers see how much of the fair-use allowance is used, not our internal costs.
        "allowance_used_pct": a.used_pct,
        "orders": [
            {"id": o.id, "status": o.status, "created_at": o.created_at, "tracking_number": o.tracking_number, "carrier": o.carrier}
            for o in db.exec(select(Order).where(Order.account_id == acc.id).order_by(col(Order.id).desc())).all()
        ],
    }


def _status(sub, now: datetime) -> dict:
    """Plan state for the customer UI (no internal costs)."""
    if sub is None:
        return {"kind": "none"}
    out = {
        "trial_end": sub.trial_end,
        "period_end": sub.current_period_end,
        "cancel_at_period_end": sub.cancel_at_period_end,
    }
    if sub.source == "complimentary":
        return out | {"kind": "complimentary" if billing.is_entitled(sub, now) else "expired", "note": sub.note}
    kind = {"trialing": "trial", "active": "active", "past_due": "past_due"}.get(sub.status, "canceled")
    return out | {"kind": kind}


@router.get("/api/me/plan")
def my_plan(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    """ola Care for the customer: status, % of the allowance used, reset date, activity, prices,
    top-ups. Never internal costs, tokens or providers."""
    s = get_settings()
    p = plan_mod.get_plan(db)
    now = datetime.now(timezone.utc)
    sub = billing.active_subscription(db, acc.id)
    a = entitlements.allowance(db, acc)
    status = {"kind": "internal"} if acc.internal else _status(sub, now)
    entitled = sub is not None and billing.is_entitled(sub, now)
    paying = entitled and sub is not None and sub.source == "stripe"
    topups = db.exec(
        select(TopUp).where(TopUp.account_id == acc.id, col(TopUp.status).in_(("paid", "refunded"))).order_by(col(TopUp.id).desc()).limit(20)
    ).all()
    base = p.care_allowance_pence or 1
    return {
        "billing_enabled": s.billing_enabled,
        "enforced": p.enforced and not acc.internal,
        "status": status,
        "usage": {
            "used_pct": a.used_pct,
            "period_start": a.period.start,
            "reset_at": a.period.end,
            "activity_count": allowance_mod.activity_count(db, acc.id, a.period),
            "extra_pct": round(a.topup_micro * 100 / (a.included_micro or 1)) if a.topup_micro else 0,
        },
        "thresholds": list(p.thresholds),
        "prices": {
            "currency": "GBP",
            "care_price_pence": p.care_price_pence,
            "topup_price_pence": p.topup_price_pence,
            # A top-up expressed as a share of the plan's monthly usage (not as internal pounds).
            "topup_adds_pct": round(p.topup_allowance_pence * 100 / base),
        },
        # A complimentary pilot can be turned into a paid plan at any time.
        "can_subscribe": s.billing_enabled and bool(s.stripe_price_care_gbp) and not paying and not acc.internal,
        "topup_available": s.billing_enabled and entitled and not acc.internal,
        "can_manage_billing": s.billing_enabled and bool(acc.stripe_customer_id),
        # ola Care that starts when the watch is paired (watch-only orders); None for legacy accounts.
        "care_activation": care_activation.public_state(db, acc.id),
        "topups": [
            {
                "id": t.id,
                "status": t.status,
                "amount_pence": t.amount_pence,
                "paid_at": t.paid_at,
                "period_end": t.period_end,
                "current": t.status == "paid" and a.period.start <= (t.period_start if t.period_start.tzinfo else t.period_start.replace(tzinfo=timezone.utc)) < a.period.end,
            }
            for t in topups
        ],
    }


@router.post("/api/me/subscribe")
def subscribe(request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    SIGNUP_PER_IP.hit(f"subscribe|{client_ip(request)}")
    try:
        return {"url": billing.create_subscription_checkout(db, acc)}
    except billing.BillingError as exc:
        raise _http(exc) from exc


@router.post("/api/me/care/activate")
async def retry_care_activation(request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    """Retry starting ola Care after a failure (needs a paired watch). Never creates a second subscription."""
    SIGNUP_PER_IP.hit(f"care|{client_ip(request)}")
    state = care_activation.public_state(db, acc.id)
    if state is None:
        raise HTTPException(404, "nothing to activate")
    if not care_activation.has_paired_watch(db, acc.id):
        raise HTTPException(409, "pair your watch first")
    care_activation.audit_retry(db, f"account:{acc.id}", acc.id)
    await care_activation.activate(acc.id)
    db.expire_all()
    return {"care_activation": care_activation.public_state(db, acc.id)}


@router.post("/api/accounts/{account_id}/care/activate", dependencies=[Depends(require_admin)])
async def operator_retry_care_activation(account_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    if care_activation.public_state(db, account_id) is None:
        raise HTTPException(404, "nothing to activate")
    care_activation.audit_retry(db, request.session["admin"], account_id)
    await care_activation.activate(account_id)
    db.expire_all()
    return {"care_activation": care_activation.public_state(db, account_id)}


@router.post("/api/me/topups/checkout")
def topup_checkout(request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    """Starts a one-time payment. Nothing is granted until Stripe confirms the payment (webhook)."""
    SIGNUP_PER_IP.hit(f"topup|{client_ip(request)}")
    try:
        topup, url = billing.create_topup_checkout(db, acc)
    except billing.BillingError as exc:
        raise _http(exc) from exc
    accounts.audit(db, f"account:{acc.id}", "topup.checkout", acc.id, detail=f"top-up {topup.id}")
    return {"url": url, "topup_id": topup.id}


@router.get("/api/me/usage-notice")
def usage_notice(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    """The highest usage threshold of this period not dismissed yet (or none)."""
    n = usage_notices.pending_web(db, acc)
    return {"notice": None if n is None else {"threshold": n.threshold, "level": usage_notices.level(n.threshold)}}


class DismissBody(BaseModel):
    threshold: int = Field(ge=1, le=100)


@router.post("/api/me/usage-notice/dismiss")
def dismiss_usage_notice(body: DismissBody, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    usage_notices.dismiss_web(db, acc, body.threshold)
    return {"ok": True}


@router.post("/api/me/billing-portal")
def billing_portal(acc: Account = Depends(current_account)) -> dict:
    try:
        return {"url": billing.portal_url(acc)}
    except billing.BillingError as exc:
        raise _http(exc) from exc


# --- operator ---------------------------------------------------------------------------------------


def _order_out(o: Order) -> dict:
    return o.model_dump()


@router.get("/api/orders", dependencies=[Depends(require_admin)])
def list_orders(status: str | None = None, db: Session = Depends(get_session)) -> list[dict]:
    q = select(Order)
    if status:
        q = q.where(Order.status == status)
    return [_order_out(o) for o in db.exec(q.order_by(col(Order.id).desc()).limit(500)).all()]


class OrderUpdate(BaseModel):
    status: Literal["paid", "shipped", "delivered", "cancelled"]
    carrier: str = Field("", max_length=60)
    tracking_number: str = Field("", max_length=120)


@router.patch("/api/orders/{order_id}", dependencies=[Depends(require_admin)])
async def update_order(order_id: int, body: OrderUpdate, request: Request, db: Session = Depends(get_session)) -> dict:
    """Refunds are made in the Stripe dashboard; the webhook then marks the order refunded."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "order not found")
    newly_shipped = body.status == "shipped" and order.status != "shipped"
    order.status = body.status
    order.carrier, order.tracking_number = body.carrier, body.tracking_number
    if body.status == "shipped" and order.shipped_at is None:
        order.shipped_at = utcnow()
    if body.status == "delivered" and order.delivered_at is None:
        order.delivered_at = utcnow()
    db.add(order)
    db.commit()
    db.refresh(order)
    accounts.audit(db, request.session["admin"], f"order.{body.status}", order.account_id, detail=f"order {order.id}")
    if newly_shipped:
        await email.send(email.order_shipped(order.email, order.id, order.carrier, order.tracking_number))
    return _order_out(order)


# --- operator: plan settings + complimentary access -------------------------------------------------------


@router.get("/api/billing/settings", dependencies=[Depends(require_admin)])
def get_billing_settings(db: Session = Depends(get_session)) -> dict:
    s = get_settings()
    return plan_mod.public(plan_mod.row(db)) | {
        "stripe_configured": s.billing_enabled,
        "stripe_mode": "live" if s.stripe_secret_key.startswith("sk_live") else ("test" if s.stripe_secret_key else "off"),
        "care_price_id_set": bool(s.stripe_price_care_gbp),
    }


class BillingSettingsBody(BaseModel):
    enforce: bool | None = None
    care_price_pence: int | None = Field(None, ge=0, le=100_000)
    care_allowance_pence: int | None = Field(None, ge=0, le=100_000)
    topup_price_pence: int | None = Field(None, ge=0, le=100_000)
    topup_allowance_pence: int | None = Field(None, ge=0, le=100_000)
    thresholds: str | None = Field(None, max_length=40)
    usd_gbp_rate: str | None = Field(None, max_length=16)
    reserve_pence: int | None = Field(None, ge=0, le=1000)


@router.put("/api/billing/settings", dependencies=[Depends(require_admin)])
def put_billing_settings(body: BillingSettingsBody, request: Request, db: Session = Depends(get_session)) -> dict:
    changes = body.model_dump(exclude_none=True)
    before = plan_mod.public(plan_mod.row(db))
    try:
        r = plan_mod.update(db, changes, request.session["admin"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    diff = {k: f"{before[k]} -> {v}" for k, v in plan_mod.public(r).items() if k in changes and before.get(k) != v}
    if diff:
        accounts.audit(db, request.session["admin"], "billing.settings", detail=str(diff))
    return get_billing_settings(db)


class ComplimentaryBody(BaseModel):
    days: int = Field(30, ge=1, le=366)
    allowance_pence: int | None = Field(None, ge=0, le=100_000)
    note: str = Field("Complimentary pilot - no payment", max_length=200)
    extend: bool = False


@router.post("/api/accounts/{account_id}/complimentary", dependencies=[Depends(require_admin)])
def grant_complimentary(account_id: int, body: ComplimentaryBody, request: Request, db: Session = Depends(get_session)) -> dict:
    acc = db.get(Account, account_id)
    if not acc or acc.status == "deleted":
        raise HTTPException(404, "account not found")
    try:
        sub, created = billing.grant_complimentary(
            db, acc, body.days, request.session["admin"], body.note, body.allowance_pence, body.extend
        )
    except billing.BillingError as exc:
        raise _http(exc) from exc
    return {"created": created, "subscription": sub.model_dump()}


@router.delete("/api/accounts/{account_id}/complimentary", dependencies=[Depends(require_admin)])
def revoke_complimentary(account_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    acc = db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "account not found")
    return {"revoked": billing.revoke_complimentary(db, acc, request.session["admin"])}


class AllowanceBody(BaseModel):
    allowance_override: float | None = Field(None, ge=0)


@router.patch("/api/accounts/{account_id}/allowance", dependencies=[Depends(require_admin)])
def set_allowance(account_id: int, body: AllowanceBody, request: Request, db: Session = Depends(get_session)) -> dict:
    acc = db.get(Account, account_id)
    if not acc or acc.status == "deleted":
        raise HTTPException(404, "account not found")
    lock_account(db, acc.id)  # the limit changes: admissions of this account wait for it
    acc.allowance_override = body.allowance_override
    db.add(acc)
    db.commit()
    accounts.audit(db, request.session["admin"], "account.allowance", acc.id, detail=str(body.allowance_override))
    a = entitlements.allowance(db, acc)
    return {"allowance_override": acc.allowance_override, "used": a.used, "limit": a.limit, "currency": a.currency}
