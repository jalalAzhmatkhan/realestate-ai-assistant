from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import Settings

# Registering the models package is what puts the tables on SQLModel.metadata;
# create_all walks metadata, not the filesystem.
import app.models  # noqa: F401


def build_engine(settings: Settings) -> Engine:
    """One engine per application instance, built from DATABASE_URL.

    Identical code path for SQLite and Postgres — the only difference is the two
    SQLite-specific accommodations below, both of which are no-ops elsewhere.
    """
    is_sqlite = settings.database_url.startswith("sqlite")
    engine = create_engine(
        settings.database_url,
        # FastAPI runs sync dependencies in a threadpool, so a connection is
        # handed between threads; SQLite refuses that by default.
        connect_args={"check_same_thread": False} if is_sqlite else {},
        pool_pre_ping=not is_sqlite,
    )
    if is_sqlite:
        _enforce_sqlite_foreign_keys(engine)
    return engine


def _enforce_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys unless asked per connection. Without this, dev on
    SQLite would silently accept referential errors that Postgres rejects in prod."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def get_session(request: Request) -> Generator[Session, None, None]:
    """Request-scoped session. The engine lives on app.state rather than in a module
    global so a test can build an app against its own database."""
    with Session(request.app.state.engine) as session:
        yield session
