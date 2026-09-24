"""Tests for `commercial_runtime.sync.site_relay.local_client` -- the
in-process client a hub's OWN two `SyncService` instances use to read and
write their OWN site log, instead of talking to Owner's cloud relay.

THE DEFECT THIS MODULE CLOSES (measured on real hardware 2026-09-24, see
this task's own plan section 0 and app.py's `_enter_hub_self_sync`
docstring): a Windows till that becomes the LAN hub kept pushing/pulling
Owner's cloud directly -- its own books and the LAN site log lived in two
disconnected worlds, so a phone's sales never reached the desktop and a
desktop-created product never reached the phone.

Same real-schema bootstrap convention as `test_site_relay_forwarder.py` in
this same directory (see that file's own module docstring for the full
reasoning): this file runs the REAL migration chain, not hand-written DDL.
Run this file on its own, one process per file:

    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_local_client.py -q --color=no
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ── Real-schema bootstrap (mirrors test_site_relay_forwarder.py verbatim) ──
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DATA_DIR = Path(tempfile.mkdtemp(prefix="site_relay_local_client_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.sync.site_relay import local_client, store  # noqa: E402


def teardown_module(module):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


retail_schema.init_retail()
_DB_PATH = retail_schema._get_path("retail")

HUB_ID = "hub-installation-1"


def _fresh_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


@pytest.fixture
def conn():
    c = _fresh_conn()
    c.execute("DELETE FROM site_sync_events")
    c.execute("DELETE FROM site_device_cursors")
    c.execute(
        "UPDATE site_forward_cursor SET forwarded_to_seq = 0, cloud_pull_seq = 0 WHERE id = 1"
    )
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


# ── L1: push stores rows under the hub's own origin, visible from a second
#        connection ──────────────────────────────────────────────────────

def test_l1_push_stores_rows_under_hub_origin_visible_from_second_connection(conn):
    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID)
    events = [_event(), _event()]

    result = client.push(events)

    assert result == {"stored": 2, "received": 2}
    rows = conn.execute(
        "SELECT id, origin_device_id FROM site_sync_events ORDER BY seq"
    ).fetchall()
    assert [r["id"] for r in rows] == [e["id"] for e in events]
    assert all(r["origin_device_id"] == HUB_ID for r in rows)


# ── L2: re-pushing the same batch stores 0, seq unchanged ──────────────────

def test_l2_repushing_same_batch_stores_zero_and_seq_unchanged(conn):
    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID)
    events = [_event(), _event()]
    client.push(events)
    before = conn.execute("SELECT MAX(seq) FROM site_sync_events").fetchone()[0]

    result = client.push(events)

    assert result == {"stored": 0, "received": 2}
    after = conn.execute("SELECT MAX(seq) FROM site_sync_events").fetchone()[0]
    assert after == before


# ── L3: pull excludes the hub's own rows; includes phone + UPSTREAM rows,
#        oldest first; cursor clamps correctly ─────────────────────────────

def test_l3_pull_excludes_own_rows_includes_phone_and_upstream_oldest_first(conn):
    own_event = _event()
    phone_event = _event()
    upstream_event = _event()
    store.append_events(conn, [own_event], origin_device_id=HUB_ID)
    store.append_events(conn, [phone_event], origin_device_id="phone-1")
    store.append_events(conn, [upstream_event], origin_device_id=store.UPSTREAM_ORIGIN)
    conn.commit()

    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID)
    result = client.pull(since=0)

    ids = [e["id"] for e in result["events"]]
    assert own_event["id"] not in ids
    assert ids == [phone_event["id"], upstream_event["id"]]
    max_seq = conn.execute("SELECT MAX(seq) FROM site_sync_events").fetchone()[0]
    assert result["cursor"] == max_seq


def test_l3_empty_pull_clamps_to_min_of_since_and_max(conn):
    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID)
    result = client.pull(since=999)
    assert result == {"events": [], "cursor": 0}


# ── L4: pull is read-only -- no writes on the client's own connection, and
#        no site_device_cursors row is ever created for the hub ───────────

def test_l4_pull_is_read_only_no_writes_no_device_cursor_row(conn):
    store.append_events(conn, [_event()], origin_device_id="phone-1")
    conn.commit()

    # A thin proxy so the test can read `total_changes` at the moment the
    # client calls `.close()` -- AFTER that point the real sqlite3
    # connection is gone and `total_changes` cannot be read at all (a
    # ProgrammingError), so recording it exactly at close time is the only
    # way to observe "how many writes happened on this connection, ever"
    # from outside `pull()` itself.
    class _ClosingSpyConn:
        def __init__(self, real):
            self._real = real
            self.total_changes_at_close = None

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            self.total_changes_at_close = self._real.total_changes
            self._real.close()

    spy_conns = []

    def _spy_get_conn():
        c = _ClosingSpyConn(_fresh_conn())
        spy_conns.append(c)
        return c

    client = local_client.LocalSiteRelayClient(get_conn=_spy_get_conn, origin_device_id=HUB_ID)
    result = client.pull(since=0)
    assert result["events"]  # sanity: something was actually pulled

    assert len(spy_conns) == 1
    used_conn = spy_conns[0]
    # total_changes counts INSERT/UPDATE/DELETE statements executed on this
    # connection since it was opened -- a pure SELECT leaves it at 0. This is
    # the mutation-proof surface for M2 (an accidental
    # store.advance_device_cursor / commit call in pull()).
    assert used_conn.total_changes_at_close == 0

    cursor_row = conn.execute(
        "SELECT 1 FROM site_device_cursors WHERE installation_id = ?", (HUB_ID,)
    ).fetchone()
    assert cursor_row is None


# ── L5: no origin -- both push and pull raise NO_INSTALLATION_ID, nothing
#        written ───────────────────────────────────────────────────────────

def test_l5_push_with_no_origin_raises_and_writes_nothing(conn):
    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=None)
    with pytest.raises(local_client.LocalSiteRelayError) as excinfo:
        client.push([_event()])
    assert excinfo.value.reason_code == "NO_INSTALLATION_ID"
    count = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    assert count == 0


def test_l5_pull_with_no_origin_raises(conn):
    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=None)
    with pytest.raises(local_client.LocalSiteRelayError) as excinfo:
        client.pull(since=0)
    assert excinfo.value.reason_code == "NO_INSTALLATION_ID"


# ── L6: an invalid event at index 1 writes nothing; a too-large batch
#        raises INVALID_BATCH ───────────────────────────────────────────────

def test_l6_invalid_event_at_index_writes_nothing(conn):
    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID)
    good = _event()
    bad = {"id": "x"}  # missing required fields
    with pytest.raises(local_client.LocalSiteRelayError) as excinfo:
        client.push([good, bad])
    assert excinfo.value.reason_code == "INVALID_EVENT"
    assert "1" in str(excinfo.value)
    count = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    assert count == 0


def test_l6_batch_over_max_raises_invalid_batch(conn):
    client = local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID)
    too_many = [_event() for _ in range(store.MAX_PUSH_BATCH + 1)]
    with pytest.raises(local_client.LocalSiteRelayError) as excinfo:
        client.push(too_many)
    assert excinfo.value.reason_code == "INVALID_BATCH"
    count = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    assert count == 0


# ── L7: RelaySelector ───────────────────────────────────────────────────────

def test_l7_selector_defaults_to_cloud_factory():
    cloud_sentinel = object()
    selector = local_client.RelaySelector(
        cloud_factory=lambda: cloud_sentinel,
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    assert selector() is cloud_sentinel


def test_l7_use_local_switches_to_local_client_with_correct_base_url():
    selector = local_client.RelaySelector(
        cloud_factory=lambda: object(),
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    first_flip = selector.use_local()
    second_flip = selector.use_local()

    assert first_flip is True
    assert second_flip is False
    built = selector()
    assert isinstance(built, local_client.LocalSiteRelayClient)
    assert built.base_url == local_client.LOCAL_RELAY_URL


def test_l7_no_cloud_and_not_local_raises_no_relay_configured():
    selector = local_client.RelaySelector(cloud_factory=None, local_factory=lambda: object())
    with pytest.raises(local_client.LocalSiteRelayError) as excinfo:
        selector()
    assert excinfo.value.reason_code == "NO_RELAY_CONFIGURED"


# ── L8: consumer_floor ──────────────────────────────────────────────────────

def test_l8_consumer_floor_zero_for_cloud_relay_url(conn):
    conn.execute(
        "UPDATE sync_cursor SET last_seq = 77, relay_url = ? WHERE id = 1",
        ("https://cloud.example",),
    )
    conn.commit()
    assert local_client.consumer_floor(conn) == 0


def test_l8_consumer_floor_last_seq_for_local_relay_url(conn):
    conn.execute(
        "UPDATE sync_cursor SET last_seq = 42, relay_url = ? WHERE id = 1",
        (local_client.LOCAL_RELAY_URL,),
    )
    conn.commit()
    assert local_client.consumer_floor(conn) == 42
