"""Migration 0028: watches on the old 15 s question limit move to 30 s; a value someone chose is kept.
Runs on PostgreSQL (BUDDYAI_PG_TEST_URL) and on a temporary SQLite file."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from tests.pg.test_migration import _cfg, _urls

WATCHES = {"w15": 15, "w20": 20, "w60": 60}


def _settings(c) -> dict[str, tuple[int, int]]:
    rows = c.execute(text("SELECT device_id, version, data FROM device_settings")).all()
    return {d: (v, (data if isinstance(data, dict) else json.loads(data))["max_listen_s"]) for d, v, data in rows}


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_0028_moves_the_old_default_to_30s_and_downgrades(url):
    engine = create_engine(url)
    if url.startswith("postgresql"):
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    cfg = _cfg(url)
    command.upgrade(cfg, "0027")
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as c:
        for dev, listen in WATCHES.items():
            c.execute(text("INSERT INTO devices (id, name, hw_model, fw_version) VALUES (:d, 'w', '', '')"), {"d": dev})
            data = json.dumps({"volume": 70, "max_listen_s": listen, "vad_sensitivity": "medium"})
            c.execute(text("INSERT INTO device_settings (device_id, version, data, updated_at) VALUES (:d, 3, :j, :t)"),
                      {"d": dev, "j": data, "t": now})
    command.upgrade(cfg, "0028")
    with engine.begin() as c:
        got = _settings(c)
        assert got == {"w15": (4, 30), "w20": (3, 20), "w60": (3, 60)}  # version up only where it changed
        raw = c.execute(text("SELECT data FROM device_settings WHERE device_id = 'w15'")).scalar()
        assert (raw if isinstance(raw, dict) else json.loads(raw))["volume"] == 70  # the rest is untouched
    command.downgrade(cfg, "0027")
    with engine.begin() as c:
        assert {d: m for d, (_, m) in _settings(c).items()} == {"w15": 15, "w20": 20, "w60": 60}
    command.upgrade(cfg, "head")
