"""Migration f16f0245da18 (Step 0 fix round: a pair refund removes its
devices 2 days later) on its OWN scratch database, upgraded from
38a13f8b2f8e with a scheduled stop already in it.

Proves: the existing stop survives exactly as it was; a row is a stop
(SUSPENDED, no devices) or a device removal (no status, devices > 0) and
nothing else -- both, neither and a zero removal are refused; the downgrade
refuses while a device removal exists and otherwise restores the old NOT NULL
and CHECK."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "38a13f8b2f8e"
NEW_REV = "f16f0245da18"
SCRATCH = "aura_owner_step0_pair_refund_scratch"
STOPS = "owner_license_scheduled_stops"


def test_pair_refund_schema_upgrade_existing_rows_guards_and_downgrade():
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
            stop_id = _seed(conn, STOPS, cache, {"to_status": "SUSPENDED", "reason": "refund"})
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        columns = {c["name"]: c for c in inspect(engine).get_columns(STOPS)}
        assert columns["devices_removed"]["nullable"] and columns["to_status"]["nullable"]
        with engine.begin() as conn:
            row = conn.execute(text(f"SELECT to_status, devices_removed FROM {STOPS} WHERE id = :i"), {"i": stop_id}).one()
            assert tuple(row) == ("SUSPENDED", None)
        # deny: both, neither, a zero removal, an unknown status
        for assignment in ("devices_removed = 2", "to_status = NULL", "to_status = NULL, devices_removed = 0",
                           "to_status = 'REVOKED'"):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(f"UPDATE {STOPS} SET {assignment} WHERE id = :i"), {"i": stop_id})
        # allow: a device removal
        with engine.begin() as conn:
            conn.execute(text(f"UPDATE {STOPS} SET to_status = NULL, devices_removed = 2 WHERE id = :i"), {"i": stop_id})
        engine.dispose()

        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "Refusing to downgrade f16f0245da18" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:  # put the row back to what it was
            conn.execute(text(f"UPDATE {STOPS} SET to_status = 'SUSPENDED', devices_removed = NULL WHERE id = :i"),
                         {"i": stop_id})
        engine.dispose()

        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        columns = {c["name"]: c for c in inspect(engine).get_columns(STOPS)}
        assert "devices_removed" not in columns and columns["to_status"]["nullable"] is False
        with pytest.raises(IntegrityError):  # the old CHECK is back
            with engine.begin() as conn:
                conn.execute(text(f"UPDATE {STOPS} SET to_status = 'REVOKED' WHERE id = :i"), {"i": stop_id})
        with engine.begin() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {STOPS}")).scalar_one() == 1
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
