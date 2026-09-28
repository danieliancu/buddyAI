"""Engine, sessions and migrations."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import SERVER_DIR, get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if url.startswith("sqlite"):
        # Enforce FKs and use WAL on SQLite only; nothing SQLite-specific leaks elsewhere.
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session


def session_scope() -> Session:
    """For non-request code: `with session_scope() as s: ...`"""
    return Session(get_engine())


def run_migrations() -> None:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    cfg.attributes["skip_logging"] = True  # keep the server's logging config
    command.upgrade(cfg, "head")
