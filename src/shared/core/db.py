"""SQLAlchemy engine / session helpers (table-free).

``make_engine`` builds the WAL-enabled engine and ``session_scope`` manages a session's lifecycle. Neither
knows about tables; creating or migrating a schema is the consuming project's job.

Every process that talks to the database should build exactly one engine per
database URL — with SQLite WAL mode enabled and the schema migrated — and
share it, instead of each call site constructing its own ad hoc
``create_engine()``. Without WAL mode, SQLite readers on one connection
contend with the inspection loop's writer connection and intermittently raise
``database is locked`` under concurrent load.

Usage — construct once at module/process scope, not per request or per call::

    from sqlalchemy.orm import sessionmaker
    from shared.core.db import make_engine
    Session = sessionmaker(bind=make_engine(config.get_database_url()))
    ...
    with Session() as session:
        ...

For a single request/call's session lifecycle (open, roll back on error, always
close), use ``session_scope`` instead of hand-writing the same
try/except/finally around every call site::

    from shared.core.db import session_scope

    with session_scope(Session) as session:
        session.query(...)
"""

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Protocol, TypeVar

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class _SessionLike(Protocol):
    """Structural type for session_scope — anything with rollback()/close().

    Deliberately not sqlalchemy.orm.Session: most call sites' session_factory
    is typed Callable[[], Any] (route handlers accept any session factory),
    and binding this to the concrete Session type would force every plain
    ``Column`` attribute assignment at those call sites through strict
    Column[T]-vs-T mypy checks that the rest of the codebase doesn't opt
    into. A caller with a properly-typed sessionmaker[Session] still gets
    Session back from session_scope; a Callable[[], Any] caller gets Any,
    unchanged from before this helper existed.
    """

    def rollback(self) -> None: ...
    def close(self) -> None: ...


_S = TypeVar("_S", bound=_SessionLike)


_SQLITE_BUSY_TIMEOUT_MS = 30_000


def make_engine(db_url: str) -> Engine:
    """Create a SQLAlchemy engine with SQLite WAL mode and busy timeout. Does not touch the schema.

    Safe to call once per process per ``db_url``. Enables WAL mode (readers
    no longer block on the writer) and ``check_same_thread=False`` (needed
    because the returned engine is typically shared across a scheduler
    thread and request-handling threads) for SQLite URLs only; non-SQLite
    URLs (e.g. PostgreSQL) are left untouched.

    Also sets ``PRAGMA busy_timeout`` to 30s (Python's sqlite3 module already
    defaults to a 5s busy timeout, but that's not always enough headroom for
    a slow writer that holds a write transaction open across a long-running
    call; this is defense in depth, not a replacement for keeping write
    transactions short in the first place).
    """
    is_sqlite = db_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(db_url, connect_args=connect_args)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn: Any, _: Any) -> None:
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
            # D-SH4 (approved 2026-10-14): enabled after the dangling-row pre-check ran clean
            # against a copy of the real deployed DB on 2026-10-31.
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


@contextmanager
def session_scope(session_factory: Callable[[], _S]) -> Iterator[_S]:
    """Open a session; roll back on exception; always close.

    Centralizes the ``session = session_factory(); try: ...; finally:
    session.close()`` boilerplate that was hand-copied across dozens of route
    handlers, several of which rolled back on error only inconsistently.
    Does not commit — callers still call ``session.commit()`` explicitly
    where a write should be persisted, same as before. An exception raised
    inside the ``with`` block triggers ``session.rollback()`` and then
    propagates unchanged, so a caller's own exception handling (e.g. mapping
    to an HTTP error response) still runs exactly as it did before.

    Usage::

        with session_scope(session_factory) as session:
            session.add(obj)
            session.commit()
    """
    session = session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
