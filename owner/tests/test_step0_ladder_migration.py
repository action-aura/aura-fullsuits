"""Migration 38a13f8b2f8e (Step 0: the commission ladder and the refund's
licence stop) on its OWN scratch database, upgraded from 1dfa1f6108b2 with a
commission row already in it.

Proves: the existing row survives as a PAYMENT_RULE row with nothing else
changed; a PAYMENT_RULE row still cannot lose its invoice or allocation (the
CHECK keeps the old NOT NULL guarantee for it) while a LADDER row may have
neither; a LADDER row must name its sale and month; the one-open-entry and
one-counted-sale indexes refuse a second row; the downgrade refuses while
ladder money exists and otherwise removes exactly what was added."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "1dfa1f6108b2"
NEW_REV = "38a13f8b2f8e"
SCRATCH = "aura_owner_step0_ladder_scratch"
LEDGER = "owner_commission_ledger_entries"


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_ladder_schema_upgrade_existing_rows_guards_and_downgrade():
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
            entry_id = _seed(conn, LEDGER, cache, {"commission_amount": 10, "status": "EARNED"})
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        assert {"entry_kind", "ladder_sale_id", "ladder_month", "ladder_position", "payable_after"} <= _columns(engine, LEDGER)
        tables = inspect(engine).get_table_names()
        assert {"owner_commission_ladder_sales", "owner_license_scheduled_stops"} <= set(tables)
        with engine.begin() as conn:
            row = conn.execute(text(f"SELECT entry_kind, ladder_sale_id, payable_after, commission_amount, "
                                    f"source_commercial_invoice_id IS NOT NULL, source_payment_allocation_id IS NOT NULL "
                                    f"FROM {LEDGER} WHERE id = :i"), {"i": entry_id}).one()
            assert tuple(row) == ("PAYMENT_RULE", None, None, 10, True, True)
        # deny: a PAYMENT_RULE row still needs its invoice and its allocation
        for column in ("source_commercial_invoice_id", "source_payment_allocation_id"):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(f"UPDATE {LEDGER} SET {column} = NULL WHERE id = :i"), {"i": entry_id})
        # deny: an unknown kind; a LADDER row without its sale/month
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(f"UPDATE {LEDGER} SET entry_kind = 'BONUS' WHERE id = :i"), {"i": entry_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(f"UPDATE {LEDGER} SET entry_kind = 'LADDER', source_payment_allocation_id = NULL "
                                  f"WHERE id = :i"), {"i": entry_id})
        # allow: a LADDER row with no allocation, naming its sale and month
        with engine.begin() as conn:
            sale_id = _seed(conn, "owner_commission_ladder_sales", cache,
                            {"status": "COUNTED", "ladder_month": "2026-10-01", "currency": "JOD"})
            conn.execute(text(f"UPDATE {LEDGER} SET entry_kind = 'LADDER', source_payment_allocation_id = NULL, "
                              f"ladder_sale_id = :s, ladder_month = '2026-10-01' WHERE id = :i"),
                         {"s": sale_id, "i": entry_id})
        # deny: a second open (EARNED) row for the same ladder sale
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(
                    f"INSERT INTO {LEDGER} (id, employee_profile_id, commission_rule_version_id, source_payment_record_id, "
                    f"base_amount, rate_or_fixed_applied, commission_amount, currency, status, entry_kind, ladder_sale_id, "
                    f"ladder_month) SELECT :n, employee_profile_id, commission_rule_version_id, source_payment_record_id, "
                    f"1, 1, 1, 'JOD', 'EARNED', 'LADDER', ladder_sale_id, ladder_month FROM {LEDGER} WHERE id = :i"),
                    {"n": str(uuid.uuid4()), "i": entry_id})
        # deny: a second COUNTED sale for the same order; an UNCOUNTED one without a cause
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO owner_commission_ladder_sales (id, employee_profile_id, sales_order_id, commercial_invoice_id, "
                    "package_plan_id, commission_rule_version_id, qualifying_payment_record_id, qualified_at, ladder_month, "
                    "hold_ends_at, currency, status) SELECT :n, employee_profile_id, sales_order_id, commercial_invoice_id, "
                    "package_plan_id, commission_rule_version_id, qualifying_payment_record_id, qualified_at, ladder_month, "
                    "hold_ends_at, currency, 'COUNTED' FROM owner_commission_ladder_sales WHERE id = :s"),
                    {"n": str(uuid.uuid4()), "s": sale_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE owner_commission_ladder_sales SET status = 'UNCOUNTED' WHERE id = :s"),
                             {"s": sale_id})
        engine.dispose()

        # deny: the downgrade would erase ladder money
        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "Refusing to downgrade 38a13f8b2f8e" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:  # put the row back to what it was
            conn.execute(text(f"UPDATE {LEDGER} SET entry_kind = 'PAYMENT_RULE', ladder_sale_id = NULL, ladder_month = NULL, "
                              f"source_payment_allocation_id = :a WHERE id = :i"),
                         {"a": cache["owner_payment_allocations"], "i": entry_id})
        engine.dispose()

        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert not ({"entry_kind", "ladder_sale_id", "ladder_month", "ladder_position", "payable_after"}
                    & _columns(engine, LEDGER))
        assert not ({"owner_commission_ladder_sales", "owner_license_scheduled_stops"}
                    & set(inspect(engine).get_table_names()))
        nullable = {c["name"]: c["nullable"] for c in inspect(engine).get_columns(LEDGER)}
        assert (nullable["source_commercial_invoice_id"], nullable["source_payment_allocation_id"]) == (False, False)
        with engine.begin() as conn:  # the row itself survives the downgrade
            assert conn.execute(text(f"SELECT count(*) FROM {LEDGER}")).scalar_one() == 1
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
