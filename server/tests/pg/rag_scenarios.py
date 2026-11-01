"""Conversation RAG scenarios on real PostgreSQL + pgvector, each run in its own process by
test_conversation_rag_search_pg.py (the application's engine reads its database URL from the environment).

    python -m tests.pg.rag_scenarios <scenario>
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import threading
import time
from datetime import timedelta


def _setup():
    from sqlalchemy import text

    from app.db.session import get_engine, run_migrations

    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    run_migrations()


def _account() -> int:
    from app.db.models import Account
    from app.db.session import session_scope

    with session_scope() as db:
        acc = Account(email=f"rag-{secrets.token_hex(4)}@example.com")
        db.add(acc)
        db.commit()
        db.refresh(acc)
        return acc.id


def _watch(acc: int, opt_in: bool = True) -> str:
    from app.conversation_rag import authz
    from app.db.repositories import DeviceRepo, SettingsRepo
    from app.db.session import session_scope
    from app.security import hash_device_token

    dev = f"dev-{secrets.token_hex(4)}"
    with session_scope() as db:
        DeviceRepo(db).pair(dev, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", acc)
        SettingsRepo(db).ensure(dev)
        if opt_in:
            authz.set_choice(db, acc, dev, "self", True)
            db.commit()
    return dev


def _turn(acc: int, dev: str, user: str = "We planned a holiday in Crete", reply: str = "Crete is lovely", **f) -> int:
    from app.db.repositories import ConversationRepo, TurnRepo
    from app.db.session import session_scope

    with session_scope() as db:
        conv = ConversationRepo(db).current(dev, 30)
        return TurnRepo(db).create(**{**dict(device_id=dev, account_id=acc, conversation_id=conv.id, session_id="s",
                                             turn_no=1, language="en", status="completed", user_text=user,
                                             assistant_text=reply), **f}).id


def _index(provider=None) -> int:
    from app.conversation_rag import index
    from app.conversation_rag.embed import provider as embed_provider

    provider = provider or embed_provider()
    total = 0
    for _ in range(20):
        n = asyncio.run(index.index_once(provider))
        total += n
        if n == 0:
            break
    return total


def _search(acc: int, dev: str, query: str, **kw):
    from app.conversation_rag import authz, search
    from app.db.session import session_scope

    with session_scope() as db:
        gen = authz.access(db, acc, dev).generation
    return asyncio.run(search.search(acc, dev, gen, query, **kw))


def _vectors(dev: str) -> int:
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as db:
        return db.execute(text("SELECT count(*) FROM conversation_rag_embeddings WHERE device_id = :d"), {"d": dev}).scalar()


def _others_off() -> None:
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as db:
        db.execute(text("UPDATE conversation_rag_authorizations SET state = 'off'"))
        db.commit()


def search_is_semantic_and_isolated() -> None:
    from app.db.models import utcnow

    _others_off()
    acc = _account()
    dev, sibling = _watch(acc), _watch(acc)
    other = _account()
    foreign = _watch(other)
    mine = _turn(acc, dev)
    _turn(acc, dev, "my car needs new brakes", "Book a garage")
    derived = _turn(acc, dev, "holiday Crete", "built from a search", rag_derived=True)
    aborted = _turn(acc, dev, "holiday Crete", "cut off", status="aborted")
    _turn(acc, sibling, "We planned a holiday in Crete", "sibling secret")
    _turn(other, foreign, "We planned a holiday in Crete", "foreign secret")
    assert _index() == 4, "mine x2, sibling, foreign"
    out = _search(acc, dev, "holiday Crete")
    ids = [h.turn_id for h in out.hits]
    assert out.status == "ok" and "vector" in out.path and ids[0] == mine, out
    assert out.hits[0].distance is not None and out.hits[0].distance <= 0.6
    assert derived not in ids and aborted not in ids and "secret" not in out.result("UTC"), out
    old = _turn(acc, dev, "holiday Crete in spring", "april", created_at=utcnow() - timedelta(days=60))
    _index()
    start, end = ((utcnow() - timedelta(days=d)).date().isoformat() for d in (70, 50))
    dated = _search(acc, dev, "holiday Crete", start=start, end=end, tz="Europe/Bucharest")
    assert [h.turn_id for h in dated.hits] == [old], dated


def vectors_of_another_model_are_ignored() -> None:
    from app.providers.mock import MockEmbeddings

    class Other(MockEmbeddings):
        model = "other"

    _others_off()
    acc = _account()
    dev = _watch(acc)
    _turn(acc, dev)
    assert _index(Other(64)) == 1
    out = _search(acc, dev, "holiday Crete")  # the current model has no vectors yet: words find it
    assert out.status == "ok" and out.hits[0].distance is None, out


def statement_timeout_is_local_to_the_search() -> None:
    from sqlalchemy import text

    from app.conversation_rag import search
    from app.db.session import session_scope
    from app.providers.mock import MockEmbeddings

    acc = _account()
    dev = _watch(acc)
    qvec = asyncio.run(MockEmbeddings(64).embed(["x"])).vectors[0]
    with session_scope() as db:
        search._vector_hits(db, acc, dev, "k", qvec, 5, None, None, None, 123)
        assert db.execute(text("SHOW statement_timeout")).scalar() == "123ms"
        db.rollback()
        assert db.execute(text("SHOW statement_timeout")).scalar() == "0"


def a_deletion_waits_for_a_running_store_and_removes_its_vectors() -> None:
    """The lock of index._store: a deletion cannot slip between the check and the commit."""
    from sqlmodel import col, select

    from app.db.models import ConversationRagEmbedding, Turn
    from app.db.repositories import ConversationRepo
    from app.db.session import session_scope

    acc = _account()
    dev = _watch(acc)
    tid = _turn(acc, dev)
    deleted = threading.Event()

    def delete():
        with session_scope() as db:
            ConversationRepo(db).delete_device_history(dev)
        deleted.set()

    with session_scope() as db:
        assert db.exec(select(Turn.id).where(col(Turn.id) == tid).with_for_update(read=True, key_share=True)).first()
        db.add(ConversationRagEmbedding(turn_id=tid, account_id=acc, device_id=dev, model_key="k", dims=3,
                                        text_hash="h", embedding=[1.0, 0.0, 0.0]))
        db.flush()
        t = threading.Thread(target=delete)
        t.start()
        time.sleep(1)
        assert not deleted.is_set(), "the deletion must wait for the store"
        db.commit()
    t.join(10)
    assert deleted.is_set() and _vectors(dev) == 0


def a_switch_off_waits_for_a_running_store_and_removes_its_vectors() -> None:
    from sqlmodel import select

    from app.conversation_rag import authz, index
    from app.db.models import ConversationRagAuthorization, ConversationRagEmbedding
    from app.db.session import session_scope

    acc = _account()
    dev = _watch(acc)
    tid = _turn(acc, dev)
    with session_scope() as db:
        generation = authz.access(db, acc, dev).generation
    off = threading.Event()

    def switch_off():
        with session_scope() as db:
            authz.set_choice(db, acc, dev, "self", False)
            db.commit()
        off.set()

    with session_scope() as db:
        db.exec(select(ConversationRagAuthorization).where(ConversationRagAuthorization.device_id == dev)
                .with_for_update()).first()
        db.add(ConversationRagEmbedding(turn_id=tid, account_id=acc, device_id=dev, model_key="k", dims=3,
                                        text_hash="h", embedding=[1.0, 0.0, 0.0]))
        db.flush()
        t = threading.Thread(target=switch_off)
        t.start()
        time.sleep(1)
        assert not off.is_set(), "the switch-off must wait for the store"
        db.commit()
    t.join(10)
    assert off.is_set() and _vectors(dev) == 0
    # and a batch embedded under the old generation is refused afterwards
    assert index._store(dev, acc, generation, "k", 3, [(tid, "t", [1.0, 0.0, 0.0])]) == 0 and _vectors(dev) == 0


def embedded_during_a_deletion_or_switch_off_stores_nothing() -> None:
    from app.conversation_rag import authz
    from app.db.repositories import ConversationRepo
    from app.db.session import session_scope
    from app.providers.mock import MockEmbeddings

    class During(MockEmbeddings):
        def __init__(self, then):
            super().__init__(64)
            self.then = then

        async def embed(self, texts):
            res = await super().embed(texts)
            self.then()
            return res

    _others_off()
    acc = _account()
    dev = _watch(acc)
    _turn(acc, dev)

    def delete():
        with session_scope() as db:
            ConversationRepo(db).delete_device_history(dev)

    assert _index(During(delete)) == 0 and _vectors(dev) == 0
    _turn(acc, dev)

    def off():
        with session_scope() as db:
            authz.set_choice(db, acc, dev, "self", False)
            db.commit()

    assert _index(During(off)) == 0 and _vectors(dev) == 0


def only_one_process_leases_a_watch() -> None:
    from app.conversation_rag import index

    _others_off()
    acc = _account()
    dev = _watch(acc)
    got: list = []
    barrier = threading.Barrier(6)

    def claim():
        barrier.wait()
        got.append(index._claim())

    threads = [threading.Thread(target=claim) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert [g[0] for g in got if g] == [dev], got


def references_follow_their_turns() -> None:
    from sqlalchemy import text

    from app.conversation_rag import context
    from app.db.session import session_scope

    acc = _account()
    dev = _watch(acc)
    a, b, consumer = _turn(acc, dev), _turn(acc, dev), _turn(acc, dev)
    with session_scope() as db:
        assert context.record_sources(db, consumer, acc, dev, [a, b]) is True
        db.commit()
        db.execute(text("DELETE FROM turns WHERE id = :i"), {"i": a})
        db.commit()
        assert db.execute(text("SELECT source_turn_id FROM conversation_rag_sources WHERE consumer_turn_id = :c"),
                          {"c": consumer}).scalars().all() == [b]
    other = _turn(acc, dev)
    other_acc = _account()
    foreign = _turn(other_acc, _watch(other_acc, opt_in=False))
    with session_scope() as db:  # a source gone (or another watch's) is reported, and not referenced
        assert context.record_sources(db, other, acc, dev, [b, a, foreign]) is False
        db.commit()
        assert db.execute(text("SELECT source_turn_id FROM conversation_rag_sources WHERE consumer_turn_id = :c"),
                          {"c": other}).scalars().all() == [b]


if __name__ == "__main__":
    name = sys.argv[1]
    if name == "setup":
        _setup()
    else:
        globals()[name]()
    print("ok")
