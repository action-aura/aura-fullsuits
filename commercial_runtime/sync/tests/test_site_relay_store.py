"""Tests for `commercial_runtime.sync.site_relay.store` and its sibling
`commercial_runtime.sync.site_relay.replay` -- the data layer for the LAN
site relay (retail schema v30, `products/retail/backend/database/schema.py`'s
`_migrate_add_site_relay`; see `docs/launch-readiness/lan-restaurant-design.md`
sec3/sec5 for what this DAO exists to support).

Unlike this directory's other fixtures (e.g. test_sync_service.py's `_SCHEMA`
constant, a hand-written CREATE TABLE string), this file builds its database
by running the REAL migration chain -- `database.schema.init_retail()`, the
exact function `products/retail/backend/app.py` calls at boot -- rather than
re-declaring the six `site_*` tables' DDL here. A test against hand-written
DDL would prove nothing about whether the schema that actually SHIPS still
matches this DAO's assumptions; running the real migration is the only way
this file can catch a drift between the two. This mirrors
`products/retail/tests/retail_loyalty_return_link_test.py`'s own precedent
for testing a specific schema version's migration (sys.path setup +
AURA_APP_DATA sandboxing before the schema module is ever imported), trimmed
down to just the schema/DAO layer -- no Flask app, no licensing seed, no HTTP
client, since nothing here needs them.

CRITICAL, same convention as every file under products/retail/tests/:
`AURA_APP_DATA` is read by database/schema.py at IMPORT time (its
module-level `BASE_DIR`/`SUBSYS_DIR` computation), so this must be set
before schema.py is ever imported -- by this file OR by any other test file
sharing the same pytest process. Run this file on its own, one process per
file:

    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_store.py -q --color=no

STOP CONDITION (per this task's own plan): if the six `site_*` tables do not
exist -- e.g. because `_migrate_add_site_relay` has not landed in
database/schema.py yet -- `_bootstrap_real_retail_db()` below fails loudly
at collection time (the real migration chain raises), and that failure IS
the intended signal. Nothing in this file papers over that by hand-creating
the tables itself.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ── Real-schema bootstrap (see module docstring) ────────────────────────────
#
# Mirrors products/retail/tests/*.py's own sys.path convention: schema.py's
# migration chain internally imports products/retail/backend-relative
# packages (e.g. `core.retail.stock_reconciliation`, reached from an OLDER
# migration step this v30 chain still runs through on a fresh install), so
# `products/retail/backend` itself must be on sys.path, not just the repo
# root, or those internal imports raise ModuleNotFoundError before the
# chain ever reaches v30's own step.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# A throwaway AURA_APP_DATA directory -- MUST be set before the first import
# of database.schema below, since that module reads this env var at import
# time to compute BASE_DIR/SUBSYS_DIR. Without this, schema.py falls back to
# a path derived from its own file location (four parents up from
# schema.py, i.e. this repo's products/ directory) and migration_safety's
# live-backup mechanism would write real backup files into this repo's own
# working tree -- confirmed the hard way while researching this task, not
# guessed at.
_DATA_DIR = Path(tempfile.mkdtemp(prefix="site_relay_store_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.sync.site_relay import replay, store  # noqa: E402


def teardown_module(module):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


# Runs the REAL v1 -> v30 migration chain exactly once for this whole test
# file (see module docstring for why this is `init_retail()` -- the same
# function app.py calls at boot -- rather than hand-written DDL). If the six
# site_* tables do not exist yet (e.g. `_migrate_add_site_relay` has not
# landed in schema.py), THIS LINE is where that fact surfaces: it raises
# whatever the missing migration step raises, at collection time, for every
# test in this file at once -- which is the intended signal, not a bug in
# this fixture. See this file's own "STOP CONDITION" paragraph above.
retail_schema.init_retail()
_DB_PATH = retail_schema._get_path("retail")


def _fresh_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


@pytest.fixture
def conn():
    """One real-schema connection per test, with the site_* tables cleared
    first.

    Deliberately NOT a fresh database per test (that would mean re-running
    the entire v1->v30 migration chain -- expensive -- for every single
    test function) and deliberately NOT isolated by a tenant column the way
    products/retail/tests/*.py's own per-test `_new_shop()` fixture isolates
    by `company_id`: these six tables carry no such column at all, by
    design (see store.py's module docstring, "DELIBERATELY NO PER-LICENCE
    SCOPING"). So isolation between tests is achieved the direct way --
    clearing exactly the rows the previous test could have left behind --
    rather than filtering by some column that does not exist here.
    """
    c = _fresh_conn()
    c.execute("DELETE FROM site_sync_events")
    c.execute("DELETE FROM site_sync_nonces")
    c.execute("DELETE FROM site_device_cursors")
    c.execute("DELETE FROM site_paired_devices")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _event(event_id=None, entity_type="sale", entity_id=None, event_type="create",
           payload=None, created_at=None) -> dict:
    return {
        "id": event_id or str(uuid.uuid4()),
        "entity_type": entity_type,
        "entity_id": entity_id or str(uuid.uuid4()),
        "event_type": event_type,
        "payload": payload if payload is not None else {"amount": 10},
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


# ── append_events / dedup (THE load-bearing guarantee) ──────────────────────

def test_append_events_returns_actual_insert_count_and_dedups_retries(conn):
    """The dedup guarantee this whole module exists to provide: a retried
    push of the SAME two events must insert nothing the second time, and
    the table must hold exactly the two real rows, never four. Mutation-
    proved in this task's own report: flipping `INSERT OR IGNORE` to
    `INSERT OR REPLACE` in store.py's append_events makes the SECOND assert
    below go RED (it returns 2 again, not 0) -- restoring `OR IGNORE` turns
    it back GREEN."""
    e1, e2 = _event(), _event()

    inserted_first = store.append_events(conn, [e1, e2], origin_device_id="dev-a")
    conn.commit()
    assert inserted_first == 2

    inserted_retry = store.append_events(conn, [e1, e2], origin_device_id="dev-a")
    conn.commit()
    assert inserted_retry == 0

    row_count = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    assert row_count == 2


def test_append_events_stores_payload_and_created_at_verbatim(conn):
    created_at = "2026-09-01T10:00:00+00:00"
    event = _event(payload={"total": 42.5, "note": "kept as-is"}, created_at=created_at)
    store.append_events(conn, [event], origin_device_id="dev-a")
    conn.commit()

    row = conn.execute(
        "SELECT payload, created_at FROM site_sync_events WHERE id = ?", (event["id"],)
    ).fetchone()
    assert row["created_at"] == created_at  # never rewritten to local time
    import json
    assert json.loads(row["payload"]) == {"total": 42.5, "note": "kept as-is"}


# ── validate_event ───────────────────────────────────────────────────────────

def test_validate_event_rejects_missing_key():
    raw = _event()
    del raw["payload"]
    with pytest.raises(store.InvalidEventError):
        store.validate_event(raw)


def test_validate_event_rejects_non_dict_payload():
    raw = _event(payload="not-a-dict")
    with pytest.raises(store.InvalidEventError):
        store.validate_event(raw)


def test_validate_event_rejects_empty_entity_type():
    raw = _event(entity_type="")
    with pytest.raises(store.InvalidEventError):
        store.validate_event(raw)


def test_validate_event_rejects_over_long_entity_type():
    raw = _event(entity_type="x" * (store.ENTITY_TYPE_MAX_LEN + 1))
    with pytest.raises(store.InvalidEventError):
        store.validate_event(raw)


def test_validate_event_rejects_unknown_event_type():
    raw = _event(event_type="teleport")
    with pytest.raises(store.InvalidEventError):
        store.validate_event(raw)


def test_validate_event_accepts_non_uuid_id_and_entity_id():
    """The one deliberate deviation from Owner's _build_event (see
    validate_event's own docstring): Owner requires id/entity_id to parse
    as uuid.UUID because its columns are Postgres `uuid`; this DAO's
    columns are plain TEXT, so a non-UUID string must be ACCEPTED, not
    rejected -- tightening this would reject event shapes this SQLite log
    can legitimately carry."""
    raw = _event(event_id="not-a-uuid-at-all", entity_id="also-not-a-uuid")
    validated = store.validate_event(raw)
    assert validated["id"] == "not-a-uuid-at-all"
    assert validated["entity_id"] == "also-not-a-uuid"


# ── read_events_since ────────────────────────────────────────────────────────

def test_read_events_since_excludes_own_origin_and_orders_by_seq(conn):
    """Self-exclusion is the guarantee that stops a device receiving its own
    just-pushed events back on its next pull. Mutation-proved in this
    task's own report: dropping the `origin_device_id != ?` clause from
    store.py's read_events_since makes the first assert below go RED (dev-a
    would see its own two events back) -- restoring the clause turns it
    back GREEN."""
    e_a1, e_b1, e_a2 = _event(), _event(), _event()
    store.append_events(conn, [e_a1], origin_device_id="dev-a")
    store.append_events(conn, [e_b1], origin_device_id="dev-b")
    store.append_events(conn, [e_a2], origin_device_id="dev-a")
    conn.commit()

    rows_excluding_a = store.read_events_since(conn, since=0, exclude_device_id="dev-a")
    assert [r["id"] for r in rows_excluding_a] == [e_b1["id"]]

    rows_excluding_b = store.read_events_since(conn, since=0, exclude_device_id="dev-b")
    assert [r["id"] for r in rows_excluding_b] == [e_a1["id"], e_a2["id"]]


def test_read_events_since_honours_limit(conn):
    events = [_event() for _ in range(5)]
    store.append_events(conn, events, origin_device_id="dev-a")
    conn.commit()

    rows = store.read_events_since(conn, since=0, exclude_device_id="dev-b", limit=3)
    assert len(rows) == 3


# ── resolve_pull_cursor / max_event_seq ─────────────────────────────────────

def test_resolve_pull_cursor_clamps_absurd_since_on_zero_rows(conn):
    """The clamp that stops an absurd client-supplied `since` from being
    persisted as a permanent, unrecoverable high-water mark. Mutation-
    proved in this task's own report: making resolve_pull_cursor return
    `since` verbatim on the zero-rows path makes the second assert below go
    RED (cursor would equal 10**9) -- restoring the clamp turns it back
    GREEN."""
    store.append_events(conn, [_event()], origin_device_id="dev-a")
    conn.commit()
    real_max = store.max_event_seq(conn)

    cursor = store.resolve_pull_cursor(conn, since=10**9, rows=[])
    assert cursor == real_max
    assert cursor != 10**9


def test_resolve_pull_cursor_uses_last_row_seq_when_rows_present(conn):
    store.append_events(conn, [_event(), _event()], origin_device_id="dev-a")
    conn.commit()
    rows = store.read_events_since(conn, since=0, exclude_device_id="dev-b")
    cursor = store.resolve_pull_cursor(conn, since=0, rows=rows)
    assert cursor == rows[-1]["seq"]


# ── paired devices / cursors (remaining DAO surface) ────────────────────────

def test_pair_lookup_and_revoke_device(conn):
    assert store.lookup_paired_device(conn, "dev-x") is None

    store.pair_device(conn, "dev-x", device_public_key="pubkey-1", label="Tablet 1")
    conn.commit()
    device = store.lookup_paired_device(conn, "dev-x")
    assert device["device_public_key"] == "pubkey-1"
    assert device["label"] == "Tablet 1"
    assert device["revoked_at"] is None

    store.revoke_device(conn, "dev-x")
    conn.commit()
    revoked = store.lookup_paired_device(conn, "dev-x")
    assert revoked["revoked_at"] is not None

    # Re-pairing is an explicit re-authorization -- it must clear a prior revoke.
    store.pair_device(conn, "dev-x", device_public_key="pubkey-2", label="Tablet 1 replaced")
    conn.commit()
    re_paired = store.lookup_paired_device(conn, "dev-x")
    assert re_paired["revoked_at"] is None
    assert re_paired["device_public_key"] == "pubkey-2"


def test_advance_device_cursor_never_regresses(conn):
    assert store.read_device_cursor(conn, "dev-y") == 0

    store.advance_device_cursor(conn, "dev-y", 10)
    conn.commit()
    assert store.read_device_cursor(conn, "dev-y") == 10

    store.advance_device_cursor(conn, "dev-y", 3)  # lower value must never regress the stored cursor
    conn.commit()
    assert store.read_device_cursor(conn, "dev-y") == 10

    store.advance_device_cursor(conn, "dev-y", 25)
    conn.commit()
    assert store.read_device_cursor(conn, "dev-y") == 25


# ── replay.py: timestamp freshness ──────────────────────────────────────────

def test_validate_timestamp_accepts_now():
    now = datetime.now(timezone.utc)
    replay.validate_timestamp(now, replay.DEFAULT_SKEW_SECONDS, now=now)  # must not raise


def test_validate_timestamp_rejects_too_far_in_the_past():
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=replay.DEFAULT_SKEW_SECONDS + 60)
    with pytest.raises(replay.ReplayError) as exc:
        replay.validate_timestamp(stale, replay.DEFAULT_SKEW_SECONDS, now=now)
    assert exc.value.reason_code == "TIMESTAMP_OUTSIDE_ALLOWED_WINDOW"


def test_validate_timestamp_rejects_too_far_in_the_future():
    now = datetime.now(timezone.utc)
    future = now + timedelta(seconds=replay.DEFAULT_SKEW_SECONDS + 60)
    with pytest.raises(replay.ReplayError) as exc:
        replay.validate_timestamp(future, replay.DEFAULT_SKEW_SECONDS, now=now)
    assert exc.value.reason_code == "TIMESTAMP_OUTSIDE_ALLOWED_WINDOW"


# ── replay.py: nonce burn / prune ────────────────────────────────────────────

def test_consume_nonce_succeeds_once_then_raises_on_reuse(conn):
    nonce = uuid.uuid4().hex
    replay.consume_nonce(conn, nonce, scope="sync_push", ttl_seconds=replay.DEFAULT_NONCE_TTL_SECONDS)

    with pytest.raises(replay.ReplayError) as exc:
        replay.consume_nonce(conn, nonce, scope="sync_push", ttl_seconds=replay.DEFAULT_NONCE_TTL_SECONDS)
    assert exc.value.reason_code == "NONCE_REUSED"


def test_consume_nonce_same_nonce_different_scope_succeeds(conn):
    nonce = uuid.uuid4().hex
    replay.consume_nonce(conn, nonce, scope="sync_push", ttl_seconds=replay.DEFAULT_NONCE_TTL_SECONDS)
    # Must NOT raise -- a nonce is scoped per operation type, exactly like
    # Owner's own nonce_scope distinguishing push from pull.
    replay.consume_nonce(conn, nonce, scope="sync_pull", ttl_seconds=replay.DEFAULT_NONCE_TTL_SECONDS)


def test_prune_nonces_removes_expired_and_keeps_fresh(conn):
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=replay.DEFAULT_NONCE_TTL_SECONDS + 60)
    conn.execute(
        "INSERT INTO site_sync_nonces (scope, nonce, seen_at) VALUES (?, ?, ?)",
        ("sync_push", "old-nonce", old.isoformat()),
    )
    conn.execute(
        "INSERT INTO site_sync_nonces (scope, nonce, seen_at) VALUES (?, ?, ?)",
        ("sync_push", "fresh-nonce", now.isoformat()),
    )
    conn.commit()

    removed = replay.prune_nonces(conn, replay.DEFAULT_NONCE_TTL_SECONDS, now=now)
    conn.commit()
    assert removed == 1

    remaining = {r["nonce"] for r in conn.execute("SELECT nonce FROM site_sync_nonces").fetchall()}
    assert remaining == {"fresh-nonce"}


# ── The TTL/skew invariant itself ───────────────────────────────────────────

def test_nonce_ttl_is_strictly_greater_than_twice_the_skew():
    """A future edit that shrinks DEFAULT_NONCE_TTL_SECONDS or grows
    DEFAULT_SKEW_SECONDS independently must fail HERE, by name, rather than
    surface as an unexplained successful replay in the field. replay.py
    itself also asserts this at import time (see its own module docstring,
    'THE ONE SECURITY INVARIANT') -- this test exists so the relationship
    is also visible as a named, reportable test result, not only as an
    import-time crash somewhere no one is looking."""
    assert replay.DEFAULT_NONCE_TTL_SECONDS > 2 * replay.DEFAULT_SKEW_SECONDS
