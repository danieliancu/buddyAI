"""Discount codes for the watch (operator panel → Discount codes).

Stripe is the source of truth: each code is a Stripe **coupon** (a fixed amount off, once, limited to the watch
product, so it can never touch ola Care) plus a **promotion code** (the text the buyer types at Checkout, e.g.
"VIP"). Checkout already allows promotion codes (billing.create_checkout). The operator gives the price the buyer
should pay; the amount off is the watch price minus that. Nothing is stored locally: the list, the uses and the
on/off state are read from Stripe each time.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import stripe

from app.billing import BillingError, stripe_error_summary
from app.config import get_settings

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"^[A-Z0-9]{3,20}$")
MIN_FINAL_PENCE = 100  # never give the watch away by mistake


def _key() -> str:
    s = get_settings()
    if not s.billing_enabled or not s.stripe_price_watch_gbp:
        raise BillingError(503, "the shop is not configured yet")
    return s.stripe_secret_key


def _stripe_failed(exc: stripe.StripeError) -> BillingError:
    log.warning("discount codes: %s", stripe_error_summary(exc))
    return BillingError(502, "payment provider unavailable, please try again")


def _watch_price(key: str) -> tuple[int, str]:
    """(unit amount in pence, product id) of the watch price that Checkout sells."""
    price = stripe.Price.retrieve(get_settings().stripe_price_watch_gbp, api_key=key)
    product = price["product"]
    return int(price["unit_amount"] or 0), product if isinstance(product, str) else product["id"]


def _ts(value: Any) -> str | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat() if value else None


def _out(pc: dict[str, Any], watch_pence: int) -> dict[str, Any]:
    # API 2025-09-30 and later: the coupon sits under `promotion`; older versions had `coupon` at the top.
    coupon = (pc.get("promotion") or {}).get("coupon") or pc.get("coupon") or {}
    off = int(coupon.get("amount_off") or 0) if isinstance(coupon, dict) else 0
    pct = coupon.get("percent_off") if isinstance(coupon, dict) else None
    final = watch_pence - off if off else (round(watch_pence * (100 - pct) / 100) if pct else watch_pence)
    return {
        "id": pc["id"],
        "code": pc["code"],
        "active": bool(pc.get("active")),
        "times_redeemed": int(pc.get("times_redeemed") or 0),
        "max_redemptions": pc.get("max_redemptions"),
        "expires_at": _ts(pc.get("expires_at")),
        "created_at": _ts(pc.get("created")),
        "amount_off_pence": off,
        "percent_off": pct,
        "currency": (coupon.get("currency") or "gbp") if isinstance(coupon, dict) else "gbp",
        "final_price_pence": max(final, 0),
        "watch_price_pence": watch_pence,
    }


def _plain(obj: Any) -> dict[str, Any]:
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def list_codes() -> dict[str, Any]:
    key = _key()
    try:
        watch_pence, _product = _watch_price(key)
        codes = stripe.PromotionCode.list(api_key=key, limit=100, expand=["data.promotion.coupon"])
        rows = [_out(_plain(pc), watch_pence) for pc in codes.auto_paging_iter()]
    except stripe.StripeError as exc:
        raise _stripe_failed(exc) from exc
    rows.sort(key=lambda r: (not r["active"], r["code"]))
    return {"watch_price_pence": watch_pence, "codes": rows}


def create_code(code: str, final_price_pence: int, max_redemptions: int | None = None,
                expires_at: datetime | None = None) -> dict[str, Any]:
    key = _key()
    code = code.strip().upper()
    if not CODE_RE.match(code):
        raise BillingError(422, "the code must be 3 to 20 letters or digits")
    if max_redemptions is not None and max_redemptions < 1:
        raise BillingError(422, "the number of uses must be at least 1")
    if expires_at is not None and expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise BillingError(422, "the expiry date must be in the future")
    try:
        watch_pence, product = _watch_price(key)
        if not MIN_FINAL_PENCE <= final_price_pence < watch_pence:
            raise BillingError(422, f"the price with the code must be from £1.00 to under the watch price (£{watch_pence / 100:.2f})")
        if stripe.PromotionCode.list(api_key=key, code=code, active=True, limit=1)["data"]:
            raise BillingError(409, f"the code {code} already exists")
        coupon = stripe.Coupon.create(
            api_key=key,
            amount_off=watch_pence - final_price_pence,
            currency="gbp",
            duration="once",
            applies_to={"products": [product]},
            name=f"{code} - watch £{final_price_pence / 100:.2f}"[:40],
            metadata={"source": "ola-admin", "code": code},
        )
        params: dict[str, Any] = {"promotion": {"type": "coupon", "coupon": coupon["id"]}, "code": code,
                                  "metadata": {"source": "ola-admin"}}
        if max_redemptions is not None:
            params["max_redemptions"] = max_redemptions
        if expires_at is not None:
            params["expires_at"] = int(expires_at.timestamp())
        pc = stripe.PromotionCode.create(api_key=key, **params)
    except stripe.StripeError as exc:
        raise _stripe_failed(exc) from exc
    row = _plain(pc)
    row["promotion"] = {"type": "coupon", "coupon": _plain(coupon)}
    return _out(row, watch_pence)


def set_active(promotion_code_id: str, active: bool) -> dict[str, Any]:
    key = _key()
    if not promotion_code_id.startswith("promo_"):
        raise BillingError(404, "discount code not found")
    try:
        watch_pence, _product = _watch_price(key)
        pc = stripe.PromotionCode.modify(promotion_code_id, api_key=key, active=active, expand=["promotion.coupon"])
    except stripe.InvalidRequestError as exc:
        raise BillingError(404, "discount code not found") from exc
    except stripe.StripeError as exc:
        raise _stripe_failed(exc) from exc
    return _out(_plain(pc), watch_pence)
