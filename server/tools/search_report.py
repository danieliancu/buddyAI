"""Web search before / after a date, from the recorded turns (read-only; no provider calls).

Run where the database is, e.g. in the production server container (Coolify: server -> Terminal):

    python tools/search_report.py --since "2026-10-31 14:00"              # every account
    python tools/search_report.py --since "2026-10-31 14:00" --account 7   # one account

--since is UTC: the moment the new search went live. "Before" is the --days-before days up to it.

Per period: search turns (hosted OpenAI search, our own ddgs_brave search, answers from the search cache),
answered / not found, cost per search turn (whole turn and the search part), the time to the first spoken
audio, and turns without a search as a control (their cost should not move). Then the last search turns
after --since: question, answer, which search, cost, latency.

The search part of a hosted search is only its per-call fee (its tokens are recorded together with the
conversation model's); for our own search it is every "search" usage row (Brave, the answering model).
Costs are the recorded usage costs (pricing rules), not provider invoices. Mock usage is excluded.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import col, select  # noqa: E402

from app.db.models import SearchProviderUsage, Turn, UsageRecord  # noqa: E402
from app.db.session import session_scope  # noqa: E402

OWN = {"ddgs", "brave", "metno", "nominatim", "trafilatura"}
NOT_FOUND = "nothing reliable found"


def _utc(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def load(since: datetime, until: datetime, account: int | None) -> list[dict]:
    with session_scope() as db:
        q = select(Turn).where(Turn.created_at >= since, Turn.created_at < until,
                               col(Turn.status) == "completed")
        if account is not None:
            q = q.where(Turn.account_id == account)
        turns = db.exec(q.order_by(Turn.created_at)).all()
        ids = [t.id for t in turns]
        rows: dict[int, list[UsageRecord]] = defaultdict(list)
        for i in range(0, len(ids), 500):
            for r in db.exec(select(UsageRecord).where(col(UsageRecord.turn_id).in_(ids[i:i + 500]),
                                                       UsageRecord.mock == False)).all():  # noqa: E712
                rows[r.turn_id].append(r)
    out = []
    for t in turns:
        recs = rows.get(t.id, [])
        searched = "web_search" in (t.tools or "") or any(r.kind == "search" or r.unit == "web_search_call" for r in recs)
        hosted = sum(r.quantity for r in recs if r.unit == "web_search_call")
        own = any(r.kind == "search" and r.provider in OWN for r in recs) or any(
            r.kind == "search" and r.unit == "search_run" and r.quantity for r in recs)
        cached = any(r.unit == "cache_hit" and r.quantity for r in recs)
        out.append({
            "at": t.created_at, "account": t.account_id, "searched": searched,
            "how": "hosted" if hosted else "own" if own else "cache" if cached else ("?" if searched else ""),
            "cost": sum(r.cost_usd or 0 for r in recs),
            "search_cost": sum(r.cost_usd or 0 for r in recs if r.kind == "search" or r.unit == "web_search_call"),
            "unpriced": sum(1 for r in recs if r.cost_usd is None),
            "ttfa": t.ttfa_server_ms, "note": t.search_note or "", "user": t.user_text or "",
            "search_ms": next((r.quantity for r in recs if r.unit == "duration_ms"), None),
            "brave": sum(r.quantity for r in recs if r.provider == "brave" and r.unit == "search_request"),
        })
    return out


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


def _fmt_s(ms: float | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f} s"


def _fmt_c(usd: float | None) -> str:
    return "-" if usd is None else f"{usd * 100:.2f} ¢"


def summary(turns: list[dict]) -> dict:
    s = [t for t in turns if t["searched"]]
    plain = [t for t in turns if not t["searched"]]
    answered = [t for t in s if t["note"] and NOT_FOUND not in t["note"]]
    ttfa = [t["ttfa"] for t in s if t["ttfa"] is not None]
    return {
        "turns": len(turns), "search turns": len(s),
        "  hosted (OpenAI)": sum(t["how"] == "hosted" for t in s),
        "  own (ddgs_brave)": sum(t["how"] == "own" for t in s),
        "  from the cache": sum(t["how"] == "cache" for t in s),
        "answered": f"{len(answered)} / {len(s)}" if s else "-",
        "cost / search turn (mean)": _fmt_c(statistics.mean(t["cost"] for t in s) if s else None),
        "search part / search turn (mean)": _fmt_c(statistics.mean(t["search_cost"] for t in s) if s else None),
        "first audio, search turns p50": _fmt_s(_pct(ttfa, 0.5)),
        "first audio, search turns p95": _fmt_s(_pct(ttfa, 0.95)),
        "own search time p50": _fmt_s(_pct([t["search_ms"] for t in s if t["search_ms"] is not None], 0.5)),
        "Brave requests": int(sum(t["brave"] for t in s)),
        "control: cost / turn without search": _fmt_c(statistics.mean(t["cost"] for t in plain) if plain else None),
        "control: first audio p50, no search": _fmt_s(_pct([t["ttfa"] for t in plain if t["ttfa"] is not None], 0.5)),
        "usage rows without a price": sum(t["unpriced"] for t in turns),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True, help="UTC, when the new search went live, e.g. '2026-10-31 14:00'")
    p.add_argument("--days-before", type=int, default=30)
    p.add_argument("--account", type=int, default=None)
    p.add_argument("--list", type=int, default=40, help="search turns after --since to list")
    args = p.parse_args()
    since = _utc(args.since)
    now = datetime.now(timezone.utc)
    before = load(since - timedelta(days=args.days_before), since, args.account)
    after = load(since, now, args.account)
    a, b = summary(before), summary(after)
    who = f"account {args.account}" if args.account is not None else "all accounts"
    print(f"Web search, {who}. Before: {args.days_before} days up to {since:%Y-%m-%d %H:%M} UTC. After: since then.\n")
    print(f"{'':40} {'before':>14} {'after':>14}")
    for k in a:
        print(f"{k:40} {str(a[k]):>14} {str(b[k]):>14}")
    try:
        with session_scope() as db:
            brave = db.exec(select(SearchProviderUsage).where(SearchProviderUsage.month == f"{now:%Y-%m}",
                                                              SearchProviderUsage.provider == "brave")).first()
        print(f"\nBrave requests this month (all accounts, budget counter): {brave.requests if brave else 0}")
    except Exception as exc:  # noqa: BLE001 - a database without the table (not migrated)
        print(f"\nBrave requests this month: unknown ({type(exc).__name__})")
    rows = [t for t in after if t["searched"]][-args.list:]
    if rows:
        print(f"\nSearch turns after {since:%Y-%m-%d %H:%M} UTC (latest {len(rows)}):")
        for t in rows:
            print(f"- {t['at']:%m-%d %H:%M} [{t['how'] or '?':6}] {_fmt_c(t['cost']):>8} turn, "
                  f"{_fmt_c(t['search_cost']):>8} search, first audio {_fmt_s(t['ttfa'])}")
            print(f"    asked: {t['user'][:140]}")
            print(f"    found: {t['note'][:220]}")


if __name__ == "__main__":
    main()
