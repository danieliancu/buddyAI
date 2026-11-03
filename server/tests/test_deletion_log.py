"""Deletion outbox: an event exists exactly when the deletion does, and a restored backup does not bring data back."""

from __future__ import annotations

import json
import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import accounts, cli, deletion_log
from app.db.models import Account, Conversation, DeletionEvent, DeletionReconcileRequired, Item, Memory, Turn, utcnow
from app.db.repositories import ConversationRepo, ItemRepo
from app.db.session import session_scope
from app.main import app
from app.memory.repo import MemoryRepo
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP
from tests.test_accounts import _customer, _give_watch


@pytest.fixture(autouse=True)
def _no_limits():
    for limiter in (LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP):
        limiter._hits.clear()
    yield


def _item(account_id: int) -> str:
    with session_scope() as db:
        it = ItemRepo(db).create(account_id, "note", text="a note")
        return it.uid


def _memory(account_id: int) -> str:
    with session_scope() as db:
        m = Memory(account_id=account_id, kind="other", content="a fact", content_hash=secrets.token_hex(16), origin="explicit")
        db.add(m)
        db.commit()
        return m.uid


def _events(**where) -> list[DeletionEvent]:
    with session_scope() as db:
        q = select(DeletionEvent)
        for k, v in where.items():
            q = q.where(getattr(DeletionEvent, k) == v)
        rows = db.exec(q).all()
        for r in rows:
            db.expunge(r)
        return list(rows)


def _as_ledger(rows: list[DeletionEvent]) -> list[dict]:
    return [{"uid": r.uid, "kind": r.kind, "account_id": r.account_id, "device_id": r.device_id, "target": r.target,
             "occurred_at": r.occurred_at.isoformat()} for r in rows]


def _forget_events(rows: list[DeletionEvent]) -> None:
    """What a backup taken before these deletions looks like: it has no such events."""
    with session_scope() as db:
        for r in rows:
            db.delete(db.get(DeletionEvent, r.id))
        db.commit()


def _turns(device_id: str) -> int:
    with session_scope() as db:
        return len(db.exec(select(Turn).where(Turn.device_id == device_id)).all())


def _bring_back_history(device_id: str, account_id: int) -> None:
    with session_scope() as db:
        conv = Conversation(device_id=device_id)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(Turn(device_id=device_id, account_id=account_id, conversation_id=conv.id, session_id="s", turn_no=1,
                    language="en", status="completed", user_text="restored", assistant_text="restored"))
        db.commit()


# --- every path writes the event, in the same transaction -------------------------------------------------------


def test_customer_history_delete_writes_one_event():
    c, me = _customer()
    dev = _give_watch(me["id"])
    assert c.delete(f"/api/me/devices/{dev}/conversations").status_code == 200
    rows = _events(device_id=dev, kind=deletion_log.DEVICE_HISTORY)
    assert len(rows) == 1 and rows[0].replicated_at is None and rows[0].target == ""


def test_item_and_memory_paths_write_events():
    _, me = _customer()
    note, voice_note, memory = _item(me["id"]), _item(me["id"]), _memory(me["id"])
    with session_scope() as db:
        repo = ItemRepo(db)
        repo.delete(db.exec(select(Item).where(Item.uid == note)).one())
        target = db.exec(select(Item).where(Item.uid == voice_note)).one()
        repo.delete_exact(me["id"], [(target.uid, target.version)])
        MemoryRepo(db).forget(me["id"], memory)
        MemoryRepo(db).forget_all(me["id"])
    assert len(_events(target=note)) == 1 and len(_events(target=voice_note)) == 1 and len(_events(target=memory)) == 1
    assert len(_events(account_id=me["id"], kind=deletion_log.ALL_MEMORIES)) == 1


def test_account_delete_writes_an_event():
    _, me = _customer()
    with session_scope() as db:
        accounts.delete_account(db, db.get(Account, me["id"]))
    assert len(_events(account_id=me["id"], kind=deletion_log.ACCOUNT)) == 1


def test_a_cancelled_deletion_leaves_no_event_and_no_deletion():
    _, me = _customer()
    dev = _give_watch(me["id"])
    with session_scope() as db:
        ConversationRepo(db).delete_device_history(dev, commit=False)
        db.rollback()
    assert _turns(dev) == 1 and _events(device_id=dev) == []


def test_a_refused_delete_exact_leaves_no_event():
    from app.db.repositories import ItemConflictError

    _, me = _customer()
    uid = _item(me["id"])
    with session_scope() as db:
        with pytest.raises(ItemConflictError):
            ItemRepo(db).delete_exact(me["id"], [(uid, 999)])
    assert _events(target=uid) == []


def test_events_hold_no_content():
    columns = set(DeletionEvent.model_fields)
    assert columns == {"id", "uid", "kind", "account_id", "device_id", "target", "occurred_at", "replicated_at"}


# --- after a restore ----------------------------------------------------------------------------------------------


def test_restored_data_is_deleted_again_and_a_second_run_changes_nothing():
    _, me = _customer()
    dev = _give_watch(me["id"])
    with session_scope() as db:
        ConversationRepo(db).delete_device_history(dev)
    rows = _events(device_id=dev)
    ledger = _as_ledger(rows)
    _forget_events(rows)  # the backup's outbox and audit log know nothing of this deletion
    _bring_back_history(dev, me["id"])
    assert _turns(dev) == 1

    with session_scope() as db:
        first = deletion_log.reconcile(db, ledger)
    assert first["applied"] == 1 and first["failed"] == [] and _turns(dev) == 0

    _bring_back_history(dev, me["id"])  # nothing new: the event is now stored, so it is not applied twice
    with session_scope() as db:
        second = deletion_log.reconcile(db, ledger)
    assert second["applied"] == 0 and second["already_in_database"] == 1 and _turns(dev) == 1


def test_a_deletion_the_backup_already_holds_is_not_applied_to_newer_data():
    _, me = _customer()
    dev = _give_watch(me["id"])
    with session_scope() as db:
        ConversationRepo(db).delete_device_history(dev)
    ledger = _as_ledger(_events(device_id=dev))
    _bring_back_history(dev, me["id"])  # said after that deletion and before the backup
    with session_scope() as db:
        assert deletion_log.reconcile(db, ledger)["already_in_database"] == 1
    assert _turns(dev) == 1


def test_dry_run_changes_nothing():
    _, me = _customer()
    dev = _give_watch(me["id"])
    event = {"uid": secrets.token_hex(16), "kind": deletion_log.DEVICE_HISTORY, "device_id": dev,
             "occurred_at": utcnow().isoformat()}
    with session_scope() as db:
        out = deletion_log.reconcile(db, [event], dry_run=True)
    assert len(out["would_apply"]) == 1 and _turns(dev) == 1 and _events(uid=event["uid"]) == []


def test_ledger_events_older_than_any_backup_are_ignored():
    _, me = _customer()
    dev = _give_watch(me["id"])
    old = utcnow() - timedelta(days=deletion_log.LEDGER_MAX_AGE_DAYS + 1)
    event = {"uid": secrets.token_hex(16), "kind": deletion_log.DEVICE_HISTORY, "device_id": dev, "occurred_at": old.isoformat()}
    with session_scope() as db:
        assert deletion_log.reconcile(db, [event])["too_old"] == 1
    assert _turns(dev) == 1


def test_replaying_an_account_deletion_does_not_call_stripe(monkeypatch):
    from app import billing

    _, me = _customer()
    monkeypatch.setattr(billing, "cancel_all_for_account", lambda *a: pytest.fail("Stripe was called"))
    event = {"uid": secrets.token_hex(16), "kind": deletion_log.ACCOUNT, "account_id": me["id"],
             "occurred_at": utcnow().isoformat()}
    with session_scope() as db:
        assert deletion_log.reconcile(db, [event])["applied"] == 1
        assert db.get(Account, me["id"]).status == "deleted"


def test_a_failed_event_keeps_the_rest_and_is_reported():
    _, me = _customer()
    dev = _give_watch(me["id"])
    bad = {"uid": secrets.token_hex(16), "kind": "unknown", "occurred_at": utcnow().isoformat()}
    good = {"uid": secrets.token_hex(16), "kind": deletion_log.DEVICE_HISTORY, "device_id": dev, "occurred_at": utcnow().isoformat()}
    with session_scope() as db:
        out = deletion_log.reconcile(db, [bad, good])
    assert out["applied"] == 1 and len(out["failed"]) == 1 and _turns(dev) == 0


def test_load_ledger_drops_repeats_from_a_crash_between_upload_and_marking(tmp_path):
    event = {"uid": "a" * 32, "kind": "item", "target": "x", "occurred_at": "2026-11-03T10:00:00"}
    (tmp_path / "one.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    (tmp_path / "two.jsonl").write_text(json.dumps(event) + "\n" + json.dumps({**event, "uid": "b" * 32}) + "\n", encoding="utf-8")
    assert [e["uid"] for e in deletion_log.load_ledger([tmp_path])] == ["a" * 32, "b" * 32]


# --- the block on a restored database --------------------------------------------------------------------------


def _mark_restored() -> None:
    with session_scope() as db:
        db.add(DeletionReconcileRequired(backup_created="2026-11-01"))
        db.commit()


def test_server_refuses_to_start_until_reconciled(tmp_path):
    _mark_restored()
    try:
        with pytest.raises(RuntimeError, match="reconcile-deletions"):
            deletion_log.refuse_start_if_unreconciled()
        (tmp_path / "l.jsonl").write_text("", encoding="utf-8")
        with session_scope() as db:
            assert cli._reconcile_deletions(db, [tmp_path], dry_run=True, no_ledger=False) == 0
            assert deletion_log.reconcile_required(db)  # a dry run lifts nothing
            assert cli._reconcile_deletions(db, [tmp_path], dry_run=False, no_ledger=False) == 0
            assert not deletion_log.reconcile_required(db)
        deletion_log.refuse_start_if_unreconciled()
    finally:
        with session_scope() as db:
            deletion_log.clear_required(db)


def test_the_block_stays_when_a_deletion_fails(tmp_path):
    _mark_restored()
    bad = {"uid": secrets.token_hex(16), "kind": "unknown", "occurred_at": utcnow().isoformat()}
    (tmp_path / "l.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
    try:
        with session_scope() as db:
            assert cli._reconcile_deletions(db, [tmp_path], dry_run=False, no_ledger=False) == 1
            assert deletion_log.reconcile_required(db)
    finally:
        with session_scope() as db:
            deletion_log.clear_required(db)


def test_no_ledger_needs_saying_so():
    with session_scope() as db:
        assert cli._reconcile_deletions(db, [], dry_run=False, no_ledger=False) == 2


# --- monitoring and retention ---------------------------------------------------------------------------------


def test_status_reports_the_oldest_unreplicated_event():
    with session_scope() as db:
        before = deletion_log.status(db)
        db.add(DeletionEvent(kind=deletion_log.ITEM, target="x", occurred_at=utcnow() - timedelta(hours=2)))
        db.commit()
        state = deletion_log.status(db)
    assert state["pending"] == before["pending"] + 1 and state["oldest_pending_s"] >= 7200


def test_prune_keeps_unreplicated_events():
    old = utcnow() - timedelta(days=deletion_log.OUTBOX_KEEP_DAYS + 1)
    done, waiting = secrets.token_hex(16), secrets.token_hex(16)
    with session_scope() as db:
        db.add(DeletionEvent(uid=done, kind=deletion_log.ITEM, occurred_at=old, replicated_at=old))
        db.add(DeletionEvent(uid=waiting, kind=deletion_log.ITEM, occurred_at=old))
        db.commit()
    deletion_log.prune()
    assert _events(uid=done) == [] and len(_events(uid=waiting)) == 1
    with session_scope() as db:  # leave no ancient pending row for other tests
        db.delete(db.exec(select(DeletionEvent).where(DeletionEvent.uid == waiting)).one())
        db.commit()


def test_system_page_shows_the_ledger_state():
    from tests.test_operator_security import _signed_in

    c, _ = _signed_in("admin")
    assert isinstance(c, TestClient) and app is not None
    assert set(c.get("/api/system/info").json()["deletion_ledger"]) == {"enabled", "pending", "oldest_pending_s"}
