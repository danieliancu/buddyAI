"""Migration 0027 (AI interactions) is additive: existing operations are not turned into interactions, existing
top-ups and settings keep their values, the defaults are 1,000 interactions / 250 per top-up / £2.00 £2.50 £5.00,
and the downgrade removes only what 0027 added.
Runs on PostgreSQL (BUDDYAI_PG_TEST_URL) and on a temporary SQLite file."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.pg.test_migration import _cfg, _urls


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_0027_keeps_history_and_downgrades(url):
    engine = create_engine(url)
    if url.startswith("postgresql"):
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    cfg = _cfg(url)
    command.upgrade(cfg, "0026")
    now = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("INSERT INTO accounts (email, name, status, internal, session_version, created_at, memory_explicit, "
                       "memory_use, memory_learn, allowance_override) VALUES ('h@example.com', '', 'active', FALSE, 1, :n, "
                       "TRUE, TRUE, TRUE, 3.5)"), {"n": now})
        acc = c.execute(text("SELECT id FROM accounts WHERE email='h@example.com'")).scalar()
        c.execute(text("INSERT INTO usage_operations (op_uid, account_id, device_id, request_key, request_kind, "
                       "request_fingerprint, kind, period_key, source, enforced, state, cost_certainty, reserved_micro, "
                       "recorded_cost_micro, unpriced_count, billable, result_status, accepted_at, created_at, updated_at) "
                       "VALUES ('u1', :a, 'd', 'r:x', 'client', 'f', 'chat', 'cal:2026-10', 'calendar', TRUE, 'settled', "
                       "'exact', 0, 120000, 0, TRUE, 'completed', :n, :n, :n)"), {"a": acc, "n": now})
        c.execute(text("INSERT INTO topups (account_id, amount_pence, allowance_pence, status, period_start, period_end, "
                       "created_at, period_key) VALUES (:a, 199, 65, 'paid', :s, :e, :n, 'cal:2026-10')"),
                  {"a": acc, "s": now - timedelta(days=1), "e": now + timedelta(days=20), "n": now})
        c.execute(text("UPDATE billing_settings SET care_allowance_pence = 300 WHERE id = 1"))
        c.execute(text("INSERT INTO usage_notices (account_id, period_start, threshold, created_at) "
                       "VALUES (:a, :s, 100, :n)"), {"a": acc, "s": now - timedelta(days=1), "n": now})
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        assert c.execute(text("SELECT interaction FROM usage_operations WHERE op_uid='u1'")).scalar() is None
        assert c.execute(text("SELECT recorded_cost_micro FROM usage_operations WHERE op_uid='u1'")).scalar() == 120000
        assert tuple(c.execute(text("SELECT interactions, allowance_pence FROM topups")).one()) == (250, 65)
        assert c.execute(text("SELECT count(*) FROM usage_notices")).scalar() == 0  # money-era notices removed
        s = c.execute(text("SELECT interaction_limit, topup_interactions, cost_warn_pence, cost_target_pence, "
                           "cost_critical_pence, care_allowance_pence FROM billing_settings WHERE id = 1")).one()
        assert tuple(s) == (1000, 250, 200, 250, 500, 300)
        a = c.execute(text("SELECT interaction_limit_override, allowance_override FROM accounts WHERE id = :a"), {"a": acc}).one()
        assert a[0] is None and a[1] == 3.5  # no invented override; the old value kept for history
        assert "cost_alerts" in inspect(c).get_table_names()
    command.downgrade(cfg, "0026")
    with engine.begin() as c:
        insp = inspect(c)
        assert "cost_alerts" not in insp.get_table_names()
        assert "interaction" not in {col["name"] for col in insp.get_columns("usage_operations")}
        assert c.execute(text("SELECT count(*) FROM usage_operations")).scalar() == 1
        assert c.execute(text("SELECT allowance_pence FROM topups")).scalar() == 65
    command.upgrade(cfg, "head")
