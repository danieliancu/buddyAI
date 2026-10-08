"""The ola Care recurring-billing terms the buyer agrees to before paying for a watch.

One versioned text, rendered from the configured price and trial length, is shown in three places: next
to the required checkbox on the site, in Stripe Checkout (terms-of-service acceptance text) and in the
PaymentIntent/session metadata (version + hash). The same text is stored as proof (BillingConsent).

The monthly amount is read from the Stripe Care price of the chosen currency (the price that will be
charged), never typed into the code. Bump CARE_TERMS_VERSION when the wording changes.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

import stripe

from app.config import get_settings

log = logging.getLogger(__name__)

CARE_TERMS_VERSION = "care-2026-11"

_SYMBOL = {"gbp": "£", "eur": "€"}
_cache: dict[str, tuple[float, "CarePrice"]] = {}
_CACHE_S = 600.0


@dataclass(frozen=True)
class CarePrice:
    amount_minor: int
    currency: str
    interval: str  # month | year


@dataclass(frozen=True)
class CareTerms:
    version: str
    text: str
    sha256: str
    price: CarePrice
    trial_days: int


def money(amount_minor: int, currency: str) -> str:
    return f"{_SYMBOL.get(currency.lower(), currency.upper() + ' ')}{amount_minor / 100:,.2f}"


def _fetch_price(price_id: str) -> dict:
    s = get_settings()
    return stripe.Price.retrieve(price_id, api_key=s.stripe_secret_key).to_dict()


def care_price(currency: str, fetch=None) -> CarePrice:
    """The recurring Care price of `currency`, from Stripe (cached for 10 minutes)."""
    currency = currency.lower()
    hit = _cache.get(currency)
    if hit and time.monotonic() - hit[0] < _CACHE_S:
        return hit[1]
    price_id = getattr(get_settings(), f"stripe_price_care_{currency}", "")
    if not price_id:
        raise LookupError(f"no ola Care price configured for {currency.upper()}")
    data = (fetch or _fetch_price)(price_id)
    recurring = data.get("recurring") or {}
    if data.get("unit_amount") is None or not recurring.get("interval"):
        raise LookupError(f"ola Care price {price_id} is not a fixed recurring price")
    if (data.get("currency") or currency).lower() != currency:
        raise LookupError(f"ola Care price {price_id} is not in {currency.upper()}")
    out = CarePrice(int(data["unit_amount"]), currency, str(recurring["interval"]))
    _cache[currency] = (time.monotonic(), out)
    return out


def clear_cache() -> None:
    _cache.clear()


def render(price: CarePrice, trial_days: int) -> str:
    each = money(price.amount_minor, price.currency)
    return (
        f"I agree that Ola Technologies London Ltd may save my card and use it for ola Care: a free "
        f"{trial_days}-day trial that starts when I pair my watch to my ola account (not today), then "
        f"{each} per {price.interval}, charged automatically every {price.interval} until I cancel. "
        "I can cancel anytime in my ola account; if I cancel before the trial ends I pay nothing for ola Care. "
        "Nothing is charged for ola Care today."
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current(currency: str, fetch=None) -> CareTerms:
    s = get_settings()
    price = care_price(currency, fetch)
    text = render(price, s.care_trial_days)
    return CareTerms(CARE_TERMS_VERSION, text, sha256(text), price, s.care_trial_days)
