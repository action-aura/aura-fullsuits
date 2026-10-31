"""Migration a6e81bf3d32c (Step 2 CA1: customer website accounts) on its OWN
scratch database, upgraded from a29865d5eab4.

Proves: the four tables and subscriptions.branch_label appear; the CHECKs and
unique indexes refuse what the design forbids (ACTIVE without a password, a
DISABLED row with no disabled_at, a stray new_email, a second account for one
customer, one email on two customers, an unknown status/kind); an existing
subscription survives with branch_label NULL; the downgrade REFUSES while any
account/branch label exists and, once they are removed, removes exactly what
was added."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "a29865d5eab4"
NEW_REV = "a6e81bf3d32c"
SCRATCH = "aura_owner_step2_ca1_scratch"
NEW_TABLES = {
    "owner_customer_accounts", "owner_customer_account_tokens",
    "owner_customer_sessions", "owner_customer_login_attempts",
}


def _insert_account(conn, customer_id, email, **over):
    values = {"id": str(uuid.uuid4()), "customer_id": customer_id, "email": email, "status": "INVITED",
              "language": "en", "session_version": 1, **over}
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO owner_customer_accounts ({cols}) VALUES ({params})"), values)
    return values["id"]


def test_accounts_schema_upgrade_guards_and_downgrade_refusal():
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
            customer_id = cache["owner_customers"]
            other_customer = _seed(conn, "owner_customers", {})
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        assert NEW_TABLES <= set(inspect(engine).get_table_names())
        assert "branch_label" in {c["name"] for c in inspect(engine).get_columns("owner_subscriptions")}
        with engine.begin() as conn:  # allow: the old subscription survives, unlabelled
            assert conn.execute(text("SELECT branch_label FROM owner_subscriptions WHERE id = :i"), {"i": sub_id}).scalar_one() is None

        # allow: a normal invited account
        with engine.begin() as conn:
            account_id = _insert_account(conn, customer_id, "a@example.com")
        # deny: a second account for the same customer; the same email on another customer
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert_account(conn, customer_id, "b@example.com")
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert_account(conn, other_customer, "a@example.com")
        # deny: ACTIVE with no password; DISABLED with no disabled_at; disabled_at on a non-DISABLED row; bad status
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE owner_customer_accounts SET status = 'ACTIVE' WHERE id = :i"), {"i": account_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE owner_customer_accounts SET status = 'DISABLED' WHERE id = :i"), {"i": account_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE owner_customer_accounts SET disabled_at = now() WHERE id = :i"), {"i": account_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE owner_customer_accounts SET status = 'WEIRD' WHERE id = :i"), {"i": account_id})
        # allow: ACTIVE with a password; DISABLED with a timestamp
        with engine.begin() as conn:
            conn.execute(text("UPDATE owner_customer_accounts SET status = 'ACTIVE', password_hash = 'x' WHERE id = :i"), {"i": account_id})
            conn.execute(text("UPDATE owner_customer_accounts SET status = 'DISABLED', disabled_at = now() WHERE id = :i"), {"i": account_id})

        # tokens: kind list, EMAIL_CHANGE needs new_email and nothing else may carry one
        def token(kind, new_email, h):
            return text("INSERT INTO owner_customer_account_tokens (id, account_id, kind, token_hash, new_email, expires_at) "
                        "VALUES (:id, :a, :k, :h, :n, now())"), {"id": str(uuid.uuid4()), "a": account_id, "k": kind, "h": h, "n": new_email}
        with engine.begin() as conn:
            conn.execute(*token("INVITE", None, "h1"))
            conn.execute(*token("EMAIL_CHANGE", "n@example.com", "h2"))
        for kind, new_email, h in (("EMAIL_CHANGE", None, "h3"), ("INVITE", "x@example.com", "h4"), ("OTHER", None, "h5"), ("RESET", None, "h1")):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(*token(kind, new_email, h))
        engine.dispose()

        # deny: the downgrade would erase customers' sign-ins
        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "Refusing to downgrade a6e81bf3d32c" in refused.stderr
        engine = create_engine(scratch_url)
        assert NEW_TABLES <= set(inspect(engine).get_table_names())  # nothing was dropped
        with engine.begin() as conn:  # a branch label alone also blocks it
            conn.execute(text("DELETE FROM owner_customer_account_tokens"))
            conn.execute(text("DELETE FROM owner_customer_accounts"))
            conn.execute(text("UPDATE owner_subscriptions SET branch_label = 'Dabouq' WHERE id = :i"), {"i": sub_id})
        engine.dispose()
        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "owner_subscriptions.branch_label" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            conn.execute(text("UPDATE owner_subscriptions SET branch_label = NULL WHERE id = :i"), {"i": sub_id})
        engine.dispose()

        # allow: with nothing to lose it removes exactly what was added
        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert not (NEW_TABLES & set(inspect(engine).get_table_names()))
        assert "branch_label" not in {c["name"] for c in inspect(engine).get_columns("owner_subscriptions")}
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM owner_subscriptions")).scalar_one() == 1
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
