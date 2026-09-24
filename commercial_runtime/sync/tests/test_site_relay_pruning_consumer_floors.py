"""Tests for `pruning.py`'s new `extra_floors` keyword and `listener.py`'s
`run_housekeeping_sweep` -- the floors that protect a hub's OWN two
`SyncService` streams (retail + registry) from having site-log rows pruned
out from under them before they have consumed those rows.

THE DEFECT THIS CLOSES: before this task, `safe_prune_watermark` only knew
about paired LAN devices and the forwarder. Once the hub itself reads its
own site log (via `local_client.LocalSiteRelayClient`, decision A in this
task's own plan), the hub's own consumption position is a THIRD reader that
must also floor the watermark, or housekeeping could prune a row the hub's
own SyncService has not applied yet.

Real-schema bootstrap, same convention as this package's other test files.
Run this file on its own, one process per file:

    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_pruning_consumer_floors.py -q --color=no
"""
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DATA_DIR = Path(tempfile.mkdtemp(prefix="site_relay_pruning_floors_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.sync.site_relay import listener, pruning, store  # noqa: E402


def teardown_module(module):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


retail_schema.init_retail()
_DB_PATH = retail_schema._get_path("retail")


def _fresh_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


@pytest.fixture
def conn():
    c = _fresh_conn()
    c.execute("DELETE FROM site_sync_events")
    # `site_sync_events.seq` is INTEGER PRIMARY KEY AUTOINCREMENT (see
    # store.py's own module docstring on why pruning depends on that never
    # reissuing a number) -- SQLite tracks the high-water mark in
    # `sqlite_sequence` independently of the rows themselves, so a bare
    # DELETE above does NOT reset it. Several tests below assert on
    # SMALL, absolute seq values (e.g. "prune exactly the first 3 rows"),
    # so this explicit reset gives each test the SAME fresh 1..N seq range
    # the DELETE alone would only give the very first test in this file.
    c.execute("DELETE FROM sqlite_sequence WHERE name = 'site_sync_events'")
    c.execute("DELETE FROM site_device_cursors")
    c.execute("DELETE FROM site_paired_devices")
    c.execute(
        "UPDATE site_forward_cursor SET forwarded_to_seq = 0, cloud_pull_seq = 0 WHERE id = 1"
    )
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _event() -> dict:
    return {
        "id": str(uuid.uuid4()), "entity_type": "sale", "entity_id": str(uuid.uuid4()),
        "event_type": "create", "payload": {"amount": 1},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _seed_events(conn, n, origin="dev-a"):
    events = [_event() for _ in range(n)]
    store.append_events(conn, events, origin_device_id=origin)
    conn.commit()
    return events


def _pair_device(conn, installation_id, last_seq):
    store.pair_device(conn, installation_id, device_public_key="pk")
    store.advance_device_cursor(conn, installation_id, last_seq)
    conn.commit()


# ── P1: forwarder at 100 + a device at 100, extra_floors=[5] -> watermark 5,
#        rows up to 5 pruned ────────────────────────────────────────────────

def test_p1_extra_floor_pins_watermark_below_forwarder_and_device(conn):
    _seed_events(conn, 10)
    conn.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 100 WHERE id = 1")
    _pair_device(conn, "device-1", 100)
    conn.commit()

    watermark = pruning.safe_prune_watermark(conn, extra_floors=[5])
    assert watermark == 5

    pruned = pruning.prune_site_log(conn, watermark=watermark)
    remaining = conn.execute("SELECT seq FROM site_sync_events ORDER BY seq").fetchall()
    assert all(r["seq"] > 5 for r in remaining)
    assert pruned == 5


# ── P2: extra_floors=[0] prunes 0 ────────────────────────────────────────────

def test_p2_extra_floor_zero_prunes_nothing(conn):
    _seed_events(conn, 10)
    conn.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 100 WHERE id = 1")
    conn.commit()

    watermark = pruning.safe_prune_watermark(conn, extra_floors=[0])
    assert watermark == 0
    pruned = pruning.prune_site_log(conn, watermark=watermark)
    assert pruned == 0


# ── P3: no extra_floors gives the identical result as today (regression
#        guard against changing the default) ───────────────────────────────

def test_p3_no_extra_floors_matches_pre_existing_behaviour(conn):
    _seed_events(conn, 10)
    conn.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 6 WHERE id = 1")
    conn.commit()

    with_default = pruning.safe_prune_watermark(conn)
    with_empty = pruning.safe_prune_watermark(conn, extra_floors=())
    assert with_default == with_empty == 6


# ── P4: run_housekeeping_sweep with a provider returning [3] prunes only
#        up to 3 ────────────────────────────────────────────────────────────

def test_p4_sweep_uses_consumer_floor_provider(conn):
    _seed_events(conn, 10)
    conn.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 100 WHERE id = 1")
    conn.commit()

    result = listener.run_housekeeping_sweep(
        get_conn=_fresh_conn, nonce_ttl_seconds=3600,
        consumer_floor_provider=lambda: [3],
    )
    assert result is not None
    assert result["events_pruned"] == 3
    remaining = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    assert remaining == 7


# ── P5: a provider that raises gives None, 0 pruned, one WARNING ───────────

def test_p5_provider_raising_gives_none_and_one_warning(conn, caplog):
    _seed_events(conn, 5)
    caplog.set_level(logging.WARNING)

    def _boom():
        raise RuntimeError("registry.db is locked")

    result = listener.run_housekeeping_sweep(
        get_conn=_fresh_conn, nonce_ttl_seconds=3600,
        consumer_floor_provider=_boom,
    )
    assert result is None
    remaining = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    assert remaining == 5  # nothing pruned
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
