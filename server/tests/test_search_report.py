"""tools/search_report.py: search turns are told apart (hosted OpenAI search, our own search, cache) and costed."""

from __future__ import annotations

import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.models import Conversation, Turn, UsageRecord
from app.db.repositories import DeviceRepo
from app.db.session import session_scope
from app.security import hash_device_token
from tests.test_items import _account

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import search_report  # noqa: E402

NOW = datetime.now(timezone.utc)


def _turn(dev: str, acc: int, conv: int, at: datetime, tools: str, note: str, usage: list[tuple], ttfa: int) -> None:
    with session_scope() as db:
        t = Turn(device_id=dev, account_id=acc, conversation_id=conv, session_id="s", turn_no=1, language="en",
                 status="completed", tools=tools, search_note=note, user_text="q", created_at=at, ttfa_server_ms=ttfa)
        db.add(t)
        db.commit()
        db.refresh(t)
        for kind, provider, unit, qty, cost in usage:
            db.add(UsageRecord(turn_id=t.id, device_id=dev, account_id=acc, kind=kind, provider=provider, model="m",
                               unit=unit, quantity=qty, cost_usd=cost, created_at=at))
        db.commit()


def test_report_tells_the_searches_apart_and_costs_them() -> None:
    acc = _account()
    dev = f"dev-rep-{secrets.token_hex(4)}"
    with session_scope() as db:
        DeviceRepo(db).pair(dev, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", acc)
        conv = Conversation(device_id=dev, started_at=NOW, last_activity_at=NOW)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        cid = conv.id
    since = NOW - timedelta(hours=1)
    old, new = since - timedelta(days=1), since + timedelta(minutes=5)
    _turn(dev, acc, cid, old, "web_search", "weather -> 12°C", [("llm", "openai", "input_token", 900, 0.004),
                                                                 ("llm", "openai", "web_search_call", 1, 0.01)], 4000)
    _turn(dev, acc, cid, old, "", "", [("llm", "openai", "input_token", 500, 0.002)], 1000)
    _turn(dev, acc, cid, new, "web_search", "weather -> 13°C", [("llm", "openai", "input_token", 900, 0.004),
                                                                 ("search", "brave", "search_request", 1, 0.005),
                                                                 ("search", "openai", "input_token", 800, 0.0002)], 2500)
    _turn(dev, acc, cid, new, "web_search", "train -> nothing reliable found",
          [("llm", "openai", "web_search_call", 1, 0.01)], 5000)
    _turn(dev, acc, cid, new, "web_search", "weather -> 13°C", [("search", "buddyai", "cache_hit", 1, 0)], 1500)

    before = search_report.summary(search_report.load(since - timedelta(days=2), since, acc))
    after = search_report.summary(search_report.load(since, NOW + timedelta(hours=1), acc))
    assert before["search turns"] == 1 and before["  hosted (OpenAI)"] == 1 and before["answered"] == "1 / 1"
    assert before["control: cost / turn without search"] == "0.20 ¢"
    assert after["  own (ddgs_brave)"] == 1 and after["  hosted (OpenAI)"] == 1 and after["  from the cache"] == 1
    assert after["answered"] == "2 / 3" and after["Brave requests"] == 1
    assert before["search part / search turn (mean)"] == "1.00 ¢"  # the hosted search fee
