"""Phase 9R M4 -- PostgreSQL connection hardening: statement/lock/idle-in-
transaction timeouts and bounded connection pool, applied via libpq connect
options rather than an ALTER ROLE (portable across whatever role/host the
connection string points at)."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.extensions import db_session, get_engine, init_db


def _show(setting: str) -> str:
    return db_session.execute(text(f"SHOW {setting}")).scalar()


class TestConnectionLevelTimeouts:
    def test_app_factory_applies_configured_timeouts(self, app):
        # `app` fixture already boots via create_app("testing"), which wires
        # config.py's DB_* values through app/__init__.py into init_db().
        assert _show("statement_timeout") == "30s"
        assert _show("lock_timeout") == "10s"
        assert _show("idle_in_transaction_session_timeout") == "2min"

    def test_custom_timeouts_are_honored(self, app):
        # Reconfigure with different values -- proves the values are read
        # from the caller, not hardcoded inside init_db().
        init_db(
            app.config["SQLALCHEMY_DATABASE_URI"],
            statement_timeout_ms=5000,
            lock_timeout_ms=2000,
            idle_in_transaction_timeout_ms=15000,
            pool_size=app.config["DB_POOL_SIZE"],
            max_overflow=app.config["DB_MAX_OVERFLOW"],
        )
        try:
            assert _show("statement_timeout") == "5s"
            assert _show("lock_timeout") == "2s"
            assert _show("idle_in_transaction_session_timeout") == "15s"
        finally:
            # Restore the app's real configured values so later tests in
            # this session aren't left on the temporary ones.
            init_db(
                app.config["SQLALCHEMY_DATABASE_URI"],
                statement_timeout_ms=app.config["DB_STATEMENT_TIMEOUT_MS"],
                lock_timeout_ms=app.config["DB_LOCK_TIMEOUT_MS"],
                idle_in_transaction_timeout_ms=app.config["DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"],
                pool_size=app.config["DB_POOL_SIZE"],
                max_overflow=app.config["DB_MAX_OVERFLOW"],
            )

    def test_statement_timeout_actually_terminates_a_runaway_query(self, app):
        init_db(
            app.config["SQLALCHEMY_DATABASE_URI"],
            statement_timeout_ms=200,
            lock_timeout_ms=app.config["DB_LOCK_TIMEOUT_MS"],
            idle_in_transaction_timeout_ms=app.config["DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"],
            pool_size=app.config["DB_POOL_SIZE"],
            max_overflow=app.config["DB_MAX_OVERFLOW"],
        )
        try:
            with pytest.raises(OperationalError, match="statement timeout"):
                db_session.execute(text("SELECT pg_sleep(2)"))
            db_session.rollback()
        finally:
            init_db(
                app.config["SQLALCHEMY_DATABASE_URI"],
                statement_timeout_ms=app.config["DB_STATEMENT_TIMEOUT_MS"],
                lock_timeout_ms=app.config["DB_LOCK_TIMEOUT_MS"],
                idle_in_transaction_timeout_ms=app.config["DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"],
                pool_size=app.config["DB_POOL_SIZE"],
                max_overflow=app.config["DB_MAX_OVERFLOW"],
            )


class TestBoundedConnectionPool:
    def test_pool_size_and_overflow_match_configuration(self, app):
        engine = get_engine()
        assert engine.pool.size() == app.config["DB_POOL_SIZE"]
        assert engine.pool._max_overflow == app.config["DB_MAX_OVERFLOW"]


class TestApplicationRoleIsNotPrivileged:
    def test_application_role_is_not_superuser(self, app):
        row = db_session.execute(
            text("SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert row.rolsuper is False, "application connection must never be a PostgreSQL superuser"
        assert row.rolcreaterole is False, "application role must never be able to create other roles"
