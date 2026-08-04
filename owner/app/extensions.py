"""Shared extension singletons, instantiated once, bound to the app in create_app().

db_session's OBJECT IDENTITY never changes after import -- init_db() only
(re)configures its bind. This matters because every domain module does
`from app.extensions import db_session` at import time; if init_db() replaced
the module-level name instead of reconfiguring the existing object, every
already-imported reference would keep pointing at the old (unbound) one.
"""
from __future__ import annotations

from flask_babel import Babel
from flask_wtf import CSRFProtect
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


csrf = CSRFProtect()
babel = Babel()

_session_factory = sessionmaker(autoflush=False, autocommit=False, future=True)
db_session = scoped_session(_session_factory)
Base.query = db_session.query_property()

_engine = None


def init_db(
    database_uri: str,
    *,
    statement_timeout_ms: int = 30000,
    lock_timeout_ms: int = 10000,
    idle_in_transaction_timeout_ms: int = 120000,
    pool_size: int = 5,
    max_overflow: int = 10,
):
    global _engine
    # Phase 9R M4: server-side timeouts set via libpq connection options
    # (-c GUC=value at connection startup) rather than an ALTER ROLE against
    # the database -- applies regardless of which role/host the connection
    # string points at, and needs no special database privilege to set.
    # SQLite (used by some standalone tooling, never by this Flask app) has
    # no such option string, so this only applies to postgresql:// URLs.
    connect_args = {}
    if database_uri.startswith("postgresql"):
        connect_args["options"] = (
            f"-c statement_timeout={statement_timeout_ms} "
            f"-c lock_timeout={lock_timeout_ms} "
            f"-c idle_in_transaction_session_timeout={idle_in_transaction_timeout_ms}"
        )
    _engine = create_engine(
        database_uri,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        connect_args=connect_args,
        future=True,
    )
    _session_factory.configure(bind=_engine)
    return _engine


def get_engine():
    if _engine is None:
        raise RuntimeError("Database engine not initialized -- call init_db() first")
    return _engine
