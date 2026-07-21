"""Part V/Y MIGRATION: Phase 5 -> Phase 6 upgrade on a POPULATED database,
zero data loss, rollback, schema drift, FK/unique/index checks."""
from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text

from tests.conftest import TEST_DB_URL


def test_upgrade_on_populated_phase5_database_loses_no_records():
    """A separate scratch database: migrate to the Phase-5 revision, seed real
    RBAC/catalog data (Phase 5 style), THEN upgrade to Phase 6 head, and
    confirm every Phase 5 row survives untouched."""
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scratch_url = TEST_DB_URL.rsplit("/", 1)[0] + "/aura_owner_phase6_migration_scratch"
    admin_url = TEST_DB_URL.rsplit("/", 1)[0] + "/postgres"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS aura_owner_phase6_migration_scratch"))
        conn.execute(text("CREATE DATABASE aura_owner_phase6_migration_scratch OWNER aura_owner"))
    admin_engine.dispose()

    env = {**os.environ, "OWNER_DATABASE_URL": scratch_url}
    # Migrate to the Phase 5 revision specifically (not head) to simulate a
    # real pre-Phase-6 database.
    up_phase5 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "62e4adb0a7b9"], cwd=owner_dir, env=env, capture_output=True, text=True
    )
    assert up_phase5.returncode == 0, up_phase5.stderr

    # Seed real Phase 5 data directly via SQL (no app import needed here).
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO owner_permissions (id, code, category, created_at, updated_at) VALUES (gen_random_uuid(), 'test.perm', 'TEST', now(), now())"))
        conn.execute(text(
            "INSERT INTO owner_products (id, product_code, name, is_active, commercial_status, is_sellable, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'TEST_PRODUCT', 'Test Product', true, 'PILOT', true, now(), now())"
        ))
    with engine.connect() as conn:
        perm_count_before = conn.execute(text("SELECT count(*) FROM owner_permissions")).scalar()
        product_count_before = conn.execute(text("SELECT count(*) FROM owner_products")).scalar()
    engine.dispose()

    # Now upgrade to Phase 6 head.
    up_phase6 = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=owner_dir, env=env, capture_output=True, text=True)
    assert up_phase6.returncode == 0, up_phase6.stderr

    engine2 = create_engine(scratch_url)
    with engine2.connect() as conn:
        perm_count_after = conn.execute(text("SELECT count(*) FROM owner_permissions")).scalar()
        product_count_after = conn.execute(text("SELECT count(*) FROM owner_products")).scalar()
    table_names = inspect(engine2).get_table_names()
    engine2.dispose()

    assert perm_count_after == perm_count_before
    assert product_count_after == product_count_before
    for phase6_table in ("owner_signing_keys", "owner_device_public_keys", "owner_signed_assertions", "owner_offline_policies"):
        assert phase6_table in table_names

    # Rollback to Phase 5 revision -- Phase 6 tables gone, Phase 5 data intact.
    down = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "62e4adb0a7b9"], cwd=owner_dir, env=env, capture_output=True, text=True)
    assert down.returncode == 0, down.stderr
    engine3 = create_engine(scratch_url)
    with engine3.connect() as conn:
        perm_count_final = conn.execute(text("SELECT count(*) FROM owner_permissions")).scalar()
    table_names_after_down = inspect(engine3).get_table_names()
    engine3.dispose()
    assert perm_count_final == perm_count_before
    assert "owner_signing_keys" not in table_names_after_down

    admin_engine2 = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine2.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS aura_owner_phase6_migration_scratch"))
    admin_engine2.dispose()


def test_no_schema_drift_after_phase6(app):
    with app.app_context():
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from app.extensions import get_engine
        from app.models import Base

        engine = get_engine()
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)
        assert diff == [], f"Uncommitted schema drift detected: {diff}"


def test_key_secret_hmac_unique_constraint_enforced(app, seeded):
    from tests.conftest import make_license, make_staff

    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from sqlalchemy.exc import IntegrityError

        actor_id = make_staff(app, "mig1@example.com")
        license_id_1, _ = make_license(app, actor_id)
        license_id_2, _ = make_license(app, actor_id)
        lic1 = db_session.get(License, license_id_1)
        lic2 = db_session.get(License, license_id_2)
        lic2.key_secret_hmac = lic1.key_secret_hmac
        try:
            db_session.commit()
            assert False, "should have raised IntegrityError"
        except IntegrityError:
            db_session.rollback()


def test_nonce_unique_constraint_enforced(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing_service import SecurityNonceRecord
        from datetime import datetime, timedelta, timezone
        from sqlalchemy.exc import IntegrityError

        expires = datetime.now(timezone.utc) + timedelta(seconds=60)
        db_session.add(SecurityNonceRecord(nonce="dup-nonce-value-12345", scope="activation", expires_at=expires))
        db_session.commit()
        db_session.add(SecurityNonceRecord(nonce="dup-nonce-value-12345", scope="activation", expires_at=expires))
        try:
            db_session.commit()
            assert False, "should have raised IntegrityError"
        except IntegrityError:
            db_session.rollback()
