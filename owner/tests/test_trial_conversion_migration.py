"""Migration f6d19b3a05c8 (new customer flow NC2) on its own scratch database,
upgraded from e2c4a8b1f730: the converts_subscription_id column, its partial unique
index (one LIVE order per trial; a cancelled order frees it), and a downgrade that
refuses while any order names a trial."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "e2c4a8b1f730"
NEW_REV = "f6d19b3a05c8"
SCRATCH = "aura_owner_nc2_scratch"


def _order(conn, cache, subscription_id, status):
    return _seed(conn, "owner_sales_orders", dict(cache), {"status": status, "converts_subscription_id": subscription_id,
                                                           "order_number": "O-" + uuid.uuid4().hex[:10]})


def test_nc2_schema_index_and_downgrade_refusal():
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
            sub_id = _seed(conn, "owner_subscriptions", cache)
            old_order = _seed(conn, "owner_sales_orders", dict(cache), {"order_number": "O-OLD"})
        engine.dispose()
        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        assert "converts_subscription_id" in {c["name"] for c in inspect(engine).get_columns("owner_sales_orders")}
        with engine.begin() as conn:  # allow: the old order survives, converting nothing
            assert conn.execute(text("SELECT converts_subscription_id FROM owner_sales_orders WHERE id = :i"),
                                {"i": old_order}).scalar_one() is None
            _order(conn, cache, sub_id, "DRAFT")                       # allow: one live order for the trial
            _order(conn, cache, sub_id, "CANCELLED")                   # allow: any number of cancelled ones
            _order(conn, cache, sub_id, "CANCELLED")
            _seed(conn, "owner_sales_orders", dict(cache), {"order_number": "O-PLAIN-2"})  # allow: ordinary orders, many
        with pytest.raises(IntegrityError):                            # deny: a second live order for the same trial
            with engine.begin() as conn:
                _order(conn, cache, sub_id, "CONFIRMED")
        engine.dispose()

        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "Refusing to downgrade f6d19b3a05c8" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            conn.execute(text("UPDATE owner_sales_orders SET converts_subscription_id = NULL"))
        engine.dispose()
        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert "converts_subscription_id" not in {c["name"] for c in inspect(engine).get_columns("owner_sales_orders")}
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
