"""Real-search benchmark of the web search backends (not part of the test suite: it uses the network, the
OpenAI key and, for Brave, the Brave key from server/.env).

    python tools/search_bench.py                 # ddgs_brave on every query
    python tools/search_bench.py --legacy        # also legacy_openai (OpenAI hosted search: billed per call)
    python tools/search_bench.py --brave-only 5  # also the first 5 queries with DDGS switched off (Brave)

It runs on a throwaway database in a temporary data dir (never data/buddyai.db), bypasses the answer cache,
and writes every answer, latency, request count and token count to out/search_bench_<date>.json. Whether an
answer is correct is not judged here: read them (the "check" column says what a correct answer contains).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))
os.environ["BUDDYAI_DATA_DIR"] = tempfile.mkdtemp(prefix="buddyai-bench-")
os.environ["BUDDYAI_DATABASE_URL"] = ""
os.environ["BUDDYAI_MOCK_PROVIDERS"] = "false"

# (category, query as the conversation model would write it, location, what a correct answer contains)
QUERIES = [
    ("weather", "weather Chelmsford today", "Chelmsford", "temperature + conditions for today"),
    ("weather", "weather London tomorrow", "London", "tomorrow's forecast"),
    ("weather", "weather Bucharest today", "Bucharest", "temperature + conditions"),
    ("weather", "rain forecast Manchester this weekend", "Manchester", "weekend rain outlook"),
    ("weather", "sunset time London today", "London", "a clock time around 16:40-17:00 (late Oct)"),
    ("news", "UK latest news headlines today", "", "a current headline"),
    ("news", "Bank of England interest rate latest decision", "", "current Bank Rate %"),
    ("news", "Romania latest news today", "", "a current Romanian headline"),
    ("news", "UK inflation rate latest", "", "latest CPI % and month"),
    ("news", "latest SpaceX launch", "", "recent launch + date"),
    ("sport", "Premier League table leader", "", "current leader + points"),
    ("sport", "Arsenal latest match result", "", "score + opponent + date"),
    ("sport", "Romania national football team next match", "", "upcoming fixture on/after today"),
    ("sport", "Romania national football team latest match result", "", "score + opponent + date"),
    ("sport", "Formula 1 next race", "", "next Grand Prix + date"),
    ("transport", "Elizabeth line status now", "London", "service status"),
    ("transport", "Greater Anglia trains Chelmsford to London disruption today", "Chelmsford", "status / disruption"),
    ("business", "Tesco Extra Chelmsford opening hours", "Chelmsford", "opening hours"),
    ("business", "Chelmsford Central Library opening hours", "Chelmsford", "opening hours"),
    ("business", "IKEA Lakeside opening hours", "", "opening hours"),
    ("business", "Boots Chelmsford High Street phone number", "Chelmsford", "a phone number"),
    ("business", "British Museum opening hours", "London", "10:00-17:00 style hours"),
    ("other", "iPhone 17 price UK", "", "a £ price"),
    ("other", "price of petrol UK average today", "", "pence per litre"),
    ("other", "Bitcoin price today GBP", "", "a £ price"),
    ("other", "gold price per ounce today", "", "a $ or £ price"),
    ("other", "population of Chelmsford", "", "~180k (city) figure"),
    ("other", "who is the current Prime Minister of the UK", "", "current PM"),
    ("other", "height of Mount Everest", "", "8,849 m"),
    ("other", "Romania capital population", "", "~1.7-1.8 million"),
]


async def bench(args) -> dict:
    from app.config import get_settings, load_providers_config
    from app.db.session import run_migrations
    from app.providers.router import ProviderRouter
    from app.search import search_instructions
    from app.websearch import orchestrator as orch_mod
    from app.websearch.service import answer_fn, get_orchestrator

    run_migrations()
    s = get_settings()
    router = ProviderRouter(s, load_providers_config())
    llm, model = router.llm_default()
    params = router.llm_params()
    tz = "Europe/London"
    rows = []
    queries = QUERIES[: args.limit] if args.limit else QUERIES

    async def ddgs_brave(category, query, location, force_brave=False):
        o = get_orchestrator(s)
        if force_brave:
            o.breaker.opened_at, o.breaker._trial = time.monotonic(), True  # DDGS off for this run
        else:
            o.breaker.success()
        t = o.turn(answer_fn(llm, model, params), tz=tz, llm_provider=llm.name, llm_model=model)
        start = time.monotonic()
        answer, tin, tout, searches, complete, usage = await t.search(query, location, "en", category=category)
        units = {f"{u.provider}:{u.unit}": u.quantity for u in usage if u.quantity}
        return {"answer": answer, "ms": int((time.monotonic() - start) * 1000), "complete": complete,
                "requests": searches, "units": units}

    async def legacy(category, query, location):
        hosted = {"search_context_size": "low"}
        start = time.monotonic()
        answer, tin, tout, searches, complete = await llm.web_search(
            query, location, "en", model=model, tool=hosted, params=params,
            instructions=search_instructions(tz), timezone_name=tz)
        return {"answer": answer, "ms": int((time.monotonic() - start) * 1000), "complete": complete,
                "requests": searches, "units": {"input_token": tin, "output_token": tout, "web_search_call": searches}}

    for i, (category, query, location, check) in enumerate(queries):
        row = {"category": category, "query": query, "check": check}
        row["ddgs_brave"] = await ddgs_brave(category, query, location)
        if args.brave_only and i < args.brave_only:
            row["brave_only"] = await ddgs_brave(category, query, location, force_brave=True)
        if args.legacy:
            try:
                row["legacy_openai"] = await legacy(category, query, location)
            except Exception as exc:  # noqa: BLE001
                row["legacy_openai"] = {"error": type(exc).__name__}
        rows.append(row)
        print(f"[{i + 1}/{len(queries)}] {category:9} {row['ddgs_brave']['ms']:>6} ms  {query}\n"
              f"    ddgs_brave: {row['ddgs_brave']['answer'][:150]}")
        for k in ("brave_only", "legacy_openai"):
            if k in row:
                print(f"    {k}: {str(row[k].get('answer', row[k]))[:150]}  ({row[k].get('ms')} ms)")
    return {"model": model, "rows": rows, "stats": dict(orch_mod.STATS),
            "prices": {f"{p['provider']}:{p['model']}:{p['unit']}": p["price_usd"]
                       for p in load_providers_config()["pricing_defaults"]}}


def summarise(result: dict) -> dict:
    prices = result["prices"]
    model = result["model"]
    out = {}
    for backend in ("ddgs_brave", "brave_only", "legacy_openai"):
        runs = [r[backend] for r in result["rows"] if backend in r and "ms" in r[backend]]
        if not runs:
            continue
        ms = sorted(r["ms"] for r in runs)
        found = [r for r in runs if r["answer"] and "NOT_FOUND" not in r["answer"]]
        cost = 0.0
        for r in runs:
            for k, q in r["units"].items():
                if backend == "legacy_openai":
                    cost += q * prices.get(f"openai:{model}:{k}", 0)
                else:
                    provider, unit = k.split(":", 1)
                    m = {"ddgs": "web", "brave": "web", "trafilatura": "extract", "buddyai": "websearch",
                         "metno": "weather", "nominatim": "geocode"}.get(provider, model)
                    cost += q * prices.get(f"{provider}:{m}:{unit}", 0)
        out[backend] = {
            "queries": len(runs), "answered": len(found), "p50_ms": int(statistics.median(ms)),
            "p95_ms": ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))], "max_ms": ms[-1],
            "cost_usd_total": round(cost, 5), "cost_usd_per_query": round(cost / len(runs), 6),
            "requests": sum(r["requests"] for r in runs),
        }
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # answers in Romanian etc. on a cp1252 console
    p = argparse.ArgumentParser()
    p.add_argument("--legacy", action="store_true", help="also run legacy_openai (billed hosted search)")
    p.add_argument("--brave-only", type=int, default=0, help="also run the first N queries through Brave only")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    result = asyncio.run(bench(args))
    result["summary"] = summarise(result)
    result["run_at"] = datetime.now(timezone.utc).isoformat()
    out = SERVER / "out" / f"search_bench_{datetime.now():%Y%m%d_%H%M}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    print(f"written: {out}")


if __name__ == "__main__":
    main()
