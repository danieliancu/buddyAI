"""Engine, sessions and migrations."""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from functools import lru_cache

from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import SERVER_DIR, get_settings

# This process, as the owner of the AI operations it executes (usage_operations.owner).
OWNER = f"{socket.gethostname()[:40]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _sqlite_pragmas(dbapi_conn) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")  # wait for a writer instead of failing at once
    cur.close()


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if _is_sqlite(url):
        engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record):  # pragma: no cover - trivial
            _sqlite_pragmas(dbapi_conn)

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10,
                         connect_args={"application_name": f"buddyai:{OWNER}"[:63], "connect_timeout": 5})


@lru_cache
def get_billing_engine() -> Engine:
    """Engine for the usage-admission transactions (app/usage_ops.py).

    PostgreSQL: the main engine; transactions lock the account row with SELECT ... FOR UPDATE.
    SQLite (development / tests): a second engine on the same file whose transactions start with
    BEGIN IMMEDIATE - the database write lock serialises admissions across processes. Nothing pretends that
    SQLite has row locks."""
    url = get_settings().database_url
    if not _is_sqlite(url):
        return get_engine()
    engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - trivial
        dbapi_conn.isolation_level = None  # we issue BEGIN ourselves
        _sqlite_pragmas(dbapi_conn)

    @event.listens_for(engine, "begin")
    def _on_begin(conn):  # pragma: no cover - trivial
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def is_postgres() -> bool:
    return not _is_sqlite(get_settings().database_url)


def db_now(db: Session) -> datetime:
    """The database's clock (PostgreSQL) - one time source for every process. SQLite: this host's clock."""
    if is_postgres():
        value = db.exec(text("SELECT now()")).one()[0]  # type: ignore[call-overload]
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session


def session_scope() -> Session:
    """For non-request code: `with session_scope() as s: ...`"""
    return Session(get_engine())


def billing_scope() -> Session:
    """A session on the billing engine (see get_billing_engine)."""
    return Session(get_billing_engine())


def run_migrations() -> None:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    cfg.attributes["skip_logging"] = True  # keep the server's logging config
    command.upgrade(cfg, "head")
