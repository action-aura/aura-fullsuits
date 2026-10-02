"""AUDIT-033 / review V3o G2: migration 7c41d2e9a0b5 on its OWN scratch database.

Proves the unique index now bites in EVERY status (a REPLACED/DEACTIVATED holder
keeps the id reserved), the history-guard index exists, and the downgrade
restores the old (status-predicated) index."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.test_sauth_registry_pin_migration import _alembic, _seed

PREV = "5f2c8e1a9b37"
NEW_REV = "7c41d2e9a0b5"
SCRATCH = "aura_owner_sauth_pin_round2_scratch"
IDX = "uq_owner_installations_license_registry_device"


def _index(engine, table, name):
    return {i["name"]: i for i in inspect(engine).get_indexes(table)}.get(name)


def test_unique_index_covers_every_status_and_downgrade_restores_the_old_one():
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
            license_id = _seed(conn, "owner_licenses", cache)
            cache["owner_licenses"] = license_id
        engine.dispose()
        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr

        engine = create_engine(scratch_url)
        idx = _index(engine, "owner_installations", IDX)
        assert idx is not None and idx["unique"] and idx["column_names"] == ["license_id", "registry_device_id"]
        assert _index(engine, "owner_sync_events", "ix_owner_sync_events_license_entity") is not None

        dev = str(uuid.uuid4())

        def insert(conn, status, rid=dev):
            _seed(conn, "owner_installations", dict(cache), {"license_id": license_id, "status": status, "registry_device_id": rid})

        with engine.begin() as conn:
            insert(conn, "REPLACED")
        # deny: a REPLACED holder still reserves the id (the old index let this through)
        for status in ("ACTIVE", "DEACTIVATED", "REPLACED"):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    insert(conn, status)
        # allow: a different id, and unpinned rows (NULL) are unaffected
        with engine.begin() as conn:
            insert(conn, "ACTIVE", rid=str(uuid.uuid4()))
            insert(conn, "ACTIVE", rid=None)
            insert(conn, "ACTIVE", rid=None)
        engine.dispose()

        down = _alembic(owner_dir, scratch_url, "downgrade", PREV)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert _index(engine, "owner_sync_events", "ix_owner_sync_events_license_entity") is None
        with engine.begin() as conn:
            insert(conn, "ACTIVE")       # old index: the REPLACED holder frees the id again
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
