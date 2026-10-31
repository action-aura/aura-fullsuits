"""Migration e2c4a8b1f730 (new customer flow NC1) on its OWN scratch database,
upgraded from c7d41e90b2a5.

Proves: the new columns and tables appear; existing pilots become kind PILOT and
existing accounts keep password_must_change false; every CHECK / unique index the
design relies on REFUSES what it forbids (a slip without a payment, an ID with a
payment, a zero-byte file, a second TRIAL for one customer, one trial mark per
device key, a VERIFY_EMAIL token carrying a new_email, an unknown kind) and still
ALLOWS the legal twin; the downgrade REFUSES while it would delete a document, a
mark, a trial or a flagged account, and once they are gone removes exactly what
was added."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "c7d41e90b2a5"
NEW_REV = "e2c4a8b1f730"
SCRATCH = "aura_owner_nc1_scratch"
NEW_TABLES = {"owner_customer_documents", "owner_trial_device_marks"}


def _insert(conn, table, **values):
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), values)
    return values.get("id")


def _doc(conn, customer_id, **over):
    values = {"id": str(uuid.uuid4()), "customer_id": customer_id, "kind": "NATIONAL_ID",
              "storage_key": f"{customer_id}/{uuid.uuid4()}.bin", "content_type": "image/jpeg", "size_bytes": 10,
              "sha256": "a" * 64, "enc_key_id": "k1", **over}
    return _insert(conn, "owner_customer_documents", **values)


def test_nc1_schema_upgrade_guards_and_downgrade_refusal():
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
            old_pilot = _seed(conn, "owner_pilot_records", cache)  # a pre-existing, ordinary pilot
            customer_id = cache["owner_customers"]
            other_customer = _seed(conn, "owner_customers", {})
            account_id = str(uuid.uuid4())
            _insert(conn, "owner_customer_accounts", id=account_id, customer_id=customer_id, email="a@example.com",
                    status="INVITED", language="en", session_version=1)
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        inspector = inspect(engine)
        assert NEW_TABLES <= set(inspector.get_table_names())
        assert {"password_must_change", "email_verified_at", "password_set_by_staff_user_id"} <= {
            c["name"] for c in inspector.get_columns("owner_customer_accounts")}
        assert {"pilot_kind", "trial_started_at", "trial_length_days"} <= {
            c["name"] for c in inspector.get_columns("owner_pilot_records")}
        with engine.begin() as conn:  # allow: old rows survive with the old meaning
            assert conn.execute(text("SELECT pilot_kind FROM owner_pilot_records WHERE id = :i"), {"i": old_pilot}).scalar_one() == "PILOT"
            assert conn.execute(text("SELECT password_must_change FROM owner_customer_accounts WHERE id = :i"),
                                {"i": account_id}).scalar_one() is False

        # tokens: VERIFY_EMAIL is a kind now, and like INVITE/RESET carries no new_email
        def token(kind, new_email, h):
            return text("INSERT INTO owner_customer_account_tokens (id, account_id, kind, token_hash, new_email, expires_at) "
                        "VALUES (:id, :a, :k, :h, :n, now())"), {"id": str(uuid.uuid4()), "a": account_id, "k": kind, "h": h, "n": new_email}
        with engine.begin() as conn:
            conn.execute(*token("VERIFY_EMAIL", None, "v1"))
        for kind, new_email, h in (("VERIFY_EMAIL", "x@example.com", "v2"), ("OTHER", None, "v3")):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(*token(kind, new_email, h))

        # documents: a slip needs a payment, an ID must not have one, size > 0, unique storage key, known kinds
        with engine.begin() as conn:
            payment_id = _seed(conn, "owner_payment_records", {"owner_customers": customer_id})
            good = _doc(conn, customer_id)
            _doc(conn, customer_id, kind="COMMERCIAL_REGISTRATION")
            _doc(conn, customer_id, kind="PAYMENT_SLIP", payment_record_id=payment_id)  # allow: slip WITH its payment
        for over in ({"kind": "PAYMENT_SLIP"}, {"payment_record_id": payment_id}, {"size_bytes": 0},
                     {"kind": "SELFIE"}, {"storage_key": "dup-key"}):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    if over.get("storage_key"):
                        _doc(conn, customer_id, storage_key="dup-key")
                    _doc(conn, customer_id, **over)

        # trial marks: one per device key
        with engine.begin() as conn:
            _insert(conn, "owner_trial_device_marks", id=str(uuid.uuid4()), device_key_fingerprint="f" * 64, customer_id=customer_id)
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert(conn, "owner_trial_device_marks", id=str(uuid.uuid4()), device_key_fingerprint="f" * 64, customer_id=other_customer)

        # pilots: one TRIAL per customer, any number of ordinary PILOTs, known kinds only
        def pilot(conn, kind, customer):
            sub = _seed(conn, "owner_subscriptions", {"owner_customers": customer})
            return _seed(conn, "owner_pilot_records", {"owner_customers": customer, "owner_subscriptions": sub},
                         {"customer_id": customer, "subscription_id": sub, "pilot_kind": kind})
        with engine.begin() as conn:
            pilot(conn, "TRIAL", other_customer)
            pilot(conn, "PILOT", other_customer)  # allow: a PILOT beside the TRIAL
            pilot(conn, "PILOT", other_customer)
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                pilot(conn, "TRIAL", other_customer)
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                pilot(conn, "WEIRD", customer_id)
        engine.dispose()

        # deny: the downgrade would shred ID photos / restart trials
        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert refused.returncode != 0 and "Refusing to downgrade e2c4a8b1f730" in refused.stderr
        for what in ("owner_customer_documents", "owner_trial_device_marks", "TRIAL pilot records", "VERIFY_EMAIL tokens"):
            assert what in refused.stderr, what
        engine = create_engine(scratch_url)
        assert NEW_TABLES <= set(inspect(engine).get_table_names())  # nothing was dropped
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM owner_customer_documents"))
            conn.execute(text("DELETE FROM owner_trial_device_marks"))
            conn.execute(text("DELETE FROM owner_pilot_records WHERE pilot_kind = 'TRIAL'"))
            conn.execute(text("DELETE FROM owner_customer_account_tokens WHERE kind = 'VERIFY_EMAIL'"))
            conn.execute(text("UPDATE owner_customer_accounts SET password_must_change = true WHERE id = :i"), {"i": account_id})
        engine.dispose()
        refused = _alembic(owner_dir, scratch_url, "downgrade", PREV)  # a flagged account alone also blocks it
        assert refused.returncode != 0 and "password_must_change" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            conn.execute(text("UPDATE owner_customer_accounts SET password_must_change = false WHERE id = :i"), {"i": account_id})
        engine.dispose()

        # allow: with nothing to lose it removes exactly what was added
        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        inspector = inspect(engine)
        assert not (NEW_TABLES & set(inspector.get_table_names()))
        assert "pilot_kind" not in {c["name"] for c in inspector.get_columns("owner_pilot_records")}
        assert "password_must_change" not in {c["name"] for c in inspector.get_columns("owner_customer_accounts")}
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM owner_customer_accounts")).scalar_one() == 1
        with pytest.raises(IntegrityError):  # the old three-kind CHECK is back
            with engine.begin() as conn:
                conn.execute(*token("VERIFY_EMAIL", None, "v9"))
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
