"""ProviderPricingConfig: cost estimation from editable price rules (DB, seeded from config).

Prices are data, never code: changing a tariff = editing a rule in the web UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlmodel import Session

from app.db.models import UsageRecord
from app.db.repositories import PricingRepo
from app.money import usd_to_micro_gbp
from app.providers.base import UsageItem


@dataclass(frozen=True)
class PriceKey:
    provider: str
    model: str
    unit: str


def is_mock(provider: str) -> bool:
    return provider.startswith("mock")


class ProviderPricingConfig:
    def __init__(self, prices: dict[PriceKey, float], usd_gbp_rate: Decimal = Decimal("0.75")) -> None:
        self.prices = prices
        self.usd_gbp_rate = usd_gbp_rate

    @classmethod
    def load(cls, session: Session) -> "ProviderPricingConfig":
        from app.plan import get_plan  # the operator's USD->GBP rate

        return cls(
            {PriceKey(r.provider, r.model, r.unit): r.price_usd for r in PricingRepo(session).list()},
            get_plan(session).usd_gbp_rate,
        )

    def cost(self, provider: str, model: str, unit: str, quantity: float) -> float | None:
        price = self.prices.get(PriceKey(provider, model, unit))
        return None if price is None else round(price * quantity, 8)

    def records(
        self,
        items: list[UsageItem],
        device_id: str,
        turn_db_id: int | None,
        account_id: int | None = None,
        turn_status: str | None = None,
        billable: bool = True,
    ) -> list[UsageRecord]:
        """Usage rows with the cost in USD and in GBP (frozen at today's rate). Items without a
        pricing rule keep cost None (flagged as unpriced, never counted as free)."""
        out = []
        for i in items:
            if not i.quantity:
                continue
            usd = self.cost(i.provider, i.model, i.unit, i.quantity)
            out.append(
                UsageRecord(
                    turn_id=turn_db_id,
                    turn_uid=f"t{turn_db_id}" if turn_db_id is not None else None,
                    turn_status=turn_status,
                    device_id=device_id,
                    account_id=account_id,
                    kind=i.kind,
                    provider=i.provider,
                    model=i.model,
                    unit=i.unit,
                    quantity=i.quantity,
                    cost_usd=usd,
                    cost_micro_gbp=usd_to_micro_gbp(usd, self.usd_gbp_rate),
                    fx_rate=float(self.usd_gbp_rate) if usd is not None else None,
                    billable=billable,
                    mock=is_mock(i.provider),
                )
            )
        return out
