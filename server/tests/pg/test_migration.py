"""Migration 0020 keeps existing data: upgrade from 0019 with usage, top-ups and notices in place, then check
the history is still counted the same way; the downgrade refuses once operations exist.
Runs on PostgreSQL (BUDDYAI_PG_TEST_URL) and on a temporary SQLite file."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import SERVER_DIR

PG_URL = os.environ.get("BUDDYAI_PG_TEST_URL", "")


def _cfg(url: str) -> Config:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["skip_logging"] = True
    return cfg


def _urls() -> list[str]:
    out = [f"sqlite:///{(Path(tempfile.mkdtemp()) / 'mig.db').as_posix()}"]
    if PG_URL:
        out.append(PG_URL)
    return out


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_upgrade_from_0019_keeps_history_and_downgrade_is_guarded(url):
    engine = create_engine(url)
    if url.startswith("postgresql"):
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    cfg = _cfg(url)
    command.upgrade(cfg, "0019")
    now = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("INSERT INTO accounts (email, name, status, internal, session_version, created_at) "
                       "VALUES ('old@example.com', '', 'active', FALSE, 1, :now)"), {"now": now})
        acc = c.execute(text("SELECT id FROM accounts WHERE email='old@example.com'")).scalar()
        for cost in (100_000, 250_000):
            c.execute(text("INSERT INTO usage_records (device_id, account_id, kind, provider, model, unit, quantity, "
                           "cost_usd, cost_micro_gbp, billable, mock, created_at) VALUES ('d', :a, 'llm', 'openai', 'm', "
                           "'input_token', 1, 0.1, :c, TRUE, FALSE, :now)"), {"a": acc, "c": cost, "now": now})
        c.execute(text("INSERT INTO topups (account_id, amount_pence, allowance_pence, status, period_start, period_end, "
                       "created_at) VALUES (:a, 199, 100, 'paid', :s, :e, :now)"),
                  {"a": acc, "s": now - timedelta(days=1), "e": now + timedelta(days=20), "now": now})
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM usage_records WHERE operation_id IS NULL AND period_key IS NULL")).scalar() == 2
        assert c.execute(text("SELECT sum(cost_micro_gbp) FROM usage_records")).scalar() == 350_000
        assert c.execute(text("SELECT count(*) FROM topups WHERE period_key IS NULL")).scalar() == 1
        assert c.execute(text("SELECT admission_paused FROM billing_settings")).scalar() in (False, 0)
        # history still counts in the current calendar period (legacy rows: by their time window)
    from app import allowance as allowance_mod
    from sqlmodel import Session

    from app.db.models import Account

    with Session(engine) as db:
        a = db.get(Account, acc)
        pid = allowance_mod.period_identity(None, now)
        assert allowance_mod.used_final(db, a.id, pid) == (350_000, 0)
        assert allowance_mod.topups_micro(db, a.id, now, pid.key) == 1_000_000
    with engine.begin() as c:
        c.execute(text("INSERT INTO usage_operations (op_uid, device_id, request_key, request_kind, request_fingerprint, "
                       "kind, source, state, accepted_at, created_at, updated_at) VALUES ('u1', 'd', 'r:x', 'client', 'f', "
                       "'chat', 'unowned', 'settled', :n, :n, :n)"), {"n": now})
    with pytest.raises(Exception, match="downgrade refused"):
        command.downgrade(cfg, "0019")
    engine.dispose()
