"""Migration c7d41e90b2a5 (Step 1: clawback deductions) on its OWN scratch
database, upgraded from a6e81bf3d32c.

Proves: the table appears and nothing existing is altered (a ledger row and a
pay-out line written before survive); the CHECKs refuse what the design forbids
(a HAND row without a reason or with a line, a PAYOUT row without its line and
batch, amount <= 0, an unknown kind) and accept the two valid shapes; the
downgrade REFUSES while any row exists and, once it is empty, removes exactly
the table."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "a6e81bf3d32c"
NEW_REV = "c7d41e90b2a5"
SCRATCH = "aura_owner_step1_clawback_scratch"
TABLE = "owner_commission_clawback_deductions"


def _insert(conn, debt, profile, staff, **over):
    values = {"id": str(uuid.uuid4()), "debt_ledger_entry_id": debt, "employee_profile_id": profile, "amount": "1.00",
              "currency": "JOD", "kind": "HAND", "reason": "paid in cash", "created_by_staff_user_id": staff, **over}
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO {TABLE} ({cols}) VALUES ({params})"), values)
    return values["id"]


def test_clawback_table_upgrade_checks_and_downgrade_refusal():
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
        with engine.begin() as conn:  # a pre-existing PAID entry with its pay-out line
            sale = _seed(conn, "owner_commission_ladder_sales", cache)
            entry = _seed(conn, "owner_commission_ledger_entries", cache,
                          {"status": "PAID", "commission_amount": 10, "entry_kind": "LADDER", "ladder_sale_id": sale,
                           "ladder_month": "2026-04-01"})
            profile = cache["owner_employee_profiles"]
            staff = cache["owner_staff_users"] if "owner_staff_users" in cache else _seed(conn, "owner_staff_users", cache)
            line = _seed(conn, "owner_commission_payout_lines", cache, {"commission_ledger_entry_id": entry})
            batch = cache["owner_commission_payout_batches"]
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        assert TABLE in set(inspect(engine).get_table_names())
        with engine.begin() as conn:  # allow: what existed before is untouched
            assert conn.execute(text("SELECT count(*) FROM owner_commission_ledger_entries")).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM owner_commission_payout_lines")).scalar_one() == 1

        # allow: the two valid shapes
        with engine.begin() as conn:
            _insert(conn, entry, profile, staff)  # HAND with a reason, no line, no batch
            _insert(conn, entry, profile, staff, kind="PAYOUT", reason=None, payout_line_id=line,
                    commission_payout_batch_id=batch)
        # deny: each way out of those shapes, and amounts that are not positive
        bad_rows = [
            {"reason": None}, {"reason": ""}, {"reason": "   "},                               # HAND needs a reason
            {"payout_line_id": line, "commission_payout_batch_id": batch},                    # HAND may not carry a line
            {"kind": "PAYOUT", "reason": None},                                              # PAYOUT needs line + batch
            {"kind": "PAYOUT", "reason": None, "payout_line_id": line},                       # ... both
            {"amount": "0"}, {"amount": "-1.00"}, {"kind": "OTHER"},
        ]
        for over in bad_rows:
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    _insert(conn, entry, profile, staff, **over)
        engine.dispose()

        # deny: the downgrade would make withheld money look unpaid
        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "Refusing to downgrade c7d41e90b2a5" in refused.stderr
        engine = create_engine(scratch_url)
        assert TABLE in set(inspect(engine).get_table_names())  # nothing dropped
        with engine.begin() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one() == 2
            conn.execute(text(f"DELETE FROM {TABLE}"))
        engine.dispose()

        # allow: with nothing to lose it removes exactly the table
        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert TABLE not in set(inspect(engine).get_table_names())
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM owner_commission_ledger_entries")).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM owner_commission_payout_lines")).scalar_one() == 1
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
