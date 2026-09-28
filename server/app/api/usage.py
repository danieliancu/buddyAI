"""Usage (units + estimated cost), diagnostics (TTFA p50/p95, per-stage latency), pricing rules."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.models import utcnow
from app.db.repositories import PricingRepo, TurnRepo, UsageRepo
from app.db.session import get_session
from app.pipeline.metrics import percentile
from app.pricing.currency import convert, get_currency, set_currency
from app.security import require_admin

router = APIRouter(prefix="/api", tags=["usage"], dependencies=[Depends(require_admin)])

TTFA_TARGET_P50_MS = 1500
TTFA_TARGET_P95_MS = 2500


@router.get("/usage")
def usage(days: int = Query(30, ge=1, le=365), device_id: str | None = None, db: Session = Depends(get_session)) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = UsageRepo(db).since(since, device_id)
    groups: dict[tuple, dict] = defaultdict(lambda: {"quantity": 0.0, "cost_usd": 0.0, "unpriced": 0})
    by_day: dict[str, float] = defaultdict(float)
    total = 0.0
    for r in rows:
        g = groups[(r.kind, r.provider, r.model, r.unit)]
        g["quantity"] += r.quantity
        if r.cost_usd is None:
            g["unpriced"] += 1
        else:
            g["cost_usd"] += r.cost_usd
            total += r.cost_usd
            by_day[r.created_at.date().isoformat()] += r.cost_usd
    cur = get_currency()
    rate = cur["usd_rate"]
    return {
        "days": days,
        "currency": cur["currency"],
        "symbol": cur["symbol"],
        "usd_rate": rate,
        "total_cost": convert(total, rate),
        "total_cost_usd": round(total, 6),
        "items": [
            {
                "kind": k,
                "provider": p,
                "model": m,
                "unit": u,
                **v,
                "cost_usd": round(v["cost_usd"], 6),
                "cost": convert(v["cost_usd"], rate),
            }
            for (k, p, m, u), v in sorted(groups.items())
        ],
        "by_day": [
            {"day": d, "cost_usd": round(c, 6), "cost": convert(c, rate)} for d, c in sorted(by_day.items())
        ],
    }


@router.get("/diagnostics")
def diagnostics(days: int = Query(7, ge=1, le=90), device_id: str | None = None, db: Session = Depends(get_session)) -> dict:
    turns = TurnRepo(db).recent(utcnow() - timedelta(days=days), device_id)

    def stats(values: list[int | None]) -> dict:
        vals = [v for v in values if v is not None]
        return {"count": len(vals), "p50": percentile(vals, 50), "p95": percentile(vals, 95)}

    def ttfa(t) -> int | None:
        return t.ttfa_device_ms if t.ttfa_device_ms is not None else t.ttfa_server_ms

    completed = [t for t in turns if t.status == "completed"]
    by_lang: dict[str, list] = defaultdict(list)
    by_provider: dict[str, list] = defaultdict(list)
    for t in completed:
        by_lang[t.language].append(ttfa(t))
        by_provider[f"{t.stt_provider} / {t.llm_model} / {t.tts_provider}"].append(ttfa(t))
    status_counts: dict[str, int] = defaultdict(int)
    for t in turns:
        status_counts[t.status] += 1
    return {
        "days": days,
        "targets": {"ttfa_p50_ms": TTFA_TARGET_P50_MS, "ttfa_p95_ms": TTFA_TARGET_P95_MS},
        "ttfa": stats([ttfa(t) for t in completed]),
        "stages": {
            "stt_ms": stats([t.stt_ms for t in completed]),
            "llm_first_token_ms": stats([t.llm_first_token_ms for t in completed]),
            "tts_first_audio_ms": stats([t.tts_first_audio_ms for t in completed]),
            "ttfa_server_ms": stats([t.ttfa_server_ms for t in completed]),
            "ttfa_device_ms": stats([t.ttfa_device_ms for t in completed]),
        },
        "by_language": {k: stats(v) for k, v in by_lang.items()},
        "by_provider": {k: stats(v) for k, v in by_provider.items()},
        "status_counts": status_counts,
        "recent": [
            {
                "id": t.id,
                "device_id": t.device_id,
                "language": t.language,
                "status": t.status,
                "created_at": t.created_at,
                "stt_ms": t.stt_ms,
                "llm_first_token_ms": t.llm_first_token_ms,
                "tts_first_audio_ms": t.tts_first_audio_ms,
                "ttfa_ms": ttfa(t),
                "error": t.error,
            }
            for t in turns[-50:][::-1]
        ],
    }


@router.get("/pricing")
def pricing(db: Session = Depends(get_session)) -> list[dict]:
    """Vendor prices in USD (as billed) plus the equivalent in the display currency."""
    rate = get_currency()["usd_rate"]
    return [{**r.model_dump(), "price_display": r.price_usd * rate} for r in PricingRepo(db).list()]


class CurrencyBody(BaseModel):
    currency: str = Field("GBP", min_length=3, max_length=3)
    usd_rate: float = Field(gt=0, description="1 USD = usd_rate units of currency")


@router.get("/pricing/currency")
def currency() -> dict:
    return get_currency()


@router.put("/pricing/currency")
def update_currency(body: CurrencyBody) -> dict:
    return set_currency(body.currency, body.usd_rate)


class PriceBody(BaseModel):
    price_usd: float = Field(ge=0)
    note: str | None = None


@router.put("/pricing/{rule_id}")
def update_price(rule_id: int, body: PriceBody, db: Session = Depends(get_session)) -> dict:
    rule = PricingRepo(db).update(rule_id, body.price_usd, body.note)
    if not rule:
        raise HTTPException(404, "rule not found")
    return {**rule.model_dump(), "price_display": rule.price_usd * get_currency()["usd_rate"]}
