"""Migration 0029 (operator security) is additive: an existing operator becomes an 'admin' with no two-factor
authentication, session version 0 and not disabled; the downgrade removes only the new columns.
Runs on PostgreSQL (BUDDYAI_PG_TEST_URL) and on a temporary SQLite file."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.pg.test_migration import _cfg, _urls

NEW = {"role", "session_version", "disabled", "mfa_enabled", "totp_secret_enc", "recovery_codes"}


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_0029_existing_operator_keeps_full_access_and_downgrades(url):
    engine = create_engine(url)
    if url.startswith("postgresql"):
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    cfg = _cfg(url)
    command.upgrade(cfg, "0028")
    with engine.begin() as c:
        c.execute(text("INSERT INTO admin_users (username, password_hash, created_at) VALUES ('boss', 'h', :n)"),
                  {"n": datetime.now(timezone.utc)})
    command.upgrade(cfg, "0029")
    with engine.begin() as c:
        row = c.execute(text("SELECT username, password_hash, role, session_version, disabled, mfa_enabled, "
                             "totp_secret_enc, recovery_codes FROM admin_users")).one()
        assert row[0] == "boss" and row[1] == "h"
        assert row[2] == "admin" and row[3] == 0
        assert row[4] in (False, 0) and row[5] in (False, 0)
        assert row[6] is None and row[7] is None
    command.downgrade(cfg, "0028")
    with engine.begin() as c:
        cols = {col["name"] for col in inspect(c).get_columns("admin_users")}
        assert not cols & NEW
        assert c.execute(text("SELECT username FROM admin_users")).scalar() == "boss"
    command.upgrade(cfg, "head")
