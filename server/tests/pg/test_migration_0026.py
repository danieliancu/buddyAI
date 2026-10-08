"""Migration 0026 (ola Diagnostics) keeps every existing issue, turns each into one legacy incident classified
only from what was stored (no invented details), and downgrades cleanly.
Runs on PostgreSQL (BUDDYAI_PG_TEST_URL) and on a temporary SQLite file."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.pg.test_migration import _cfg, _urls

ROWS = [  # kind, reason, severity -> category, confidence, reason_code, component
    ("reboot", "panic", "error", "watch", "confirmed", "watch_crash", "firmware"),
    ("reboot", "task_wdt", "error", "watch", "confirmed", "watch_freeze", "firmware"),
    ("reboot", "brownout", "error", "watch", "confirmed", "watch_power_fault", "power"),
    ("reboot", "power_on", "info", "watch", "confirmed", "watch_restart", None),
    ("reboot", "mystery", "warn", "watch", "unknown", "watch_restart_unknown", None),
    ("disconnect", "wifi_lost", "warn", "connection", "confirmed", "wifi_lost", "wifi"),
    ("disconnect", "ws_error", "warn", "undetermined", "unknown", "link_lost_unknown", None),
    ("disconnect", "ws_closed", "warn", "undetermined", "unknown", "link_lost_unknown", None),
    ("turn_interrupted", "", "warn", "undetermined", "unknown", "turn_interrupted", None),
    ("server_timeout", "", "warn", "undetermined", "unknown", "watch_silent", None),
]


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_0026_backfills_legacy_incidents_and_downgrades(url):
    engine = create_engine(url)
    if url.startswith("postgresql"):
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    cfg = _cfg(url)
    command.upgrade(cfg, "0025")
    when = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    with engine.begin() as c:
        for kind, reason, sev, *_ in ROWS:
            c.execute(text("INSERT INTO device_issues (device_id, account_id, kind, severity, reason, detail, fw_version, "
                           "created_at) VALUES ('w1', 7, :k, :s, :r, '{\"prev_uptime_s\": 5}', '0.1.0', :t)"),
                      {"k": kind, "s": sev, "r": reason, "t": when})
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        incs = c.execute(text("SELECT i.category, i.confidence, i.reason_code, i.suspected_component, i.severity, "
                              "i.legacy, i.event_count, i.session_id, i.turn_id, i.recovered_at, i.account_id, "
                              "d.kind, d.reason, d.session_id AS ev_session, d.category AS ev_category, d.dedup_key, d.occurred_at "
                              "FROM diag_incidents i JOIN device_issues d ON d.incident_id = i.id "
                              "AND i.primary_event_id = d.id ORDER BY d.id")).all()
        assert len(incs) == len(ROWS)
        for row, (kind, reason, sev, cat, conf, code, comp) in zip(incs, ROWS):
            assert (row.kind, row.reason) == (kind, reason)
            assert (row.category, row.confidence, row.reason_code, row.suspected_component, row.severity) == (
                cat, conf, code, comp, sev)
            assert row.legacy in (True, 1) and row.event_count == 1 and row.account_id == 7
            # nothing invented: no session, turn, recovery, or per-event diagnostics on old rows
            assert row.session_id is None and row.turn_id is None and row.recovered_at is None
            assert row.ev_session is None and row.ev_category is None and row.dedup_key is None and row.occurred_at is None
        detail = c.execute(text("SELECT detail FROM device_issues LIMIT 1")).scalar()
        assert "prev_uptime_s" in str(detail)
        assert "last_session_id" in {col["name"] for col in inspect(c).get_columns("devices")}
    command.downgrade(cfg, "0025")
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM device_issues")).scalar() == len(ROWS)
        insp = inspect(c)
        assert "diag_incidents" not in insp.get_table_names()
        assert "incident_id" not in {col["name"] for col in insp.get_columns("device_issues")}
        assert "last_session_id" not in {col["name"] for col in insp.get_columns("devices")}
    command.upgrade(cfg, "head")  # and up again
