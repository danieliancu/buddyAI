"""Conversation RAG, phase B (docs/CONVERSATION_RAG.md): the schema, the database-level cascades and the per-watch
authorisation. The same cascade guarantees are checked on PostgreSQL in tests/pg/test_conversation_rag_pg.py."""

from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app import accounts, retention
from app.config import get_settings
from app.conversation_rag import authz
from app.db.models import (Account, ConversationRagAuthorization, ConversationRagEmbedding, ConversationRagSource,
                           Device, Turn, utcnow)
from app.db.repositories import ConversationRepo, DeviceRepo, SettingsRepo, TurnRepo
from app.db.session import session_scope
from app.security import hash_device_token
from tests.test_items import _account


@pytest.fixture(autouse=True)
def rag_on():
    s = get_settings()
    saved = (s.conversation_rag_enabled, s.conversation_rag_accounts)
    s.conversation_rag_enabled, s.conversation_rag_accounts = True, ""
    yield s
    s.conversation_rag_enabled, s.conversation_rag_accounts = saved


def _device(acc: int) -> str:
    device_id = f"dev-{secrets.token_hex(4)}"
    with session_scope() as db:
        DeviceRepo(db).pair(device_id, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", acc)
        SettingsRepo(db).ensure(device_id)
    return device_id


def _turn(acc: int, device_id: str, user: str = "We talked about Crete", reply: str = "Crete sounds lovely") -> int:
    with session_scope() as db:
        conv = ConversationRepo(db).current(device_id, 30)
        t = TurnRepo(db).create(device_id=device_id, account_id=acc, conversation_id=conv.id, session_id="s",
                                turn_no=1, language="en", status="completed", user_text=user, assistant_text=reply)
        return t.id


def _vector(acc: int, device_id: str, turn_id: int, model_key: str = "mock:m:3") -> None:
    with session_scope() as db:
        db.add(ConversationRagEmbedding(turn_id=turn_id, account_id=acc, device_id=device_id, model_key=model_key,
                                        dims=3, text_hash="h", embedding=[0.1, 0.2, 0.3]))
        db.commit()


def _source(consumer: int, source: int) -> None:
    with session_scope() as db:
        db.add(ConversationRagSource(consumer_turn_id=consumer, source_turn_id=source))
        db.commit()


def _count(model, **where) -> int:
    with session_scope() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return len(db.exec(q).all())


def _opt_in(acc: int, device_id: str, wearer: str = "self", enabled: bool = True) -> None:
    with session_scope() as db:
        authz.set_choice(db, acc, device_id, wearer, enabled)
        db.commit()


# --- schema ------------------------------------------------------------------------------------------------


def test_new_turn_columns_have_safe_defaults():
    acc = _account()
    dev = _device(acc)
    tid = _turn(acc, dev)
    with session_scope() as db:
        t = db.get(Turn, tid)
        assert t.rag_derived is False and t.rag_redacted_at is None


def test_one_vector_per_turn_and_model():
    acc = _account()
    dev = _device(acc)
    tid = _turn(acc, dev)
    _vector(acc, dev, tid)
    _vector(acc, dev, tid, model_key="mock:other:3")  # another model space: allowed
    with pytest.raises(IntegrityError):
        _vector(acc, dev, tid)


def test_deleting_a_turn_deletes_its_vectors_and_references_at_database_level():
    acc = _account()
    dev = _device(acc)
    old, new = _turn(acc, dev), _turn(acc, dev, "What did we say about Crete?", "That it sounds lovely")
    _vector(acc, dev, old)
    _source(new, old)
    with session_scope() as db:  # plain SQL: nothing in the application cleans up
        db.execute(text("DELETE FROM turns WHERE id = :i"), {"i": old})
        db.commit()
    assert _count(ConversationRagEmbedding, turn_id=old) == 0
    assert _count(ConversationRagSource, consumer_turn_id=new) == 0
    with session_scope() as db:  # the consumer turn and its marker stay
        assert db.get(Turn, new) is not None


# --- deletion paths ----------------------------------------------------------------------------------------


def test_clearing_watch_history_removes_vectors_and_references_but_keeps_the_choice():
    acc = _account()
    dev = _device(acc)
    _opt_in(acc, dev)
    a, b = _turn(acc, dev), _turn(acc, dev)
    _vector(acc, dev, a)
    _source(b, a)
    with session_scope() as db:
        ConversationRepo(db).delete_device_history(dev)
    assert _count(ConversationRagEmbedding, device_id=dev) == 0
    assert _count(ConversationRagSource, source_turn_id=a) == 0
    with session_scope() as db:
        assert authz.access(db, acc, dev).on


def test_retention_bulk_delete_cascades():
    acc = _account()
    dev = _device(acc)
    a, b = _turn(acc, dev), _turn(acc, dev)
    _vector(acc, dev, a)
    _source(b, a)
    with session_scope() as db:
        retention.delete_turns(db, [a])
        db.commit()
    assert _count(ConversationRagEmbedding, turn_id=a) == 0
    assert _count(ConversationRagSource, consumer_turn_id=b) == 0


@pytest.mark.parametrize("how", ["unassign", "assign", "pair"])
def test_a_new_owner_starts_without_the_previous_choice_or_vectors(how):
    acc, other = _account(), _account()
    dev = _device(acc)
    _opt_in(acc, dev)
    _vector(acc, dev, _turn(acc, dev))
    with session_scope() as db:
        repo = DeviceRepo(db)
        if how == "unassign":
            repo.revoke(dev, unassign=True)
        elif how == "assign":
            repo.assign(dev, other)
        else:
            repo.pair(dev, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", other)
    assert _count(ConversationRagAuthorization, device_id=dev) == 0
    assert _count(ConversationRagEmbedding, device_id=dev) == 0
    with session_scope() as db:
        assert not authz.access(db, acc, dev).on  # the previous owner has no way back in
        # The new owner starts from the default, not from the previous owner's choice.
        assert authz.access(db, other, dev).on is (how != "unassign")
    if how == "unassign":
        assert _count(ConversationRagAuthorization, device_id=dev) == 0


def test_account_deletion_removes_choices_and_vectors():
    acc = _account()
    dev = _device(acc)
    _opt_in(acc, dev)
    _vector(acc, dev, _turn(acc, dev))
    with session_scope() as db:
        accounts.delete_account(db, db.get(Account, acc))
    assert _count(ConversationRagAuthorization, account_id=acc) == 0
    assert _count(ConversationRagEmbedding, account_id=acc) == 0


# --- authorisation -----------------------------------------------------------------------------------------


def test_on_by_default_until_the_owner_says_otherwise():
    acc = _account()
    dev = _device(acc)
    with session_scope() as db:
        a = authz.access(db, acc, dev)  # existing watch: no row yet
        v = authz.view(db, acc, dev)
        method = db.get(ConversationRagAuthorization, dev).method
    assert a.on and v["enabled"] and v["wearer"] == "self" and v["authorized_at"] and method == "default"
    _opt_in(acc, dev, enabled=False)
    with session_scope() as db:
        assert not authz.access(db, acc, dev).on  # a switch-off stays: the default is not applied again
        assert authz.view(db, acc, dev)["enabled"] is False
    _opt_in(acc, dev)
    with session_scope() as db:
        assert authz.access(db, acc, dev).on


def test_a_watch_declared_as_someone_elses_stays_off():
    acc = _account()
    dev = _device(acc)
    with session_scope() as db:
        authz.set_choice(db, acc, dev, "other", False)
        db.commit()
    with session_scope() as db:
        assert not authz.access(db, acc, dev).on and authz.view(db, acc, dev)["wearer"] == "other"


def test_the_default_is_never_created_for_a_watch_of_another_account():
    acc, other = _account(), _account()
    dev = _device(acc)
    with session_scope() as db:
        assert not authz.access(db, other, dev).on
    assert _count(ConversationRagAuthorization, device_id=dev) == 0


def test_someone_elses_watch_cannot_be_switched_on():
    acc = _account()
    dev = _device(acc)
    with session_scope() as db, pytest.raises(ValueError):
        authz.set_choice(db, acc, dev, "other", True)
    _opt_in(acc, dev, wearer="other", enabled=False)
    with session_scope() as db, pytest.raises(IntegrityError):  # not even by writing the row directly
        db.execute(text("UPDATE conversation_rag_authorizations SET state = 'on' WHERE device_id = :d"), {"d": dev})
        db.commit()


def test_switching_off_deletes_vectors_and_invalidates_running_work():
    acc = _account()
    dev = _device(acc)
    _opt_in(acc, dev)
    with session_scope() as db:
        gen = authz.access(db, acc, dev).generation
    _vector(acc, dev, _turn(acc, dev))
    _opt_in(acc, dev, enabled=False)
    assert _count(ConversationRagEmbedding, device_id=dev) == 0
    _opt_in(acc, dev)  # back on: a new generation, not the old one
    with session_scope() as db:
        assert not authz.still_allowed(db, acc, dev, gen)
        assert authz.still_allowed(db, acc, dev, authz.access(db, acc, dev).generation)


def test_declaring_another_wearer_switches_off():
    acc = _account()
    dev = _device(acc)
    _opt_in(acc, dev)
    _vector(acc, dev, _turn(acc, dev))
    _opt_in(acc, dev, wearer="other", enabled=False)
    with session_scope() as db:
        assert not authz.access(db, acc, dev).on
    assert _count(ConversationRagEmbedding, device_id=dev) == 0


def test_access_needs_the_server_switch_the_allowlist_and_current_ownership(rag_on):
    acc, other = _account(), _account()
    dev = _device(acc)
    _opt_in(acc, dev)
    with session_scope() as db:
        assert not authz.access(db, other, dev).on  # another account, same watch id
    rag_on.conversation_rag_accounts = str(other)
    with session_scope() as db:
        assert not authz.access(db, acc, dev).on
    rag_on.conversation_rag_accounts = ""
    rag_on.conversation_rag_enabled = False  # kill switch: off, but the choice is kept
    with session_scope() as db:
        assert not authz.access(db, acc, dev).on
    assert _count(ConversationRagAuthorization, device_id=dev, state="on") == 1
    rag_on.conversation_rag_enabled = True
    with session_scope() as db:  # a revoked watch is off
        d = db.get(Device, dev)
        d.revoked_at = utcnow() - timedelta(seconds=1)
        db.add(d)
        db.commit()
        assert not authz.access(db, acc, dev).on
