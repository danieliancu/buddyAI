"""Discount codes (operator panel): Stripe coupon + promotion code limited to the watch, and the code saved on the
order. No real Stripe calls."""

from __future__ import annotations

import secrets

import pytest
import stripe
from sqlmodel import select

from app import billing
from app.db.models import AuditLog, Order
from app.db.session import session_scope
from tests.test_operator_security import _no_rate_limits_between_tests, _signed_in  # noqa: F401 - fixture
from tests.test_shop import _session_event, billing_on  # noqa: F401 - fixture


class FakeStripe:
    """Coupons and promotion codes kept in memory, in the API 2025-09-30+ shape (coupon under `promotion`)."""

    def __init__(self) -> None:
        self.coupons: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}

    def price(self, price_id, api_key=None, **kw):
        assert price_id == "price_watch_gbp"
        return {"id": price_id, "unit_amount": 11900, "product": "prod_watch"}

    def coupon_create(self, api_key=None, **kw):
        c = {"id": f"co_{secrets.token_hex(3)}", **kw}
        self.coupons[c["id"]] = c
        return c

    def promo_create(self, api_key=None, promotion=None, code="", **kw):
        pc = {"id": f"promo_{secrets.token_hex(3)}", "code": code, "active": True, "times_redeemed": 0, "created": 1763200000,
              "promotion": {"type": "coupon", "coupon": promotion["coupon"]}, **kw}
        self.codes[pc["id"]] = pc
        return pc

    def _expanded(self, pc):
        return {**pc, "promotion": {"type": "coupon", "coupon": self.coupons[pc["promotion"]["coupon"]]}}

    def promo_list(self, api_key=None, code=None, active=None, **kw):
        rows = [self._expanded(pc) for pc in self.codes.values()
                if (code is None or pc["code"] == code) and (active is None or pc["active"] == active)]
        return FakeList(rows)

    def promo_modify(self, pid, api_key=None, active=None, **kw):
        if pid not in self.codes:
            raise stripe.InvalidRequestError("No such promotion code", "id")
        self.codes[pid]["active"] = active
        return self._expanded(self.codes[pid])

    def promo_retrieve(self, pid, api_key=None, **kw):
        return self.codes[pid]


class FakeList(dict):
    def __init__(self, rows):
        super().__init__(data=rows)

    def auto_paging_iter(self):
        return iter(self["data"])


@pytest.fixture
def fake_stripe(billing_on, monkeypatch):
    f = FakeStripe()
    monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(f.price))
    monkeypatch.setattr(stripe.Coupon, "create", staticmethod(f.coupon_create))
    monkeypatch.setattr(stripe.PromotionCode, "create", staticmethod(f.promo_create))
    monkeypatch.setattr(stripe.PromotionCode, "list", staticmethod(f.promo_list))
    monkeypatch.setattr(stripe.PromotionCode, "modify", staticmethod(f.promo_modify))
    monkeypatch.setattr(stripe.PromotionCode, "retrieve", staticmethod(f.promo_retrieve))
    return f


def test_vip_code_brings_the_watch_to_7999_and_only_the_watch(fake_stripe):
    c, _ = _signed_in("admin")
    r = c.post("/api/billing/discounts", json={"code": "vip", "final_price_pence": 7999})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["code"] == "VIP" and row["active"] and row["final_price_pence"] == 7999 and row["amount_off_pence"] == 3901
    coupon = next(iter(fake_stripe.coupons.values()))
    assert coupon["amount_off"] == 3901 and coupon["currency"] == "gbp" and coupon["duration"] == "once"
    assert coupon["applies_to"] == {"products": ["prod_watch"]}  # never ola Care
    pc = next(iter(fake_stripe.codes.values()))
    assert "max_redemptions" not in pc and "expires_at" not in pc  # no limit unless asked
    listed = c.get("/api/billing/discounts").json()
    assert listed["watch_price_pence"] == 11900 and [x["code"] for x in listed["codes"]] == ["VIP"]
    with session_scope() as db:
        assert db.exec(select(AuditLog).where(AuditLog.action == "discount.create")).first() is not None


def test_bad_codes_and_prices_are_refused(fake_stripe):
    c, _ = _signed_in("admin")
    assert c.post("/api/billing/discounts", json={"code": "VIP", "final_price_pence": 7999}).status_code == 200
    assert c.post("/api/billing/discounts", json={"code": "VIP", "final_price_pence": 5000}).status_code == 409
    assert c.post("/api/billing/discounts", json={"code": "TOO-MUCH", "final_price_pence": 5000}).status_code == 422
    assert c.post("/api/billing/discounts", json={"code": "FREE", "final_price_pence": 11900}).status_code == 422
    assert c.post("/api/billing/discounts", json={"code": "FREE", "final_price_pence": 50}).status_code == 422
    assert len(fake_stripe.coupons) == 1  # nothing created for the refused ones


def test_switch_off_and_on(fake_stripe):
    c, _ = _signed_in("admin")
    pid = c.post("/api/billing/discounts", json={"code": "VIP", "final_price_pence": 7999}).json()["id"]
    assert c.patch(f"/api/billing/discounts/{pid}", json={"active": False}).json()["active"] is False
    assert fake_stripe.codes[pid]["active"] is False
    assert c.patch(f"/api/billing/discounts/{pid}", json={"active": True}).json()["active"] is True
    assert c.patch("/api/billing/discounts/promo_missing", json={"active": False}).status_code == 404


def test_support_operators_can_look_but_not_change(fake_stripe):
    c, _ = _signed_in("support")
    assert c.get("/api/billing/discounts").status_code == 200
    assert c.post("/api/billing/discounts", json={"code": "VIP", "final_price_pence": 7999}).status_code == 403


async def test_order_keeps_the_code_and_the_discount(fake_stripe):
    pc = fake_stripe.promo_create(promotion={"coupon": "co_x"}, code="VIP")
    event = _session_event(f"vip-{secrets.token_hex(3)}@example.com", f"cus_{secrets.token_hex(4)}", f"sub_{secrets.token_hex(4)}")
    cs = event["data"]["object"]
    cs["total_details"]["amount_discount"] = 3901
    cs["discounts"] = [{"coupon": "co_x", "promotion_code": pc["id"]}]
    order = billing._new_order(cs, None)
    assert order.amount_discount == 3901 and order.discount_code == "VIP"
    cs.pop("discounts")
    cs["total_details"]["amount_discount"] = 0
    plain = billing._new_order(cs, None)
    assert plain.amount_discount == 0 and plain.discount_code == ""


def test_shop_closed_without_stripe():
    c, _ = _signed_in("admin")
    assert c.get("/api/billing/discounts").status_code == 503
