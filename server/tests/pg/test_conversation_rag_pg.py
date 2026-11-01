"""Conversation RAG schema (migration 0033) on a real database: additive upgrade and downgrade with existing
conversations, ON DELETE CASCADE enforced by the database, the vector size check and a pgvector `<=>` search
that never crosses accounts or watches.

PostgreSQL needs pgvector: build deploy/postgres.Dockerfile (docs/CONVERSATION_RAG.md, "Running the PostgreSQL
tests") and set BUDDYAI_PG_TEST_URL. Without it only the SQLite case runs."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session

from app.db.models import Account, Conversation, ConversationRagEmbedding, ConversationRagSource, Device, Turn
from tests.pg.test_migration import _cfg, _urls

RAG_TABLES = {"conversation_rag_authorizations", "conversation_rag_embeddings", "conversation_rag_sources"}


def _fresh(url: str):
    engine = create_engine(url)
    if url.startswith("postgresql"):
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    else:  # foreign keys are off by default on a raw SQLite connection; the application switches them on
        from sqlalchemy import event

        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    return engine


def _world(db: Session) -> dict:
    """Two accounts; account A has two watches. One turn per watch."""
    ids: dict = {}
    for name in ("a", "b"):
        acc = Account(email=f"{name}@example.com")
        db.add(acc)
        db.flush()
        ids[name] = acc.id
    for dev, owner in (("dev-a1", "a"), ("dev-a2", "a"), ("dev-b1", "b")):
        db.add(Device(id=dev, account_id=ids[owner]))
        db.flush()
        conv = Conversation(device_id=dev)
        db.add(conv)
        db.flush()
        t = Turn(device_id=dev, account_id=ids[owner], conversation_id=conv.id, session_id="s", turn_no=1,
                 language="en", status="completed", user_text=f"holiday in Crete ({dev})", assistant_text="ok")
        db.add(t)
        db.flush()
        ids[dev] = t.id
    db.commit()
    return ids


def _vec(db: Session, ids: dict, dev: str, owner: str, vec: list[float]) -> None:
    db.add(ConversationRagEmbedding(turn_id=ids[dev], account_id=ids[owner], device_id=dev, model_key="k:m:3",
                                    dims=3, text_hash="h", embedding=vec))
    db.commit()


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_0033_is_additive_and_downgrades_keeping_conversations(url):
    engine = _fresh(url)
    cfg = _cfg(url)
    command.upgrade(cfg, "head")
    with Session(engine) as db:
        ids = _world(db)
    command.downgrade(cfg, "0032")
    with engine.begin() as c:
        assert not RAG_TABLES & set(inspect(c).get_table_names())
        assert not {"rag_derived", "rag_redacted_at"} & {col["name"] for col in inspect(c).get_columns("turns")}
        assert c.execute(text("SELECT count(*) FROM turns")).scalar() == 3
    command.upgrade(cfg, "0033")
    with engine.begin() as c:
        rows = c.execute(text("SELECT rag_derived, rag_redacted_at FROM turns WHERE id = :i"),
                         {"i": ids["dev-a1"]}).one()
        assert rows[0] in (False, 0) and rows[1] is None
    engine.dispose()


@pytest.mark.parametrize("url", _urls(), ids=lambda u: u.split(":")[0])
def test_0033_cascades_are_enforced_by_the_database(url):
    engine = _fresh(url)
    command.upgrade(_cfg(url), "head")
    with Session(engine) as db:
        ids = _world(db)
        _vec(db, ids, "dev-a1", "a", [1.0, 0.0, 0.0])
        db.add(ConversationRagSource(consumer_turn_id=ids["dev-a2"], source_turn_id=ids["dev-a1"]))
        db.commit()
    with engine.begin() as c:  # raw SQL: no application code involved
        c.execute(text("DELETE FROM turns WHERE id = :i"), {"i": ids["dev-a1"]})
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM conversation_rag_embeddings")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM conversation_rag_sources")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM turns WHERE id = :i"), {"i": ids["dev-a2"]}).scalar() == 1
    with Session(engine) as db, pytest.raises(IntegrityError):  # a vector for a deleted turn is refused
        _vec(db, ids, "dev-a1", "a", [1.0, 0.0, 0.0])
    engine.dispose()


@pytest.mark.parametrize("url", [u for u in _urls() if u.startswith("postgresql")] or
                         [pytest.param("", marks=pytest.mark.skip(reason="BUDDYAI_PG_TEST_URL not set"))],
                         ids=lambda u: u.split(":")[0] or "pg")
def test_0033_pgvector_search_stays_inside_one_account_and_watch(url):
    engine = _fresh(url)
    command.upgrade(_cfg(url), "head")
    with Session(engine) as db:
        ids = _world(db)
        _vec(db, ids, "dev-a1", "a", [0.9, 0.1, 0.0])
        _vec(db, ids, "dev-a2", "a", [1.0, 0.0, 0.0])  # closest to the query, but another watch
        _vec(db, ids, "dev-b1", "b", [1.0, 0.0, 0.0])  # closest too, another account
    with engine.begin() as c:
        rows = c.execute(text(
            "SELECT e.turn_id, e.embedding <=> CAST(:q AS vector) AS d FROM conversation_rag_embeddings e "
            "JOIN turns t ON t.id = e.turn_id "
            "WHERE e.account_id = :acc AND e.device_id = :dev AND e.model_key = 'k:m:3' "
            "AND t.account_id = :acc AND t.device_id = :dev ORDER BY d LIMIT 5"),
            {"q": "[1,0,0]", "acc": ids["a"], "dev": "dev-a1"}).all()
        assert [r[0] for r in rows] == [ids["dev-a1"]]
        assert 0 < rows[0][1] < 0.1
    with Session(engine) as db, pytest.raises(DBAPIError):  # the stored size must match the vector
        db.add(ConversationRagEmbedding(turn_id=ids["dev-a1"], account_id=ids["a"], device_id="dev-a1",
                                        model_key="k:m:4", dims=4, text_hash="h", embedding=[1.0, 0.0, 0.0]))
        db.commit()
    engine.dispose()
