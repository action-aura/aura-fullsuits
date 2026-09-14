"""Tests for `commercial_runtime.sync.site_relay.pruning` -- deciding what of
the LAN site log is safe to forget.

Same fixture approach as `test_site_relay_store.py` (read its module docstring
for the full reasoning): the database is built by running the REAL migration
chain via `database.schema.init_retail()`, the exact function app.py calls at
boot, rather than hand-written DDL -- a test against hand-written DDL cannot
catch a drift between this module's assumptions and the schema that ships.

WHAT THIS FILE IS REALLY GUARDING. Pruning has two failure modes with wildly
different costs, and almost every test here is aimed at the expensive one:

  * pruning too EAGERLY silently destroys events a device had not yet pulled.
    Its cursor is already past the deleted range, so it never asks again and
    nothing reports a gap -- data loss with no error, in a money path;
  * pruning too TIMIDLY just grows the table.

So the assertions below are overwhelmingly of the form "this must NOT be
pruned yet", and the mutation proofs target exactly the conditions whose
removal would start deleting live history.

A NOTE ON WHY EVERY HELPER HERE IS SEQ-AWARE, because the first draft of this
file was wrong in an instructive way. `site_sync_events.seq` is
`INTEGER PRIMARY KEY AUTOINCREMENT`, so numbers are never reissued -- not even
after the fixture empties the table between tests. The second test in a run
therefore starts at seq 6, not seq 1, and watermarks hardcoded as small
integers silently matched nothing. The tests expecting "nothing is pruned"
still passed, which is exactly the shape ENGINEERING.md warns about: the
harness agreeing with itself for the wrong reason. Everything below is
expressed in terms of the seqs actually allocated.

Run:
    pytest commercial_runtime/sync/tests/test_site_relay_pruning.py -v
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DATA_DIR = Path(tempfile.mkdtemp(prefix="site_relay_pruning_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.sync.site_relay import pruning, store  # noqa: E402

DESK = "11111111-1111-4111-8111-111111111111"
TABLET = "22222222-2222-4222-8222-222222222222"
OTHER = "99999999-9999-4999-8999-999999999999"


def teardown_module(module):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


retail_schema.init_retail()
_DB_PATH = retail_schema._get_path("retail")


@pytest.fixture
def conn():
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    for table in ("site_sync_events", "site_device_cursors", "site_paired_devices"):
        c.execute(f"DELETE FROM {table}")
    c.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 0, cloud_pull_seq = 0 WHERE id = 1")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _seed_events(conn, count, *, origin=OTHER):
    """Append `count` events and return the seqs they were ACTUALLY given.

    Returning the seqs rather than assuming 1..count is the whole point -- see
    the module docstring."""
    events = [{
        "id": str(uuid.uuid4()),
        "entity_type": "product",
        "entity_id": str(uuid.uuid4()),
        "event_type": "create",
        "payload": {"n": i},
        "created_at": "2026-09-14T12:00:00+00:00",
    } for i in range(count)]
    store.append_events(conn, events, origin_device_id=origin)
    conn.commit()
    ids = {e["id"] for e in events}
    rows = conn.execute("SELECT id, seq FROM site_sync_events ORDER BY seq").fetchall()
    return [r["seq"] for r in rows if r["id"] in ids]


def _set_forwarded(conn, seq):
    conn.execute("UPDATE site_forward_cursor SET forwarded_to_seq = ? WHERE id = 1", (seq,))
    conn.commit()


def _pair(conn, installation_id, *, revoked=False):
    store.pair_device(conn, installation_id, device_public_key="x" * 44, label=None)
    if revoked:
        store.revoke_device(conn, installation_id)
    conn.commit()


def _count_events(conn):
    return conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]


def test_nothing_is_pruned_when_the_forwarder_has_sent_nothing(conn):
    """The single-till case at its very first sync. Every row is still the
    only copy in existence until the forwarder gets it to Owner."""
    _seed_events(conn, 5)
    assert pruning.safe_prune_watermark(conn) == 0
    assert pruning.prune_site_log(conn) == 0
    assert _count_events(conn) == 5


def test_a_lone_hub_prunes_up_to_what_it_forwarded(conn):
    """No paired devices means no LAN consumer, so the forwarder alone
    governs. Without this branch an ordinary single-till shop would grow its
    log forever despite having no peers to serve."""
    seqs = _seed_events(conn, 5)
    _set_forwarded(conn, seqs[2])          # first three forwarded

    assert pruning.safe_prune_watermark(conn) == seqs[2]
    assert pruning.prune_site_log(conn) == 3
    assert _count_events(conn) == 2


def test_a_paired_device_that_has_never_pulled_pins_the_watermark_to_zero(conn):
    """THE EXPENSIVE CASE. A tablet paired on Monday and switched on Tuesday
    has no cursor row. Treating "no row" as anything but 0 would delete the
    entire history it was paired to receive."""
    seqs = _seed_events(conn, 5)
    _set_forwarded(conn, seqs[-1])
    _pair(conn, TABLET)

    assert pruning.safe_prune_watermark(conn) == 0
    assert pruning.prune_site_log(conn) == 0
    assert _count_events(conn) == 5


def test_the_slowest_device_governs(conn):
    seqs = _seed_events(conn, 10)
    _set_forwarded(conn, seqs[-1])
    _pair(conn, DESK)
    _pair(conn, TABLET)
    store.advance_device_cursor(conn, DESK, seqs[7])
    store.advance_device_cursor(conn, TABLET, seqs[3])   # slowest: 4 events taken
    conn.commit()

    assert pruning.safe_prune_watermark(conn) == seqs[3]
    assert pruning.prune_site_log(conn) == 4
    assert _count_events(conn) == 6


def test_the_forwarder_caps_the_watermark_even_when_devices_are_ahead(conn):
    """Both LAN devices have taken everything, but the cloud has not. Those
    rows are still the only copy that exists, so they stay."""
    seqs = _seed_events(conn, 10)
    _set_forwarded(conn, seqs[1])          # only two have reached Owner
    _pair(conn, DESK)
    store.advance_device_cursor(conn, DESK, seqs[-1])
    conn.commit()

    assert pruning.safe_prune_watermark(conn) == seqs[1]
    assert pruning.prune_site_log(conn) == 2
    assert _count_events(conn) == 8


def test_a_revoked_device_does_not_freeze_pruning_forever(conn):
    """The trap the design doc names on the cloud side. A fired employee's
    revoked tablet sits near the start forever; if it stayed in the MIN, this
    shop could never prune again for the life of the install."""
    seqs = _seed_events(conn, 10)
    _set_forwarded(conn, seqs[-1])
    _pair(conn, DESK)
    _pair(conn, TABLET, revoked=True)
    store.advance_device_cursor(conn, DESK, seqs[8])
    store.advance_device_cursor(conn, TABLET, seqs[1])
    conn.commit()

    assert pruning.safe_prune_watermark(conn) == seqs[8]


def test_pruning_never_deletes_above_the_watermark(conn):
    seqs = _seed_events(conn, 10)
    _set_forwarded(conn, seqs[3])
    pruning.prune_site_log(conn)
    conn.commit()

    remaining = [r["seq"] for r in conn.execute(
        "SELECT seq FROM site_sync_events ORDER BY seq").fetchall()]
    assert min(remaining) > seqs[3], f"pruned above the watermark: {remaining}"


def test_pruning_is_batched(conn):
    seqs = _seed_events(conn, 10)
    _set_forwarded(conn, seqs[-1])

    assert pruning.prune_site_log(conn, batch=3) == 3
    assert _count_events(conn) == 7


def test_seq_is_not_reused_after_pruning(conn):
    """The property that makes pruning safe at all. If a pruned seq were
    reissued, a device whose cursor is already past it would silently skip
    whatever event took the slot -- the same AUTOINCREMENT guarantee
    retail_site_relay_schema_test.py pins, asserted here against the pruning
    path specifically because this is the only code that deletes these rows."""
    seqs = _seed_events(conn, 5)
    _set_forwarded(conn, seqs[-1])
    pruning.prune_site_log(conn)
    conn.commit()
    assert _count_events(conn) == 0

    later = _seed_events(conn, 1)
    assert later[0] > seqs[-1], f"a pruned seq was reissued: {later[0]} after {seqs[-1]}"


def test_the_sweep_reports_what_it_actually_deleted(conn):
    """Returning real counts is what lets an operator notice pruning has been
    stuck at zero for a month, rather than just seeing 'housekeeping ran'."""
    seqs = _seed_events(conn, 6)
    _set_forwarded(conn, seqs[-1])

    result = pruning.prune_nonces_and_log(conn, nonce_ttl_seconds=1800)
    conn.commit()

    assert result["events_pruned"] == 6
    assert "nonces_pruned" in result
