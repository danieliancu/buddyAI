"""Shop: public checkout, Stripe webhook, customer subscription, operator orders."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app import accounts, billing, email, entitlements
from app.api.me import current_account
from app.config import get_settings
from app.db.models import Account, Order, utcnow
from app.db.session import get_session
from app.ratelimit import SIGNUP_PER_IP, client_ip
from app.security import require_admin

router = APIRouter(tags=["shop"])


def _http(exc: billing.BillingError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


# --- public -------------------------------------------------------------------------------------


@router.get("/api/shop/status")
def shop_status() -> dict:
    s = get_settings()
    currencies = [c for c in ("gbp", "eur") if getattr(s, f"stripe_price_watch_{c}") and getattr(s, f"stripe_price_care_{c}")]
    return {"open": s.billing_enabled and bool(currencies), "currencies": currencies, "trial_days": s.care_trial_days}


class CheckoutBody(BaseModel):
    currency: Literal["gbp", "eur"] | None = None
    country: str | None = Field(None, max_length=2)
    email: str | None = None


@router.post("/api/shop/checkout")
def checkout(body: CheckoutBody, request: Request) -> dict:
    SIGNUP_PER_IP.hit(f"checkout|{client_ip(request)}")
    currency = body.currency or billing.currency_for(body.country)
    try:
        return {"url": billing.create_checkout(currency, body.email)}
    except billing.BillingError as exc:
        raise _http(exc) from exc


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
        "allowance_used_pct": min(100, round(a.fraction * 100)),
        "orders": [
            {"id": o.id, "status": o.status, "created_at": o.created_at, "tracking_number": o.tracking_number, "carrier": o.carrier}
            for o in db.exec(select(Order).where(Order.account_id == acc.id).order_by(col(Order.id).desc())).all()
        ],
    }


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


class AllowanceBody(BaseModel):
    allowance_override: float | None = Field(None, ge=0)


@router.patch("/api/accounts/{account_id}/allowance", dependencies=[Depends(require_admin)])
def set_allowance(account_id: int, body: AllowanceBody, request: Request, db: Session = Depends(get_session)) -> dict:
    acc = db.get(Account, account_id)
    if not acc or acc.status == "deleted":
        raise HTTPException(404, "account not found")
    acc.allowance_override = body.allowance_override
    db.add(acc)
    db.commit()
    accounts.audit(db, request.session["admin"], "account.allowance", acc.id, detail=str(body.allowance_override))
    a = entitlements.allowance(db, acc)
    return {"allowance_override": acc.allowance_override, "used": a.used, "limit": a.limit, "currency": a.currency}
