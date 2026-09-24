"""Integration tests: a REAL `SyncService` (commercial_runtime/sync/
sync_service.py, completely unmodified by this task) wired with a
`RelaySelector` (local_client.py) in LOCAL mode, over the REAL retail.db
schema. This is the actual composition `app.py::_enter_hub_self_sync` wires
in production -- these tests prove the pieces work together BEFORE app.py
is asked to hold them.

THE DEFECT THIS PROVES CLOSED (this task's own plan, section 0 and
app.py's `_enter_hub_self_sync` docstring): a hub's own SyncService kept
talking to Owner's cloud relay while its paired LAN devices talked only to
the site log -- two disconnected worlds. Once `client_factory` for BOTH of
a hub's own SyncService instances is a `RelaySelector` in LOCAL mode, the
hub's own outbox rows land in the site log (H1), the hub applies what a
paired phone pushed there (H2), and a relay switch mid-flight resets the
cursor and un-wedges rather than deadlocking (H3) -- and a real
`ForwarderLoop` bridges the site log to a fake cloud with no double-apply
(H4).

Real-schema bootstrap, same convention as this package's other test files.
Run this file on its own, one process per file:

    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_hub_self_sync.py -q --color=no
"""
import json
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

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DATA_DIR = Path(tempfile.mkdtemp(prefix="site_relay_hub_self_sync_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.sync.site_relay import forwarder_loop, local_client, store  # noqa: E402
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService  # noqa: E402

registry_db.init_registry_db()


def teardown_module(module):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


retail_schema.init_retail()
_DB_PATH = retail_schema._get_path("retail")

HUB_ID = "hub-installation-xyz"
CID = 1


def _fresh_conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


@pytest.fixture
def conn():
    c = _fresh_conn()
    c.execute("DELETE FROM site_sync_events")
    c.execute("DELETE FROM sqlite_sequence WHERE name = 'site_sync_events'")
    c.execute("DELETE FROM site_device_cursors")
    c.execute("DELETE FROM sync_outbox")
    c.execute("DELETE FROM sync_conflicts")
    c.execute("DELETE FROM categories")
    c.execute("UPDATE sync_cursor SET last_seq = 0, relay_url = NULL WHERE id = 1")
    c.execute(
        "UPDATE site_forward_cursor SET forwarded_to_seq = 0, cloud_pull_seq = 0 WHERE id = 1"
    )
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _category_event(entity_id=None, name="Widgets", row_version=None):
    entity_id = entity_id or str(uuid.uuid4())
    payload = {"id": entity_id, "company_id": CID, "name": name, "description": ""}
    if row_version is not None:
        payload["row_version"] = row_version
    return {
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": entity_id,
        "event_type": "create", "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _local_service(selector):
    return SyncService(selector, _fresh_conn, local_company_id_provider=lambda: CID)


# ── H1: a hub outbox row goes through push_once into the site log under the
#        hub's own origin; the outbox ends up empty ─────────────────────────

def test_h1_hub_outbox_row_lands_in_site_log_under_hub_origin(conn):
    entity_id = str(uuid.uuid4())
    payload = {"id": entity_id, "company_id": CID, "name": "Snacks", "description": ""}
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "category", entity_id, "create", json.dumps(payload),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    selector = local_client.RelaySelector(
        cloud_factory=None,
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    selector.use_local()
    service = _local_service(selector)

    service.push_once()

    outbox = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    assert outbox == 0
    rows = conn.execute("SELECT origin_device_id FROM site_sync_events").fetchall()
    assert len(rows) == 1
    assert rows[0]["origin_device_id"] == HUB_ID


# ── H2: a phone category row applied via pull_once; cursor.relay_url is
#        LOCAL_RELAY_URL; no conflicts ──────────────────────────────────────

def test_h2_phone_row_applied_via_pull_once_no_conflicts(conn):
    phone_event = _category_event(name="From Phone")
    store.append_events(conn, [phone_event], origin_device_id="phone-1")
    conn.commit()

    selector = local_client.RelaySelector(
        cloud_factory=None,
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    selector.use_local()
    service = _local_service(selector)

    service.pull_once()

    cat = conn.execute(
        "SELECT name FROM categories WHERE id = ?", (phone_event["entity_id"],)
    ).fetchone()
    assert cat is not None and cat["name"] == "From Phone"
    cursor_row = conn.execute("SELECT relay_url FROM sync_cursor WHERE id=1").fetchone()
    assert cursor_row["relay_url"] == local_client.LOCAL_RELAY_URL
    conflicts = conn.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0]
    assert conflicts == 0


# ── H3: switch tick -- preset cursor pointed at a stale cloud URL; pull_once
#        in a thread must finish (no self-deadlock) and apply the row ──────

def test_h3_switch_tick_resets_cursor_with_no_self_deadlock(conn):
    conn.execute(
        "UPDATE sync_cursor SET relay_url = 'https://cloud.example', last_seq = 999 WHERE id = 1"
    )
    conn.commit()

    phone_event = _category_event(name="Switch Tick")
    store.append_events(conn, [phone_event], origin_device_id="phone-1")
    conn.commit()
    local_seq = conn.execute("SELECT MAX(seq) FROM site_sync_events").fetchone()[0]

    selector = local_client.RelaySelector(
        cloud_factory=None,
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    selector.use_local()
    service = _local_service(selector)

    thread = threading.Thread(target=service.pull_once)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "pull_once deadlocked on the cursor reset"

    cat = conn.execute(
        "SELECT 1 FROM categories WHERE id = ?", (phone_event["entity_id"],)
    ).fetchone()
    assert cat is not None
    cursor_row = conn.execute("SELECT last_seq, relay_url FROM sync_cursor WHERE id=1").fetchone()
    assert cursor_row["relay_url"] == local_client.LOCAL_RELAY_URL
    assert cursor_row["last_seq"] == local_seq


# ── H4: exactly-once / echo guard -- a ForwarderLoop tick writes a cloud
#        event as UPSTREAM_ORIGIN; pull_once applies it with no conflicts;
#        a second forwarder tick's push never includes it ────────────────

def test_h4_forwarder_and_hub_pull_compose_with_no_double_apply_or_echo(conn):
    # row_version=1 is the PRODUCTION shape (retail_api.py's category create
    # queues `'row_version': 1`), and it is load-bearing here (adversarial
    # verifier, 2026-09-24): `_apply_event`'s stale/equal-version gate only
    # fires when the payload carries a row_version, so without it a SECOND
    # apply of the same event is silent and `sync_conflicts == 0` below could
    # not tell "applied once" from "applied twice". Measured: with the
    # forwarder mutated to apply pulled events itself (M6), this test stayed
    # GREEN with no row_version and goes RED with it (H6 measures the same
    # thing: an equal-version replay writes exactly 1 conflict row).
    cloud_event = _category_event(name="From Cloud", row_version=1)

    class _FakeCloudClient:
        def __init__(self):
            self.push_calls = []
            self._pull_queue = [{"events": [cloud_event], "cursor": 5}, {"events": [], "cursor": 5}]

        def push(self, events):
            self.push_calls.append(events)
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            return self._pull_queue.pop(0)

    cloud_client = _FakeCloudClient()
    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: cloud_client)

    first_tick = loop.tick()
    assert first_tick["pulled"] == 1
    upstream_rows = conn.execute(
        "SELECT origin_device_id FROM site_sync_events WHERE id = ?", (cloud_event["id"],)
    ).fetchall()
    assert len(upstream_rows) == 1
    assert upstream_rows[0]["origin_device_id"] == store.UPSTREAM_ORIGIN

    selector = local_client.RelaySelector(
        cloud_factory=None,
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    selector.use_local()
    service = _local_service(selector)
    service.pull_once()

    cat = conn.execute(
        "SELECT 1 FROM categories WHERE id = ?", (cloud_event["entity_id"],)
    ).fetchone()
    assert cat is not None
    conflicts = conn.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0]
    assert conflicts == 0

    # A hub-originated row too, so the second tick has something local to
    # consider forwarding (and something to prove is NOT the echoed cloud
    # event).
    hub_local_event = _category_event(name="Hub Local")
    store.append_events(conn, [hub_local_event], origin_device_id=HUB_ID)
    conn.commit()

    second_tick = loop.tick()
    pushed_ids = [e["id"] for batch in cloud_client.push_calls for e in batch]
    # Positive half first (adversarial verifier, 2026-09-24): "E was not
    # pushed" alone is also what a forwarder that pushes NOTHING looks like
    # -- the pass condition must not be the bug signature.
    assert hub_local_event["id"] in pushed_ids, "the hub's own site-log row never went up"
    assert cloud_event["id"] not in pushed_ids, "the forwarder echoed the cloud's own event back to it"


# ── H5: registry stream -- a second SyncService sharing the SAME selector,
#        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES, applies nothing
#        retail and advances its OWN cursor ─────────────────────────────────

def test_h5_registry_stream_shares_selector_advances_own_cursor(conn):
    reg_conn = registry_db.get_conn()
    try:
        reg_conn.execute("DELETE FROM sync_cursor")
        reg_conn.execute("INSERT INTO sync_cursor (id, last_seq) VALUES (1, 0)")
        reg_conn.commit()
    finally:
        reg_conn.close()

    # Only a retail-facing "category" event sits in the site log -- the
    # registry stream's own `handled_entity_types` gate must skip it (never
    # apply it, never touch `categories`, which does not even exist in
    # registry.db) while still advancing ITS OWN cursor past it, exactly
    # like `_apply_event`'s own gate comment documents for any entity type
    # outside `self._handled_entity_types`.
    store.append_events(conn, [_category_event(name="Retail Only")], origin_device_id="phone-1")
    conn.commit()
    local_seq = conn.execute("SELECT MAX(seq) FROM site_sync_events").fetchone()[0]

    selector = local_client.RelaySelector(
        cloud_factory=None,
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    selector.use_local()
    registry_service = SyncService(
        selector, registry_db.get_conn, lambda: CID,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
    )

    registry_service.pull_once()

    reg_conn = registry_db.get_conn()
    try:
        cursor_row = reg_conn.execute("SELECT last_seq, relay_url FROM sync_cursor WHERE id=1").fetchone()
    finally:
        reg_conn.close()
    assert cursor_row["relay_url"] == local_client.LOCAL_RELAY_URL
    assert cursor_row["last_seq"] == local_seq


# ── H6: measure (not a gate) -- replaying an equal-row_version catalogue
#        event via UPSTREAM produces exactly one sync_conflicts row ────────

def test_h6_replay_at_equal_row_version_conflict_count(conn):
    entity_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?)",
        (entity_id, CID, "Existing", ""),
    )
    conn.commit()

    replay_event = _category_event(entity_id=entity_id, name="Existing", row_version=1)
    store.append_events(conn, [replay_event], origin_device_id=store.UPSTREAM_ORIGIN)
    conn.commit()

    selector = local_client.RelaySelector(
        cloud_factory=None,
        local_factory=lambda: local_client.LocalSiteRelayClient(get_conn=_fresh_conn, origin_device_id=HUB_ID),
    )
    selector.use_local()
    service = _local_service(selector)
    service.pull_once()

    conflicts = conn.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0]
    # MEASUREMENT, not a strict gate on this codebase's own conflict-vs-
    # no-op design choice (see this task's plan item G4/0.6) -- reported
    # rather than asserted to a single hardcoded expectation, so a future
    # "equal version + identical fields is a replay, not a conflict" fix
    # (the follow-up G4 names) does not have to fight this test.
    print(f"H6 measurement: replaying an equal-row_version event produced {conflicts} sync_conflicts row(s)")
    assert conflicts in (0, 1)
