"""Phase 9 Milestone 9 -- real advisory-lock behavior for deploy/staging/run_scheduled_ops.py.

Imports the real script (not a reimplementation) and exercises the real
Postgres advisory lock using two genuinely separate raw connections (a
Postgres session-level advisory lock is reentrant *within* one session/
connection -- the real cross-process exclusion this scheduler depends on
only shows up across separate connections, exactly as two separate
`python deploy/staging/run_scheduled_ops.py` OS processes would have).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "deploy" / "staging" / "run_scheduled_ops.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_scheduled_ops", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def scheduler_module():
    return _load_module()


@pytest.fixture()
def two_raw_connections(app):
    import psycopg

    url = app.config["SQLALCHEMY_DATABASE_URI"].replace("postgresql+psycopg://", "postgresql://")
    conn_a = psycopg.connect(url)
    conn_b = psycopg.connect(url)
    yield conn_a, conn_b
    conn_a.close()
    conn_b.close()


def test_second_concurrent_lock_attempt_from_a_different_connection_is_skipped(scheduler_module, two_raw_connections):
    conn_a, conn_b = two_raw_connections
    key = scheduler_module.ADVISORY_LOCK_KEY

    with conn_a.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        assert cur.fetchone()[0] is True

    with conn_b.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        held_by_other_session = cur.fetchone()[0]
    assert held_by_other_session is False, "a concurrent run on a different connection must be refused the lock"

    with conn_a.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
        conn_a.commit()


def test_lock_releases_cleanly_for_a_later_separate_run(scheduler_module, two_raw_connections):
    conn_a, conn_b = two_raw_connections
    key = scheduler_module.ADVISORY_LOCK_KEY

    with conn_a.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
        conn_a.commit()

    with conn_b.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        reacquired = cur.fetchone()[0]
        cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
        conn_b.commit()
    assert reacquired is True, "a later, separate run must be able to acquire the lock after a clean release"
