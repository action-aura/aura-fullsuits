"""AUDIT-033 S-AUTH fix round (verifier V finding #1): migration 5f2c8e1a9b37.

Runs on its OWN scratch database (never the shared test database): migrate to
the previous head, seed an installation, upgrade, and prove the columns are
additive/nullable, the partial unique index bites only live installations, and
the downgrade is clean."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL

PREV_HEAD = "54627d59dd59"
NEW_REV = "5f2c8e1a9b37"
SCRATCH = "aura_owner_sauth_pin_migration_scratch"


def _alembic(owner_dir, url, *args):
    env = {**os.environ, "OWNER_DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=owner_dir, env=env, capture_output=True, text=True)


def _seed(conn, table, cache, overrides=None):
    """Insert one minimally-valid row into `table`, recursively seeding every
    NOT NULL foreign key it needs. Returns the new row id."""
    overrides = overrides or {}
    insp = inspect(conn)
    fks = {fk["constrained_columns"][0]: fk for fk in insp.get_foreign_keys(table)}
    values = {}
    for col in insp.get_columns(table):
        name = col["name"]
        if name in overrides:
            values[name] = overrides[name]
            continue
        if col["nullable"] or col.get("default") is not None:
            continue
        if name in fks:
            ref = fks[name]["referred_table"]
            if ref not in cache:
                cache[ref] = _seed(conn, ref, cache)
            values[name] = cache[ref]
            continue
        t = str(col["type"]).upper()
        if name == "id":
            values[name] = str(uuid.uuid4())
        elif "UUID" in t:
            values[name] = str(uuid.uuid4())
        elif "BOOL" in t:
            values[name] = False
        elif "INT" in t or "NUMERIC" in t:
            values[name] = 1
        elif "TIMESTAMP" in t:
            values[name] = "2026-01-01T00:00:00+00:00"
        elif t == "DATE":
            values[name] = "2026-01-01"
        elif "JSON" in t:
            values[name] = "{}"
        else:
            length = getattr(col["type"], "length", None) or 12
            values[name] = uuid.uuid4().hex[: min(length, 12)]
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), values)
    return values["id"]


def test_registry_device_id_columns_index_and_downgrade():
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = TEST_DB_URL.rsplit("/", 1)[0]
    scratch_url, admin_url = f"{base}/{SCRATCH}", f"{base}/postgres"

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH} OWNER aura_owner"))
    admin.dispose()
    try:
        up_prev = _alembic(owner_dir, scratch_url, "upgrade", PREV_HEAD)
        assert up_prev.returncode == 0, up_prev.stderr

        # Seed a pre-existing installation BEFORE the new columns exist.
        engine = create_engine(scratch_url)
        cache: dict = {}
        with engine.begin() as conn:
            license_id = _seed(conn, "owner_licenses", cache)
            cache["owner_licenses"] = license_id
            pre_inst = _seed(conn, "owner_installations", cache, {"license_id": license_id, "status": "ACTIVE"})
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        assert up.returncode == 0, up.stderr

        engine = create_engine(scratch_url)
        cols = {c["name"]: c for c in inspect(engine).get_columns("owner_installations")}
        for name in ("registry_device_id", "registry_device_id_pinned_at"):
            assert name in cols and cols[name]["nullable"], f"{name} must exist and stay nullable"
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT registry_device_id, registry_device_id_pinned_at FROM owner_installations WHERE id=:i"),
                {"i": pre_inst},
            ).one()
        assert tuple(row) == (None, None), "an existing installation must start unpinned"

        dev = str(uuid.uuid4())

        def insert(conn, status):
            _seed(conn, "owner_installations", dict(cache), {
                "license_id": license_id, "status": status, "registry_device_id": dev,
            })

        # Two LIVE installations of one licence cannot both hold the id.
        with engine.begin() as conn:
            insert(conn, "ACTIVE")
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                insert(conn, "ACTIVE")
        # ...but a DEACTIVATED/REPLACED holder frees it (allow half).
        with engine.begin() as conn:
            conn.execute(text("UPDATE owner_installations SET status='DEACTIVATED' WHERE registry_device_id=:d"), {"d": dev})
            insert(conn, "ACTIVE")
            insert(conn, "REPLACED")
        engine.dispose()

        down = _alembic(owner_dir, scratch_url, "downgrade", PREV_HEAD)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        insp = inspect(engine)
        assert "registry_device_id" not in {c["name"] for c in insp.get_columns("owner_installations")}
        assert "uq_owner_installations_license_registry_device" not in {i["name"] for i in insp.get_indexes("owner_installations")}
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
