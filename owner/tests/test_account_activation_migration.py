"""Migrations b1d4f8a26c93 (CA3), c5a2e7d94f16 (CT1) and a3c7e9f15b28 (CA2) on their own
scratch database, upgraded from f6d19b3a05c8 with an OLD download authorization already
in the table: it must survive untouched, the new CHECKs must hold, and each downgrade
must refuse while the data it would destroy exists (and work once that data is gone)."""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

BASE_REV, CA3, CT1, CA2 = "f6d19b3a05c8", "b1d4f8a26c93", "c5a2e7d94f16", "a3c7e9f15b28"  # pragma: allowlist secret -- Alembic revision ids
SCRATCH = "aura_owner_ca_scratch"
AUTH = "owner_release_download_authorizations"


def _contract_version(conn, *, label="v1", body="hello"):
    vid = str(uuid.uuid4())
    conn.execute(text(
        "INSERT INTO owner_contract_versions (id, audience, language, version_label, title, body, body_sha256, lawyer_approved) "
        "VALUES (:i, 'CUSTOMER', 'en', :l, 't', :b, :h, false)"),
        {"i": vid, "l": label, "b": body, "h": hashlib.sha256(body.encode()).hexdigest()})
    return vid


def test_ca3_ct1_ca2_schema_checks_and_downgrade_refusals():
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = TEST_DB_URL.rsplit("/", 1)[0]
    scratch_url, admin_url = f"{base}/{SCRATCH}", f"{base}/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH} OWNER aura_owner"))
    admin.dispose()
    try:
        assert _alembic(owner_dir, scratch_url, "upgrade", BASE_REV).returncode == 0
        engine = create_engine(scratch_url)
        cache: dict = {}
        with engine.begin() as conn:
            old_auth = _seed(conn, AUTH, cache)  # a device link made before the new code existed
            customer_id = _seed(conn, "owner_customers", cache)
            account_id = _seed(conn, "owner_customer_accounts", dict(cache), {"status": "INVITED", "language": "en"})
            product_version_id = conn.execute(text(f"SELECT product_version_id FROM {AUTH} WHERE id = :i"), {"i": old_auth}).scalar_one()
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", CA2)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        insp = inspect(engine)
        assert {"owner_customer_activation_grants", "owner_contract_versions", "owner_contract_acceptances"} <= set(insp.get_table_names())
        with engine.begin() as conn:
            row = conn.execute(text(f"SELECT installation_id, license_id, customer_account_id FROM {AUTH} WHERE id = :i"), {"i": old_auth}).one()
            assert row.installation_id is not None and row.license_id is not None and row.customer_account_id is None  # survived

        website = dict(product_version_id=product_version_id, token_hash=uuid.uuid4().hex, max_uses=3, use_count=0)
        insert = text(f"INSERT INTO {AUTH} (id, product_version_id, installation_id, license_id, customer_account_id, token_hash, "
                      "expires_at, max_uses, use_count) VALUES (gen_random_uuid(), :product_version_id, :inst, :lic, :acc, :token_hash, "
                      "now(), :max_uses, :use_count)")
        for inst, lic, acc in ((None, None, None), (str(uuid.uuid4()), None, None)):  # nobody / a device link without a licence
            with pytest.raises((IntegrityError, DBAPIError)):
                with engine.begin() as conn:
                    conn.execute(insert, {**website, "token_hash": uuid.uuid4().hex, "inst": inst, "lic": lic, "acc": acc})
        with pytest.raises((IntegrityError, DBAPIError)):  # both a device and an account
            with engine.begin() as conn:
                old = conn.execute(text(f"SELECT installation_id, license_id FROM {AUTH} WHERE id = :i"), {"i": old_auth}).one()
                conn.execute(insert, {**website, "token_hash": uuid.uuid4().hex, "inst": old.installation_id, "lic": old.license_id, "acc": account_id})
        with engine.begin() as conn:  # allow: a link for an account alone
            conn.execute(insert, {**website, "inst": None, "lic": None, "acc": account_id})
        engine.dispose()

        refused = _alembic(owner_dir, scratch_url, "downgrade", CT1)
        assert refused.returncode != 0 and "Refusing to downgrade a3c7e9f15b28" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {AUTH} WHERE customer_account_id IS NOT NULL"))
        engine.dispose()
        assert _alembic(owner_dir, scratch_url, "downgrade", CT1).returncode == 0
        engine = create_engine(scratch_url)
        cols = {c["name"]: c for c in inspect(engine).get_columns(AUTH)}
        assert "customer_account_id" not in cols and cols["installation_id"]["nullable"] is False and cols["license_id"]["nullable"] is False
        engine.dispose()

        # CT1: acceptances are legal evidence -- the downgrade refuses while any exist
        assert _alembic(owner_dir, scratch_url, "upgrade", CT1).returncode == 0
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            version = _contract_version(conn)
            conn.execute(text(
                "INSERT INTO owner_contract_acceptances (id, contract_version_id, audience, customer_id, email_snapshot, language, "
                "text_sha256, accepted_at) VALUES (gen_random_uuid(), :v, 'CUSTOMER', :c, 'e@x.y', 'en', 'h', now())"),
                {"v": version, "c": customer_id})
        engine.dispose()
        refused = _alembic(owner_dir, scratch_url, "downgrade", CA3)
        assert refused.returncode != 0 and "Refusing to downgrade c5a2e7d94f16" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE owner_contract_acceptances DISABLE TRIGGER trg_owner_contract_acceptances_append_only"))
            conn.execute(text("DELETE FROM owner_contract_acceptances"))
            conn.execute(text("DELETE FROM owner_contract_versions"))
        engine.dispose()
        assert _alembic(owner_dir, scratch_url, "downgrade", CA3).returncode == 0
        engine = create_engine(scratch_url)
        names = set(inspect(engine).get_table_names())
        assert "owner_contract_versions" not in names and "owner_contract_acceptances" not in names
        with engine.connect() as conn:  # the trigger functions went with the tables
            assert conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'owner_contract_%'")).scalar_one() == 0
        engine.dispose()

        # CA3: a grant row is the only record that an activation was authorised by a ticket or code
        assert _alembic(owner_dir, scratch_url, "upgrade", CA3).returncode == 0
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO owner_customer_activation_grants (id, kind, token_hash, customer_id, account_id, device_key_fingerprint, "
                "session_version_at_issue, expires_at) VALUES (gen_random_uuid(), 'ACCOUNT_TICKET', 'h1', :c, :a, 'f', 1, now())"),
                {"c": customer_id, "a": account_id})
        grant = ("INSERT INTO owner_customer_activation_grants (id, kind, token_hash, customer_id, account_id, license_id, "
                 "device_key_fingerprint, session_version_at_issue, expires_at) VALUES (gen_random_uuid(), :k, :h, :c, :a, :l, :f, :v, now())")
        half_filled = (
            dict(k="ACCOUNT_TICKET", a=None, l=None, f=None, v=None),          # a ticket bound to nobody and no device
            dict(k="ACCOUNT_TICKET", a=account_id, l=None, f=None, v=1),       # a ticket not bound to a device
            dict(k="ACTIVATION_CODE", a=account_id, l=None, f=None, v=None),   # a code naming an account, no licence
        )
        for fields in half_filled:
            with pytest.raises((IntegrityError, DBAPIError)):
                with engine.begin() as conn:
                    conn.execute(text(grant), {"h": uuid.uuid4().hex, "c": customer_id, **fields})
        engine.dispose()
        refused = _alembic(owner_dir, scratch_url, "downgrade", BASE_REV)
        assert refused.returncode != 0 and "Refusing to downgrade b1d4f8a26c93" in refused.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM owner_customer_activation_grants"))
        engine.dispose()
        assert _alembic(owner_dir, scratch_url, "downgrade", BASE_REV).returncode == 0
        engine = create_engine(scratch_url)
        assert "owner_customer_activation_grants" not in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
