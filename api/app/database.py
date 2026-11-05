"""SQLAlchemy engine and session helpers. Works with PostgreSQL and SQLite."""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from .config import DATABASE_URL


def _normalize_url(url: str) -> str:
    # SQLAlchemy 2 defaults postgresql:// to psycopg v3. This project installs psycopg2.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://") :]
    return url


def _build_engine(url: str):
    url = _normalize_url(url)
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _build_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401

    models.Base.metadata.create_all(bind=engine)


def database_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_engine(url: str | None = None) -> None:
    """Test helper: point the process at another database URL."""
    global engine, SessionLocal
    engine.dispose()
    engine = _build_engine(url or os.getenv("DATABASE_URL", DATABASE_URL))
    SessionLocal.configure(bind=engine)
