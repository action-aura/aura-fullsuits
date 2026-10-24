"""Migration 1dfa1f6108b2 (Step 0 packages: the contract term) on its OWN
scratch database, upgraded from e7c2a9d5b1f4 with rows already in it.

Proves: existing plans and subscriptions survive with NULL = no contract
(what they were sold as); the new columns take a real contract; the checks
refuse a zero or negative term; the downgrade removes exactly these columns."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "e7c2a9d5b1f4"
NEW_REV = "1dfa1f6108b2"
SCRATCH = "aura_owner_step0_packages_scratch"


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_contract_term_columns_upgrade_existing_rows_checks_and_downgrade():
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
            plan_id = _seed(conn, "owner_plans", cache)
            cache["owner_plans"] = plan_id
            sub_id = _seed(conn, "owner_subscriptions", cache, {"plan_id": plan_id})
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        assert "contract_term_months" in _columns(engine, "owner_plans")
        assert {"contract_term_months", "contract_ends_on"} <= _columns(engine, "owner_subscriptions")
        with engine.begin() as conn:
            # existing rows: untouched, no contract
            assert conn.execute(text("SELECT contract_term_months FROM owner_plans WHERE id = :i"),
                                {"i": plan_id}).scalar_one() is None
            assert conn.execute(text("SELECT contract_term_months, contract_ends_on FROM owner_subscriptions WHERE id = :i"),
                                {"i": sub_id}).one() == (None, None)
            # allow: a real contract
            conn.execute(text("UPDATE owner_plans SET contract_term_months = 12 WHERE id = :i"), {"i": plan_id})
            conn.execute(text("UPDATE owner_subscriptions SET contract_term_months = 24, contract_ends_on = '2028-10-24' "
                              "WHERE id = :i"), {"i": sub_id})
        # deny: zero / negative terms
        for table, row_id in (("owner_plans", plan_id), ("owner_subscriptions", sub_id)):
            for bad in (0, -12):
                with pytest.raises(IntegrityError):
                    with engine.begin() as conn:
                        conn.execute(text(f"UPDATE {table} SET contract_term_months = :v WHERE id = :i"),
                                     {"v": bad, "i": row_id})
        engine.dispose()

        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert "contract_term_months" not in _columns(engine, "owner_plans")
        assert not ({"contract_term_months", "contract_ends_on"} & _columns(engine, "owner_subscriptions"))
        with engine.begin() as conn:  # the rows themselves survive the downgrade
            assert conn.execute(text("SELECT count(*) FROM owner_subscriptions")).scalar_one() == 1
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
