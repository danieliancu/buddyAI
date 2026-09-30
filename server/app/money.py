"""Money arithmetic without floats.

Provider prices are USD (floats in the pricing rules, as the vendors publish them); every amount the
business reasons about is GBP in integers: pence for prices and allowances, micro-pounds (millionths
of £1) for AI costs, which are often fractions of a penny. Conversions go through Decimal and round
half-up once.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MICRO_PER_POUND = 1_000_000
MICRO_PER_PENNY = 10_000


def dec(value: float | int | str | Decimal) -> Decimal:
    """A Decimal from a float without binary noise (0.1 -> Decimal('0.1'))."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def usd_to_micro_gbp(usd: float | Decimal | None, usd_gbp_rate: Decimal) -> int | None:
    if usd is None:
        return None
    return int((dec(usd) * usd_gbp_rate * MICRO_PER_POUND).to_integral_value(ROUND_HALF_UP))


def pence_to_micro(pence: int) -> int:
    return int(pence) * MICRO_PER_PENNY


def pounds_to_micro(pounds: float | Decimal) -> int:
    return int((dec(pounds) * MICRO_PER_POUND).to_integral_value(ROUND_HALF_UP))


def micro_to_pounds(micro: int) -> Decimal:
    return Decimal(micro) / MICRO_PER_POUND


def micro_to_float(micro: int, places: int = 4) -> float:
    """For JSON responses to the operator UI (display only; sums are done in integers)."""
    return float(round(micro_to_pounds(micro), places))


def pence_str(pence: int) -> str:
    """799 -> '£7.99'."""
    return f"£{Decimal(pence) / 100:.2f}"
