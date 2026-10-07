"""Migration 0025 (watch-only checkout, ola Care at pairing) is additive: existing accounts, orders and
subscriptions keep their values, legacy orders stay legacy (checkout_flow NULL), nobody gets a pending
activation, and the downgrade removes only what 0025 added.
Runs on PostgreSQL (BUDDYAI_PG_TEST_URL) and on a temporary SQLite file."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.pg.test_migration import _cfg, _urls


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_0025_keeps_existing_billing_records(url):
    engine = create_engine(url)
    if url.startswith("postgresql"):
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    cfg = _cfg(url)
    command.upgrade(cfg, "0024")
    now = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("INSERT INTO accounts (email, name, status, internal, session_version, created_at, stripe_customer_id, "
                       "memory_explicit, memory_use, memory_learn) VALUES ('paying@example.com', '', 'active', FALSE, 1, :now, "
                       "'cus_old', TRUE, TRUE, TRUE)"), {"now": now})
        acc = c.execute(text("SELECT id FROM accounts WHERE email='paying@example.com'")).scalar()
        c.execute(text("INSERT INTO orders (stripe_session_id, account_id, email, currency, amount_total, amount_tax, "
                       "amount_shipping, status, shipping_name, shipping_address, carrier, tracking_number, created_at) "
                       "VALUES ('cs_old', :a, 'paying@example.com', 'gbp', 20998, 3500, 499, 'shipped', 'P', '{}', '', '', :now)"),
                  {"a": acc, "now": now})
        c.execute(text("INSERT INTO subscriptions (source, stripe_subscription_id, stripe_customer_id, account_id, status, "
                       "cancel_at_period_end, granted_by, note, created_at, updated_at) VALUES ('stripe', 'sub_old', 'cus_old', "
                       ":a, 'active', FALSE, NULL, '', :now, :now)"), {"a": acc, "now": now})
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        row = c.execute(text("SELECT status, checkout_flow, amount_total FROM orders WHERE stripe_session_id='cs_old'")).one()
        assert tuple(row) == ("shipped", None, 20998)
        assert c.execute(text("SELECT setup_platform, stripe_customer_id FROM accounts WHERE id=:a"), {"a": acc}).one() == (None, "cus_old")
        assert c.execute(text("SELECT status FROM subscriptions WHERE stripe_subscription_id='sub_old'")).scalar() == "active"
        assert c.execute(text("SELECT count(*) FROM care_activations")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM billing_consents")).scalar() == 0
    command.downgrade(cfg, "0024")
    names = set(inspect(engine).get_table_names())
    assert "care_activations" not in names and "billing_consents" not in names
    assert "checkout_flow" not in {col["name"] for col in inspect(engine).get_columns("orders")}
    with engine.begin() as c:
        assert c.execute(text("SELECT status FROM orders WHERE stripe_session_id='cs_old'")).scalar() == "shipped"
    command.upgrade(cfg, "head")
    engine.dispose()
