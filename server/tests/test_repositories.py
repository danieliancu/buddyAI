"""Versioned item writes, all-or-nothing deletes, durable voice operations, and the 0017 migration on
existing data."""

from __future__ import annotations

import os
import tempfile

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.db.models import VoiceOperation
from app.db.repositories import ItemConflictError, ItemRepo, VoiceOpRepo
from app.db.session import SERVER_DIR, session_scope
from tests.test_items import _account


def test_cas_update_and_delete_exact() -> None:
    acc = _account()
    with session_scope() as db:
        r = ItemRepo(db)
        a = r.create(acc, "note", "A\n1")
        b = r.create(acc, "note", "B\n2")
        ua, va, ub, vb = a.uid, a.version, b.uid, b.version
    with session_scope() as db:
        r = ItemRepo(db)
        it = r.update(r.get_by_uid(acc, ua), text="A\n1\n2", expected_version=va)
        assert it.version == va + 1
        with pytest.raises(ItemConflictError) as exc:  # stale version: refused, nothing written
            r.update(r.get_by_uid(acc, ua), text="A\nlost", expected_version=va)
        assert exc.value.changed == [ua]
        assert r.get_by_uid(acc, ua).text == "A\n1\n2"
        with pytest.raises(ItemConflictError):  # one stale target: nothing deleted at all
            r.delete_exact(acc, [(ub, vb), (ua, va)])
        assert r.get_by_uid(acc, ub) is not None
        assert r.delete_exact(acc, [(ub, vb), (ua, va + 1)]) == 2
        assert r.list(acc) == []


def test_delivery_marks_do_not_bump_the_version() -> None:
    from datetime import datetime, timedelta, timezone

    acc = _account()
    with session_scope() as db:
        r = ItemRepo(db)
        it = r.create(acc, "reminder", "x", datetime.now(timezone.utc) + timedelta(hours=1))
        v = it.version
        r.mark_fired(it)
        assert r.get_by_uid(acc, it.uid).version == v


def test_voice_operation_is_written_with_the_change_and_unique() -> None:
    acc = _account()
    op = VoiceOperation(op_key="k-" + os.urandom(6).hex(), account_id=acc, device_id="d", session_id="s", turn_id=1,
                        tool="item_create", summary="created", result="{}")
    with session_scope() as db:
        ItemRepo(db).create(acc, "note", "N\n1", op=op)
        assert VoiceOpRepo(db).get(op.op_key) is not None
    twin = VoiceOperation(op_key=op.op_key, account_id=acc, device_id="d", session_id="s", turn_id=1,
                          tool="item_create", summary="created", result="{}")
    with session_scope() as db, pytest.raises(sa.exc.IntegrityError):
        ItemRepo(db).create(acc, "note", "N\n1", op=twin)  # the same operation cannot commit twice
    with session_scope() as db:
        assert len(ItemRepo(db).list(acc)) == 1


def test_migration_backfills_uid_and_version() -> None:
    path = os.path.join(tempfile.mkdtemp(), "mig.db")
    url = f"sqlite:///{path}"
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "0016")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO accounts (email, name, status, session_version, created_at) "
                          "VALUES ('m@x', 'm', 'active', 1, CURRENT_TIMESTAMP)"))
        acc = c.execute(sa.text("SELECT id FROM accounts WHERE email='m@x'")).scalar()
        for n in (1, 2):
            c.execute(sa.text("INSERT INTO items (account_id, kind, number, text, pinned, created_at, updated_at) "
                              "VALUES (:a, 'note', :n, 'x', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"), {"a": acc, "n": n})
    command.upgrade(cfg, "head")
    with eng.begin() as c:
        rows = c.execute(sa.text("SELECT uid, version FROM items")).fetchall()
    assert len({r[0] for r in rows}) == 2 and all(len(r[0]) == 32 and r[1] == 1 for r in rows)
    command.downgrade(cfg, "0016")
    command.upgrade(cfg, "head")
    eng.dispose()
