"""Display currency. Vendor prices and stored costs stay in USD (what OpenAI/Alibaba/Azure bill);
the web app shows amounts converted with an editable USD->display rate (default GBP)."""

from __future__ import annotations

import json

from app.config import get_settings

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€", "RON": "lei"}


def _file():
    return get_settings().data_dir / "currency.json"


def get_currency() -> dict:
    s = get_settings()
    data = {"currency": s.display_currency, "usd_rate": s.usd_to_display_rate}
    if _file().exists():
        data.update(json.loads(_file().read_text(encoding="utf-8")))
    data["symbol"] = SYMBOLS.get(data["currency"], data["currency"])
    return data


def set_currency(currency: str, usd_rate: float) -> dict:
    _file().write_text(json.dumps({"currency": currency.upper(), "usd_rate": usd_rate}), encoding="utf-8")
    return get_currency()


def convert(usd: float | None, rate: float) -> float | None:
    return None if usd is None else round(usd * rate, 6)
