"""Shared extension singletons, instantiated once, bound to the app in create_app().

db_session's OBJECT IDENTITY never changes after import -- init_db() only
(re)configures its bind. This matters because every domain module does
`from app.extensions import db_session` at import time; if init_db() replaced
the module-level name instead of reconfiguring the existing object, every
already-imported reference would keep pointing at the old (unbound) one.
"""
from __future__ import annotations

from flask_wtf import CSRFProtect
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


csrf = CSRFProtect()

_session_factory = sessionmaker(autoflush=False, autocommit=False, future=True)
db_session = scoped_session(_session_factory)
Base.query = db_session.query_property()

_engine = None


def init_db(database_uri: str):
    global _engine
    _engine = create_engine(database_uri, pool_pre_ping=True, future=True)
    _session_factory.configure(bind=_engine)
    return _engine


def get_engine():
    if _engine is None:
        raise RuntimeError("Database engine not initialized -- call init_db() first")
    return _engine
