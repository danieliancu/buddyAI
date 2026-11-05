"""Conversation RAG, phase E (docs/CONVERSATION_RAG.md): what a reply built from earlier conversations may not leave
behind. Automatic learning skips it, later turns inherit its sources, retention removes it with its source, and every
deletion path reaches vectors, references and the live session."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app import accounts, retention
from app.conversation_rag import authz, context, search as rag_search
from app.db.models import (Conversation, ConversationRagAuthorization, ConversationRagEmbedding, ConversationRagSource,
                           Memory, MemoryJob, Turn, utcnow)
from app.db.repositories import ConversationRepo, DeviceRepo
from app.db.session import session_scope
from app.device_settings import DeviceSettings
from app.memory import extract
from app.memory.repo import MemoryRepo
from app.pipeline.conversation import build_messages_from_db
from app.pipeline.turn import TurnContext
from app.providers.llm.base import LLMChunk, LLMProvider
from tests.test_accounts import _customer, _give_watch
from tests.test_conversation_rag import _During, _generation, _index, _search, _turn, _vectors, _watch, rag_on  # noqa: F401
from tests.test_items import _account


def _new_conversation(dev: str) -> int:
    with session_scope() as db:
        conv = Conversation(device_id=dev)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return conv.id


def _refer(consumer: int, *sources: int) -> None:
    with session_scope() as db:
        for s in sources:
            db.add(ConversationRagSource(consumer_turn_id=consumer, source_turn_id=s))
        db.commit()


def _row(turn_id: int) -> Turn | None:
    with session_scope() as db:
        t = db.get(Turn, turn_id)
        if t is not None:
            db.expunge(t)
        return t


def _refs(consumer: int) -> list[int]:
    with session_scope() as db:
        return list(db.exec(select(ConversationRagSource.source_turn_id)
                            .where(ConversationRagSource.consumer_turn_id == consumer)).all())


# --- automatic learning ------------------------------------------------------------------------------------


def _job(acc: int, conv: int, upto: int) -> MemoryJob:
    return MemoryJob(kind="extract", account_id=acc, conversation_id=conv, upto_turn_id=upto)


def test_learning_never_reads_turns_built_from_earlier_conversations():
    acc, dev = _watch()
    with session_scope() as db:
        MemoryRepo(db).set_prefs(acc, learn=True)
    conv = _new_conversation(dev)
    own = _turn(acc, dev, "My daughter moved to Leeds", "Lovely", conversation_id=conv)
    last = _turn(acc, dev, "what did I say about the clinic", "You had a thyroid test in March", conversation_id=conv,
                 rag_derived=True)
    mat = extract._material(_job(acc, conv, last))
    assert [t[0] for t in mat["turns"]] == [own]
    assert "thyroid" not in json.dumps(mat)


def test_a_conversation_of_only_such_turns_costs_no_model_call():
    acc, dev = _watch()
    with session_scope() as db:
        MemoryRepo(db).set_prefs(acc, learn=True)
    conv = _new_conversation(dev)
    last = _turn(acc, dev, "what did I say about the clinic", "A thyroid test", conversation_id=conv, rag_derived=True)

    class NoCalls(LLMProvider):
        name = "none"

        async def stream(self, request):
            raise AssertionError("no model call for a conversation with nothing to learn from")
            yield LLMChunk(delta="")

    asyncio.run(extract.run_extract(SimpleNamespace(llm_default=lambda: (NoCalls(), "m")), _job(acc, conv, last)))
    with session_scope() as db:
        assert not db.exec(select(Memory).where(Memory.account_id == acc)).all()


# --- later turns of the conversation depend on the same sources ----------------------------------------------


def _prompt(acc: int, dev: str, conv: int, text: str = "and then?") -> tuple[list[dict], TurnContext]:
    turn = TurnContext(5, "s", dev, "en", DeviceSettings(), 16000, account_id=acc, conversation_id=conv)
    return build_messages_from_db(turn, text), turn


def test_a_rag_reply_in_the_prompt_makes_the_turn_depend_on_its_original_sources():
    acc, dev = _watch()
    source = _turn(acc, dev, "We planned Crete", "Book the ferry")
    conv = _new_conversation(dev)
    rag = _turn(acc, dev, "what about Crete?", "You planned the ferry", conversation_id=conv, rag_derived=True)
    _refer(rag, source)
    messages, turn = _prompt(acc, dev, conv)
    assert any("You planned the ferry" in str(m["content"]) for m in messages)  # the reply is in this prompt
    assert turn.rag_derived and turn.rag_sources == [source]  # refers to the original, not to the reply
    assert rag not in turn.rag_sources


def test_an_ordinary_history_marks_nothing():
    acc, dev = _watch()
    conv = _new_conversation(dev)
    _turn(acc, dev, "tell me a joke", "knock knock", conversation_id=conv)
    _, turn = _prompt(acc, dev, conv)
    assert not turn.rag_derived and turn.rag_sources == []


def test_a_removed_reply_stays_out_of_the_prompt_and_marks_nothing():
    acc, dev = _watch()
    conv = _new_conversation(dev)
    _turn(acc, dev, "what about Crete?", "", conversation_id=conv, rag_derived=True, rag_redacted_at=utcnow())
    messages, turn = _prompt(acc, dev, conv)
    assert not any("what about Crete" in str(m["content"]) for m in messages)
    assert not turn.rag_derived


def test_later_turns_are_neither_indexed_nor_found():
    acc, dev = _watch()
    source = _turn(acc, dev, "We planned Crete", "Book the ferry")
    conv = _new_conversation(dev)
    rag = _turn(acc, dev, "Crete plans?", "Ferry, you said", conversation_id=conv, rag_derived=True)
    _refer(rag, source)
    assert _index() == 1 and _vectors(dev) == {source}
    assert rag not in [h.turn_id for h in _search(acc, dev, "Crete ferry").hits]


# --- retention: the only deletion that keeps later turns ------------------------------------------------------


def _policy(days: int = 30) -> retention.RetentionPolicy:
    return retention.RetentionPolicy(conversation_days=days)


def test_retention_removes_the_reply_built_from_an_expired_source():
    acc, dev = _watch()
    old = _turn(acc, dev, "I had a thyroid test", "Noted", created_at=utcnow() - timedelta(days=60))
    unrelated = _turn(acc, dev, "tell me a joke", "knock knock")
    rag = _turn(acc, dev, "what test did I have?", "A thyroid test", rag_derived=True, search_note="x -> thyroid")
    follow = _turn(acc, dev, "when?", "In March, the thyroid test", rag_derived=True)
    _refer(rag, old)
    _refer(follow, old)
    assert _index() == 2  # the source and the unrelated turn
    assert retention.prune_conversations(_policy(), utcnow()) >= 1  # the database is shared with other tests' old turns
    assert _row(old) is None and old not in _vectors(dev)  # vector gone by cascade
    for tid in (rag, follow):
        t = _row(tid)
        assert t.assistant_text == "" and t.search_note == "" and t.rag_redacted_at is not None
        assert t.user_text and t.rag_derived  # the question and the marker stay
        assert _refs(tid) == []
    assert _row(unrelated).assistant_text == "knock knock" and _row(unrelated).rag_redacted_at is None


def test_a_reply_expiring_with_its_source_needs_no_redaction():
    acc, dev = _watch()
    long_ago = utcnow() - timedelta(days=60)
    old = _turn(acc, dev, created_at=long_ago)
    rag = _turn(acc, dev, "x", "y", rag_derived=True, created_at=long_ago)
    _refer(rag, old)
    assert retention.prune_conversations(_policy(), utcnow()) >= 2
    assert _row(old) is None and _row(rag) is None


def test_retention_off_touches_nothing():
    acc, dev = _watch()
    old = _turn(acc, dev, created_at=utcnow() - timedelta(days=4000))
    rag = _turn(acc, dev, "x", "y", rag_derived=True)
    _refer(rag, old)
    assert retention.prune_conversations(_policy(0), utcnow()) == 0
    assert _row(old) is not None and _row(rag).assistant_text == "y"


def test_a_removed_reply_is_marked_in_history_and_export():
    c, me = _customer()
    try:
        acc = me["id"]
        dev = _give_watch(acc, with_history=False)
        old = _turn(acc, dev, created_at=utcnow() - timedelta(days=60))
        rag = _turn(acc, dev, "what test?", "A thyroid test", rag_derived=True)
        _refer(rag, old)
        retention.prune_conversations(_policy(), utcnow())
        turns = [t for conv in c.get(f"/api/me/devices/{dev}/conversations").json() for t in conv["turns"]]
        row = next(t for t in turns if t["id"] == rag)
        assert row["redacted"] is True and row["assistant_text"] == "" and row["user_text"] == "what test?"
        assert "thyroid" not in json.dumps(turns)
        assert "source_turn" not in json.dumps(row)  # no internal references for the customer
        with session_scope() as db:
            exported = accounts.export(db, db.get(accounts.Account, acc))
        device = next(d for d in exported["watches"] if d["id"] == dev)
        assert [t for t in device["conversation_turns"] if t.get("reply_removed")]
        assert "thyroid" not in json.dumps(exported, default=str)
    finally:
        c.__exit__(None, None, None)


def test_an_independent_memory_survives_every_conversation_deletion():
    acc, dev = _watch()
    with session_scope() as db:
        saved = MemoryRepo(db).save(acc, "User likes Crete", "preference", "explicit", device_id=dev).memory.id
    old = _turn(acc, dev, created_at=utcnow() - timedelta(days=60))
    rag = _turn(acc, dev, "x", "y", rag_derived=True)
    _refer(rag, old)
    retention.prune_conversations(_policy(), utcnow())
    with session_scope() as db:
        ConversationRepo(db).delete_device_history(dev)
        assert db.get(Memory, saved) is not None


# --- deletion paths through the API, and the live session -------------------------------------------------------


def _live(c, dev: str) -> SimpleNamespace:
    conn = SimpleNamespace(rag=authz.Access(True, 1), rag_context=context.RagContext(1, (1,), 2), device_id=dev,
                           authenticated=False, active=None)
    c.app.state.hub.connections[dev] = conn
    return conn


def test_customer_clearing_history_reaches_vectors_references_and_the_session():
    c, me = _customer()
    try:
        acc = me["id"]
        dev = _give_watch(acc, with_history=False)
        authz_url = f"/api/me/devices/{dev}/conversation-search"
        assert c.put(authz_url, json={"wearer": "self", "enabled": True}).status_code == 200
        a, b = _turn(acc, dev), _turn(acc, dev, "x", "y", rag_derived=True)
        _refer(b, a)
        _index()
        assert _vectors(dev)
        conn = _live(c, dev)
        assert c.delete(f"/api/me/devices/{dev}/conversations").status_code == 200
        c.app.state.hub.connections.pop(dev, None)
        assert _vectors(dev) == set() and _refs(b) == [] and conn.rag_context is None
        assert c.get(authz_url).json()["enabled"] is True  # the choice is not history
    finally:
        c.__exit__(None, None, None)


def test_administrator_clearing_history_reaches_vectors_and_the_session():
    import pyotp

    from tests.test_operator_security import PASSWORD, _enable_mfa, _signed_in

    c, _ = _signed_in("principal")
    secret, _ = _enable_mfa(c)
    try:
        acc, dev = _watch()
        a = _turn(acc, dev)
        _index()
        assert a in _vectors(dev)
        conn = _live(c, dev)
        body = {"reason": "The customer asked us to clear this history.", "password": PASSWORD,
                "code": pyotp.TOTP(secret).now()}
        assert c.post(f"/api/devices/{dev}/conversations/delete", json=body).status_code == 200
        c.app.state.hub.connections.pop(dev, None)
        assert _vectors(dev) == set() and conn.rag_context is None
    finally:
        c.__exit__(None, None, None)


def test_withdrawal_through_the_website_stops_the_live_session_and_keeps_the_conversations():
    c, me = _customer()
    try:
        acc = me["id"]
        dev = _give_watch(acc, with_history=False)
        url = f"/api/me/devices/{dev}/conversation-search"
        c.put(url, json={"wearer": "self", "enabled": True})
        tid = _turn(acc, dev)
        _index()
        conn = _live(c, dev)
        assert c.put(url, json={"wearer": "self", "enabled": False}).status_code == 200
        c.app.state.hub.connections.pop(dev, None)
        assert not conn.rag.on and conn.rag_context is None
        assert _vectors(dev) == set() and _row(tid) is not None
    finally:
        c.__exit__(None, None, None)


def test_switching_on_reaches_a_watch_already_connected():
    c, me = _customer()
    try:
        dev = _give_watch(me["id"], with_history=False)
        conn = _live(c, dev)
        conn.rag = authz.OFF
        c.put(f"/api/me/devices/{dev}/conversation-search", json={"wearer": "self", "enabled": True})
        c.app.state.hub.connections.pop(dev, None)
        assert conn.rag.on
    finally:
        c.__exit__(None, None, None)


def test_a_watch_removed_from_the_account_leaves_nothing():
    c, me = _customer()
    try:
        acc = me["id"]
        dev = _give_watch(acc, with_history=False)
        c.put(f"/api/me/devices/{dev}/conversation-search", json={"wearer": "self", "enabled": True})
        _turn(acc, dev)
        _index()
        assert c.delete(f"/api/me/devices/{dev}").status_code == 200
        assert _vectors(dev) == set()
        with session_scope() as db:
            assert db.get(ConversationRagAuthorization, dev) is None
    finally:
        c.__exit__(None, None, None)


# --- races that matter --------------------------------------------------------------------------------------------


def test_a_watch_reassigned_during_the_search_returns_nothing(monkeypatch):
    acc, dev = _watch()
    _turn(acc, dev)
    gen = _generation(acc, dev)
    other = _account()

    def move():
        with session_scope() as db:
            DeviceRepo(db).assign(dev, other)

    monkeypatch.setattr("app.conversation_rag.embed.provider", lambda: _During(move))
    assert asyncio.run(rag_search.search(acc, dev, gen, "Crete")).status == "not_allowed"


def test_history_deleted_during_the_search_returns_nothing(monkeypatch):
    acc, dev = _watch()
    _turn(acc, dev)
    gen = _generation(acc, dev)

    def delete():
        with session_scope() as db:
            ConversationRepo(db).delete_device_history(dev)

    monkeypatch.setattr("app.conversation_rag.embed.provider", lambda: _During(delete))
    assert asyncio.run(rag_search.search(acc, dev, gen, "Crete")).status == "not_found"


def test_consent_withdrawn_during_the_search_returns_nothing(monkeypatch):
    acc, dev = _watch()
    _turn(acc, dev)
    gen = _generation(acc, dev)

    def off():
        with session_scope() as db:
            authz.set_choice(db, acc, dev, "self", False)
            db.commit()

    monkeypatch.setattr("app.conversation_rag.embed.provider", lambda: _During(off))
    assert asyncio.run(rag_search.search(acc, dev, gen, "Crete")).status == "not_allowed"


def test_vectors_are_kept_by_the_kill_switch_and_only_by_it(rag_on):  # noqa: F811
    acc, dev = _watch()
    _turn(acc, dev)
    _index()
    rag_on.conversation_rag_enabled = False
    with session_scope() as db:
        assert not authz.access(db, acc, dev).on
        assert db.exec(select(ConversationRagEmbedding).where(ConversationRagEmbedding.device_id == dev)).all()
