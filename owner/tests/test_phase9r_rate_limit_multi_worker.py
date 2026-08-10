"""Phase 9R M7 -- proves login/MFA throttling (app/security/ratelimit.py)
is already multi-worker-safe, not process-local. Phase 5's own threat
model (#14) called this a "single-process" limitation "acceptable... would
need Redis at real scale" -- true when written, stale now: the mechanism
is backed by the persistent owner_login_attempts table, the same real
PostgreSQL every Gunicorn worker connects to. Simulates two independent
worker processes as two independent SQLAlchemy engines/sessions against
the same database, rather than sharing the app's own scoped db_session --
a real second connection, not just a second call on the same one."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.staff import LoginAttempt


def _independent_session(app):
    """A genuinely separate engine + session, standing in for a second
    Gunicorn worker process's own connection to the same database."""
    engine = create_engine(app.config["SQLALCHEMY_DATABASE_URI"], future=True)
    return sessionmaker(bind=engine, future=True)()


class TestLoginThrottleIsSharedAcrossWorkers:
    def test_failure_recorded_by_worker_a_is_visible_to_worker_b(self, app):
        from app.security.ratelimit import is_locked_out, record_attempt

        email = "multiworker-a@example.com"
        max_attempts, window = 3, 300

        # "Worker A" (the app's own db_session) records 3 failures.
        with app.app_context():
            for _ in range(max_attempts):
                record_attempt(email, "10.0.0.1", success=False, reason="bad_password")

        # "Worker B" -- a totally independent connection -- must see the
        # same lockout state, proving the store is shared, not per-process.
        worker_b = _independent_session(app)
        try:
            count = (
                worker_b.query(LoginAttempt)
                .filter(LoginAttempt.email_attempted == email, LoginAttempt.success.is_(False))
                .count()
            )
            assert count == max_attempts
        finally:
            worker_b.close()

        with app.app_context():
            assert is_locked_out(email, "10.0.0.1", max_attempts, window) is True

    def test_lockout_recorded_by_one_connection_blocks_login_checked_from_another(self, app):
        from app.security.ratelimit import is_locked_out, record_attempt

        email = "multiworker-b@example.com"
        max_attempts, window = 2, 300

        engine = create_engine(app.config["SQLALCHEMY_DATABASE_URI"], future=True)
        WorkerSession = sessionmaker(bind=engine, future=True)
        worker_1 = WorkerSession()
        try:
            for _ in range(max_attempts):
                worker_1.add(LoginAttempt(email_attempted=email, ip_address="10.0.0.2", success=False, reason="bad_password"))
            worker_1.commit()
        finally:
            worker_1.close()

        # A completely different "worker" (the app's own session, never
        # having called record_attempt itself for this email) must still
        # correctly see the lockout -- proving the check reads real shared
        # state, not anything cached on the writer's own connection/session.
        with app.app_context():
            assert is_locked_out(email, "10.0.0.2", max_attempts, window) is True
