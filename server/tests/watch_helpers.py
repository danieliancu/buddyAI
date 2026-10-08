"""Shared fakes for the watch-only checkout and the ola Care activation (no real Stripe calls)."""

from __future__ import annotations

import secrets
import threading
import time
from types import SimpleNamespace
from typing import Any

import stripe

from app import care_activation, care_terms
from app.config import get_settings
from app.db.models import BillingConsent
from app.db.session import session_scope
from app.email import ConsoleEmailSender

WEBHOOK_SECRET = "whsec_test_secret"
CARE_PENCE = 790


def enable_billing(monkeypatch) -> None:
    s = get_settings()
    for name, value in {
        "stripe_secret_key": "sk_test_dummy",
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "stripe_price_watch_gbp": "price_watch_gbp",
        "stripe_price_care_gbp": "price_care_gbp",
        "stripe_price_watch_eur": "price_watch_eur",
        "stripe_price_care_eur": "price_care_eur",
        "stripe_shipping_rates_gbp": "shr_uk",
        "site_url": "https://www.example.com",
        "app_url": "https://app.example.com",
        "care_trial_days": 30,
    }.items():
        monkeypatch.setattr(s, name, value)
    care_terms.clear_cache()
    monkeypatch.setattr(
        care_terms,
        "_fetch_price",
        lambda price_id: {"unit_amount": CARE_PENCE if price_id.endswith("gbp") else 890,
                          "currency": "gbp" if price_id.endswith("gbp") else "eur",
                          "recurring": {"interval": "month"}},
    )


class FakeCheckout:
    """Replaces stripe.checkout.Session.create; remembers the parameters of each session."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[dict[str, Any]] = []
        outer = self

        def create(**kwargs):
            sid = f"cs_test_{secrets.token_hex(8)}"
            outer.calls.append(kwargs | {"_id": sid})
            return {"id": sid, "url": f"https://checkout.stripe.com/c/pay/{sid}"}

        monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(create))

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


def accept_terms_body(client, currency: str = "gbp", **extra) -> dict[str, Any]:
    status = client.get("/api/shop/status").json()
    t = status["care_terms"][currency]
    return {"currency": currency, "care_terms_accepted": True, "care_terms_version": t["version"],
            "care_terms_sha256": t["sha256"]} | extra


def start_checkout(client, fake: FakeCheckout, currency: str = "gbp") -> str:
    r = client.post("/api/shop/checkout", json=accept_terms_body(client, currency))
    assert r.status_code == 200, r.text
    return fake.last["_id"]


def session_obj(
    cs_id: str,
    email_addr: str,
    customer: str,
    payment_status: str = "paid",
    consent: str | None = "accepted",
    pi: str | None = None,
) -> dict[str, Any]:
    with session_scope() as db:
        from sqlmodel import select

        row = db.exec(select(BillingConsent).where(BillingConsent.stripe_checkout_session_id == cs_id)).first()
        meta = {"source": "buddyai-site", "kind": "watch"}
        if row is not None:
            meta |= {"care_terms_version": row.terms_version, "care_terms_sha256": row.terms_sha256}
    return {
        "id": cs_id,
        "object": "checkout.session",
        "mode": "payment",
        "customer": customer,
        "payment_intent": pi or f"pi_{secrets.token_hex(6)}",
        "payment_status": payment_status,
        "currency": "gbp",
        "amount_total": 8498,
        "created": int(time.time()),
        "consent": {"terms_of_service": consent} if consent else None,
        "total_details": {"amount_tax": 1333, "amount_shipping": 499},
        "customer_details": {"email": email_addr, "name": "Jane Buyer", "address": {"country": "GB"}},
        "collected_information": {
            "shipping_details": {
                "name": "Jane Buyer",
                "address": {"line1": "1 High St", "city": "London", "postal_code": "N1 1AA", "country": "GB"},
            }
        },
        "metadata": meta,
    }


def event(kind: str, obj: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"evt_{secrets.token_hex(8)}", "type": kind, "data": {"object": obj}}


def pi_fetch(pm: str | None = "pm_card_visa"):
    return lambda pi_id: {"id": pi_id, "payment_method": pm}


class FakeGateway:
    """Replaces care_activation.gateway: a tiny in-memory Stripe for subscriptions."""

    def __init__(self) -> None:
        self.created: list[tuple[dict[str, Any], str]] = []
        self.subs: dict[str, list[dict[str, Any]]] = {}
        self.default_pm: dict[str, str] = {}
        self.fail_find: Exception | None = None
        self.fail_create: Exception | None = None
        self.create_delay = 0.0
        self._lock = threading.Lock()

    def find_subscription(self, customer: str, activation_id: int):
        if self.fail_find:
            raise self.fail_find
        for sub in self.subs.get(customer, []):
            if sub["metadata"].get("activation_id") == str(activation_id):
                return sub
        return None

    def default_payment_method(self, customer: str):
        return self.default_pm.get(customer)

    def set_default_payment_method(self, customer: str, pm: str) -> None:
        self.default_pm[customer] = pm

    def create_subscription(self, params: dict[str, Any], idempotency_key: str):
        if self.create_delay:
            time.sleep(self.create_delay)
        if self.fail_create:
            raise self.fail_create
        with self._lock:
            for prev_params, prev_key in self.created:  # Stripe idempotency: same key, same answer
                if prev_key == idempotency_key:
                    return self._sub_for(prev_params)
            self.created.append((params, idempotency_key))
            sub = self._new_sub(params)
            self.subs.setdefault(params["customer"], []).append(sub)
            return sub

    def _sub_for(self, params):
        return next(s for s in self.subs[params["customer"]] if s["metadata"] == params["metadata"])

    @staticmethod
    def _new_sub(params: dict[str, Any]) -> dict[str, Any]:
        now = int(time.time())
        end = now + params["trial_period_days"] * 86400
        return {
            "id": f"sub_{secrets.token_hex(6)}",
            "customer": params["customer"],
            "status": "trialing",
            "trial_end": end,
            "cancel_at_period_end": False,
            "metadata": dict(params["metadata"]),
            "items": {"data": [{"current_period_start": now, "current_period_end": end}]},
        }


def install_gateway(monkeypatch) -> FakeGateway:
    gw = FakeGateway()
    monkeypatch.setattr(care_activation, "gateway", gw)
    return gw


def last_link(to: str, marker: str = "token=") -> str:
    mail = [m for m in ConsoleEmailSender.sent if m.to == to and marker in m.text][-1]
    return mail.text.split(marker)[1].split()[0]


class FakeWatchConn:
    """Stands in for a watch's WebSocket connection while it waits to be paired."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.sent: list[tuple[str, dict]] = []

    async def send_json(self, kind: str, **fields) -> None:
        self.sent.append((kind, fields))


def watch_waiting(client, code: str | None = None) -> tuple[str, str]:
    """A watch showing a pairing code (as after its hello). Returns (device_id, code)."""
    code = code or f"{secrets.randbelow(900000) + 100000}"
    device_id = f"buddy-{secrets.token_hex(6)}"
    client.app.state.hub.add_pending(code, FakeWatchConn(device_id), "hw-test", "0.1.0")
    return device_id, code


def stripe_error(kind: str = "api", **kw) -> Exception:
    return {
        "api": lambda: stripe.APIConnectionError("network down"),
        "card": lambda: stripe.CardError("card declined", param="payment_method", code="card_declined"),
        "invalid_pm": lambda: stripe.InvalidRequestError("No such PaymentMethod", param="default_payment_method"),
    }[kind]()


__all__ = [
    "SimpleNamespace",
    "WEBHOOK_SECRET",
]
