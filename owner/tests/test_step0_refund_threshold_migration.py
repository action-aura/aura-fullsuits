"""Migration f28dfb3efc40 (Step 0, 2026-10-25: the refund threshold and the
Super Admin's late-refund override) on its OWN scratch database, upgraded
from f16f0245da18 with a customer, a paid refund and a counted ladder sale
already in it.

Proves: the existing rows survive with every new column NULL; a customer's
threshold is 0..100 or NULL; a refund's decision is both-or-neither and its
override all-three-or-none with a reason of at least 10 characters (each
deny spelled to catch a CHECK that evaluates to NULL); a ladder sale may be
uncounted by REFUND_LATE_OVERRIDE and still not by an unknown cause; the
downgrade refuses while any row uses the new columns and otherwise restores
the old schema exactly."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "f16f0245da18"
NEW_REV = "f28dfb3efc40"
SCRATCH = "aura_owner_step0_refund_threshold_scratch"
CUSTOMERS = "owner_customers"
REFUNDS = "owner_commercial_refunds"
LADDER = "owner_commission_ladder_sales"
NEW_REFUND_COLUMNS = {"threshold_percent_applied", "package_refund_over_threshold", "window_override_reason",
                      "window_override_by_staff_user_id", "window_override_at"}


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _refused(engine, sql, params):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(sql), params)


def _ok(engine, sql, params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def test_refund_threshold_schema_upgrade_existing_rows_guards_and_downgrade():
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = TEST_DB_URL.rsplit("/", 1)[0]
    scratch_url, admin_url = f"{base}/{SCRATCH}", f"{base}/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH} OWNER aura_owner"))
    admin.dispose()
    try:
        assert _alembic(owner_dir, scratch_url, "upgrade", PREV).returncode == 0
        engine = create_engine(scratch_url)
        cache: dict = {}
        with engine.begin() as conn:
            customer_id = _seed(conn, CUSTOMERS, cache)
            refund_id = _seed(conn, REFUNDS, cache, {"status": "PAID", "amount": 10, "currency": "JOD",
                                                     "reason": "before the rule"})
            sale_id = _seed(conn, LADDER, cache, {"status": "COUNTED", "currency": "JOD"})
            staff_id = cache.get("owner_staff_users") or _seed(conn, "owner_staff_users", cache)
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        assert "refund_licence_stop_threshold_percent" in _columns(engine, CUSTOMERS)
        assert NEW_REFUND_COLUMNS <= _columns(engine, REFUNDS)
        with engine.begin() as conn:
            assert conn.execute(text(f"SELECT refund_licence_stop_threshold_percent FROM {CUSTOMERS} WHERE id = :i"),
                                {"i": customer_id}).scalar_one() is None
            row = conn.execute(text(f"SELECT {', '.join(sorted(NEW_REFUND_COLUMNS))}, status, amount FROM {REFUNDS} "
                                    "WHERE id = :i"), {"i": refund_id}).one()
            assert tuple(row)[:5] == (None,) * 5 and row.status == "PAID"

        upd_customer = f"UPDATE {CUSTOMERS} SET refund_licence_stop_threshold_percent = :p WHERE id = :i"
        for bad in (-1, 101):
            _refused(engine, upd_customer, {"p": bad, "i": customer_id})
        for good in (0, 100, None, 20):
            _ok(engine, upd_customer, {"p": good, "i": customer_id})

        decision = f"UPDATE {REFUNDS} SET threshold_percent_applied = :p, package_refund_over_threshold = :o WHERE id = :i"
        for p, o in ((50, None), (None, True), (101, True), (-1, False)):
            _refused(engine, decision, {"p": p, "o": o, "i": refund_id})
        _ok(engine, decision, {"p": 50, "o": False, "i": refund_id})

        override = (f"UPDATE {REFUNDS} SET window_override_reason = :r, window_override_by_staff_user_id = :s, "
                    "window_override_at = :t WHERE id = :i")
        when = "2026-10-25T12:00:00+00:00"
        for r, s, t in (("A written reason here", None, when), (None, staff_id, when), ("A written reason here", staff_id, None),
                        ("too short", staff_id, when), ("          x", staff_id, when)):
            _refused(engine, override, {"r": r, "s": s, "t": t, "i": refund_id})
        _ok(engine, override, {"r": "A written reason here", "s": staff_id, "t": when, "i": refund_id})

        uncount = (f"UPDATE {LADDER} SET status = 'UNCOUNTED', uncounted_at = now(), uncount_cause = :c "
                   "WHERE id = :i")
        _refused(engine, uncount, {"c": "SOMETHING_ELSE", "i": sale_id})
        _ok(engine, uncount, {"c": "REFUND_LATE_OVERRIDE", "i": sale_id})
        engine.dispose()

        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "Refusing to downgrade f28dfb3efc40" in refused.stderr
        for what in ("customer refund thresholds", "refund threshold decisions", "late-refund overrides",
                     "ladder sales uncounted by a late refund"):
            assert what in refused.stderr, what
        engine = create_engine(scratch_url)
        _ok(engine, f"UPDATE {CUSTOMERS} SET refund_licence_stop_threshold_percent = NULL WHERE id = :i", {"i": customer_id})
        _ok(engine, f"UPDATE {REFUNDS} SET threshold_percent_applied = NULL, package_refund_over_threshold = NULL, "
                    "window_override_reason = NULL, window_override_by_staff_user_id = NULL, window_override_at = NULL "
                    "WHERE id = :i", {"i": refund_id})
        _ok(engine, f"UPDATE {LADDER} SET uncount_cause = 'CORRECTION' WHERE id = :i", {"i": sale_id})
        engine.dispose()

        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert "refund_licence_stop_threshold_percent" not in _columns(engine, CUSTOMERS)
        assert not (NEW_REFUND_COLUMNS & _columns(engine, REFUNDS))
        _refused(engine, uncount, {"c": "REFUND_LATE_OVERRIDE", "i": sale_id})  # the old CHECK is back
        with engine.begin() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {REFUNDS}")).scalar_one() == 1
            assert conn.execute(text(f"SELECT uncount_cause FROM {LADDER} WHERE id = :i"),
                                {"i": sale_id}).scalar_one() == "CORRECTION"
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
