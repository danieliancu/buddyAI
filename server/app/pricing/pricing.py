"""ProviderPricingConfig: cost estimation from editable price rules (DB, seeded from config).

Prices are data, never code: changing a tariff = editing a rule in the web UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from app.db.models import UsageRecord
from app.db.repositories import PricingRepo
from app.providers.base import UsageItem


@dataclass(frozen=True)
class PriceKey:
    provider: str
    model: str
    unit: str


class ProviderPricingConfig:
    def __init__(self, prices: dict[PriceKey, float]) -> None:
        self.prices = prices

    @classmethod
    def load(cls, session: Session) -> "ProviderPricingConfig":
        return cls({PriceKey(r.provider, r.model, r.unit): r.price_usd for r in PricingRepo(session).list()})

    def cost(self, provider: str, model: str, unit: str, quantity: float) -> float | None:
        price = self.prices.get(PriceKey(provider, model, unit))
        return None if price is None else round(price * quantity, 8)

    def records(self, items: list[UsageItem], device_id: str, turn_db_id: int | None) -> list[UsageRecord]:
        return [
            UsageRecord(
                turn_id=turn_db_id,
                device_id=device_id,
                kind=i.kind,
                provider=i.provider,
                model=i.model,
                unit=i.unit,
                quantity=i.quantity,
                cost_usd=self.cost(i.provider, i.model, i.unit, i.quantity),
            )
            for i in items
            if i.quantity
        ]
