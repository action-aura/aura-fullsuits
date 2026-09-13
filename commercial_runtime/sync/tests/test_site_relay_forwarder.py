"""Tests for `commercial_runtime.sync.site_relay.forwarder` -- the
hub-to-cloud bridge for the LAN site relay (retail schema v30,
`products/retail/backend/database/schema.py`'s `_migrate_add_site_relay`;
see `docs/launch-readiness/lan-restaurant-design.md` sec6 for the design
this module implements).

Same real-schema bootstrap convention as `test_site_relay_store.py` in this
same directory (see that file's own module docstring for the full
reasoning): this file builds its database by running the REAL migration
chain (`database.schema.init_retail()`), not hand-written DDL, so a test
here proves something about the schema that actually ships. Run this file
on its own, one process per file:

    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_forwarder.py -q --color=no

Deliberately NOT using a real HTTP server or the real `SyncRelayClient`
(unlike `test_site_relay_pinning.py`'s real-TLS-server approach, reserved
there because the transport layer IS the thing under test): the cloud
wire protocol itself is already covered by `test_relay_client.py`
(client-level, mirroring `licensing_contracts/tests/test_client.py`'s
FakeSession idiom) and by Owner's own route-level suite. What THIS file
tests is the forwarder's cursor bookkeeping and echo-guard logic, so
`FakeCloudClient` below is a queue-of-canned-results fake at the
`push`/`pull` call boundary -- the same shape `test_relay_client.py`'s
`FakeSession` uses one layer down. The SITE side of every test is the
real `store.py` DAO against the real schema; only the CLOUD side is
faked.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ── Real-schema bootstrap (see module docstring; mirrors
# test_site_relay_store.py's own identical setup verbatim) ──────────────────
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# MUST be set before database.schema is ever imported -- see
# test_site_relay_store.py's identical comment for why (schema.py reads
# this env var at import time; without it, migration_safety's live-backup
# mechanism would write real backup files into this repo's own working
# tree).
_DATA_DIR = Path(tempfile.mkdtemp(prefix="site_relay_forwarder_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.sync.relay_client import NetworkError  # noqa: E402
from commercial_runtime.sync.site_relay import forwarder, store  # noqa: E402


def teardown_module(module):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


# Runs the REAL v1 -> v30 migration chain exactly once for this whole file --
# see test_site_relay_store.py's own "STOP CONDITION" paragraph: if
# `_migrate_add_site_relay` (or `site_forward_cursor` specifically) is
# missing, THIS line is where that surfaces, loudly, for every test at once.
retail_schema.init_retail()
_DB_PATH = retail_schema._get_path("retail")


def _fresh_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


@pytest.fixture
def conn():
    """One real-schema connection per test, with `site_sync_events` cleared
    and `site_forward_cursor` reset to its post-migration seed of (0, 0).

    `site_forward_cursor` is a single CHECK(id = 1) row that the migration
    seeds with `INSERT OR IGNORE` -- it is never deleted between tests
    (there is nothing to delete-and-reseed; the row always exists once
    `init_retail()` has run once for the whole file), so isolation for it
    is an explicit UPDATE back to (0, 0) rather than a DELETE, mirroring
    how `test_site_relay_store.py`'s own `conn` fixture clears each table
    the direct way rather than by some tenant column that does not exist
    on any of these tables."""
    c = _fresh_conn()
    c.execute("DELETE FROM site_sync_events")
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


class FakeCloudClient:
    """Queue-of-canned-results fake standing in for
    `relay_client.SyncRelayClient`, matching this module's own
    `_CloudRelayClient` Protocol (`push`/`pull`) and
    `test_relay_client.py`'s FakeSession queue idiom: each queued item is
    either an `Exception` instance (raised) or a dict (returned) for the
    corresponding call, consumed in order. Records every call's arguments
    for assertion. A queue that runs out raises `IndexError` -- a test
    that calls `push`/`pull` more times than it queued responses for is a
    test bug, and a loud IndexError surfaces that immediately rather than
    silently returning some made-up default.
    """

    def __init__(self, push_queue=None, pull_queue=None):
        self.push_calls = []  # list of `events` lists, one per call
        self.pull_calls = []  # list of `since` values, one per call
        self._push_queue = list(push_queue) if push_queue is not None else None
        self._pull_queue = list(pull_queue) if pull_queue is not None else None

    def push(self, events):
        self.push_calls.append(events)
        if self._push_queue is not None:
            item = self._push_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return {"stored": len(events), "received": len(events)}

    def pull(self, since):
        self.pull_calls.append(since)
        if self._pull_queue is not None:
            item = self._pull_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return {"events": [], "cursor": since}


# ── forward_up: pushes local events, advances the watermark ─────────────────

def test_forward_up_pushes_local_events_and_advances_forwarded_to_seq(conn):
    e1, e2 = _event(), _event()
    store.append_events(conn, [e1, e2], origin_device_id="dev-a")
    conn.commit()

    client = FakeCloudClient()
    forwarded = forwarder.forward_up(conn, client)

    assert forwarded == 2
    assert len(client.push_calls) == 1
    assert [e["id"] for e in client.push_calls[0]] == [e1["id"], e2["id"]]

    forwarded_to_seq, cloud_pull_seq = forwarder.read_forward_cursor(conn)
    # The last pushed row's own site-log seq, not merely "some positive
    # number" -- pins the exact watermark value forward_up must land on.
    last_seq = conn.execute(
        "SELECT MAX(seq) FROM site_sync_events WHERE id IN (?, ?)", (e1["id"], e2["id"])
    ).fetchone()[0]
    assert forwarded_to_seq == last_seq
    assert cloud_pull_seq == 0  # forward_up must never touch the downstream half


# ── forward_up: THE ECHO GUARD (mutation-proved below) ───────────────────────

def test_forward_up_echo_guard_never_pushes_upstream_origin_rows(conn):
    """Seeds the site log with rows carrying `store.UPSTREAM_ORIGIN` as
    their origin -- exactly what `pull_down` stamps on a cloud-pulled row
    (see that function's own test further down). `forward_up` must push
    NONE of them: forwarding a cloud-originated row back to the cloud
    under the hub's own signature is the unbounded echo loop
    `origin_device_id` exists to prevent (see forwarder.py's own module
    docstring, "THE ECHO GUARD").

    MUTATION-PROVED (see this task's own report): removing
    `exclude_device_id=store.UPSTREAM_ORIGIN` from forward_up's
    `store.read_events_since(...)` call makes the assert below go RED (the
    fake client would receive both upstream rows) -- restoring the
    argument turns it back GREEN."""
    upstream_events = [_event(), _event()]
    store.append_events(conn, upstream_events, origin_device_id=store.UPSTREAM_ORIGIN)
    conn.commit()

    client = FakeCloudClient()
    forwarded = forwarder.forward_up(conn, client)

    assert forwarded == 0
    assert client.push_calls == []  # push() was never even called -- nothing forwardable


def test_forward_up_mixed_log_forwards_only_locally_originated_rows(conn):
    """A log containing both local and upstream-origin rows, interleaved,
    must forward only the local ones -- the realistic shape of a hub's
    site log once it has both received LAN pushes and pulled from the
    cloud at least once."""
    local_1 = _event()
    upstream_1 = _event()
    local_2 = _event()
    store.append_events(conn, [local_1], origin_device_id="dev-a")
    store.append_events(conn, [upstream_1], origin_device_id=store.UPSTREAM_ORIGIN)
    store.append_events(conn, [local_2], origin_device_id="dev-a")
    conn.commit()

    client = FakeCloudClient()
    forwarded = forwarder.forward_up(conn, client)

    assert forwarded == 2
    pushed_ids = [e["id"] for e in client.push_calls[0]]
    assert pushed_ids == [local_1["id"], local_2["id"]]
    assert upstream_1["id"] not in pushed_ids


# ── forward_up: failure leaves the cursor alone (mutation-proved below) ─────

def test_forward_up_failure_leaves_cursor_unchanged_and_offers_same_events_next_time(conn):
    """The data-loss guard: if `cloud_client.push` raises, `forwarded_to_seq`
    must be UNTOUCHED, and the identical events must be offered again on
    the very next call -- nothing was lost just because one attempt failed
    to reach the cloud.

    MUTATION-PROVED (see this task's own report): moving
    `_advance_forwarded_to(...)` to BEFORE `cloud_client.push(events)` in
    forward_up makes the second assert below go RED (the retry would push
    zero events, since the cursor already claims to be caught up) --
    restoring the original order turns it back GREEN."""
    e1, e2 = _event(), _event()
    store.append_events(conn, [e1, e2], origin_device_id="dev-a")
    conn.commit()

    failing_client = FakeCloudClient(push_queue=[NetworkError("NETWORK_UNAVAILABLE", "offline")])
    with pytest.raises(NetworkError):
        forwarder.forward_up(conn, failing_client)

    forwarded_to_seq, _cloud_pull_seq = forwarder.read_forward_cursor(conn)
    assert forwarded_to_seq == 0  # cursor never moved

    retry_client = FakeCloudClient()
    forwarded = forwarder.forward_up(conn, retry_client)
    assert forwarded == 2
    assert [e["id"] for e in retry_client.push_calls[0]] == [e1["id"], e2["id"]]


# ── forward_up: preserves original ids/created_at (mutation-proved below) ──

def test_forward_up_preserves_original_event_ids_and_created_at(conn):
    """Re-minting a fresh id per event, or rewriting `created_at` to the
    hub's own clock, would defeat the cloud relay's id-based dedup and
    discard the origin device's only ordering information respectively
    (see forward_up's own docstring) -- asserted here on the EXACT values
    the fake client received, not merely a count.

    MUTATION-PROVED (see this task's own report): replacing
    `"id": row["id"]` with a freshly generated `str(uuid.uuid4())` in
    forward_up's event-shaping loop makes the id assert below go RED --
    restoring the original value turns it back GREEN."""
    created_at = "2026-09-01T10:00:00+00:00"
    event = _event(created_at=created_at)
    store.append_events(conn, [event], origin_device_id="dev-a")
    conn.commit()

    client = FakeCloudClient()
    forwarder.forward_up(conn, client)

    pushed = client.push_calls[0]
    assert len(pushed) == 1
    assert pushed[0]["id"] == event["id"]
    assert pushed[0]["created_at"] == created_at
    assert pushed[0]["entity_type"] == event["entity_type"]
    assert pushed[0]["entity_id"] == event["entity_id"]
    assert pushed[0]["event_type"] == event["event_type"]
    assert pushed[0]["payload"] == event["payload"]


# ── pull_down: applies, delivers to the LAN, advances the watermark ─────────

def test_pull_down_applies_events_inserts_into_site_log_and_advances_cursor(conn):
    pulled = [_event(entity_type="product"), _event(entity_type="customer")]
    client = FakeCloudClient(pull_queue=[{"events": pulled, "cursor": 42}])
    applied_batches = []

    def fake_apply(events):
        applied_batches.append(events)

    n = forwarder.pull_down(conn, client, apply_pulled=fake_apply)

    assert n == 2
    assert applied_batches == [pulled]  # the injected callable received the exact batch

    rows = conn.execute(
        "SELECT id, origin_device_id FROM site_sync_events ORDER BY seq"
    ).fetchall()
    assert [r["id"] for r in rows] == [e["id"] for e in pulled]
    assert all(r["origin_device_id"] == store.UPSTREAM_ORIGIN for r in rows)

    _forwarded_to_seq, cloud_pull_seq = forwarder.read_forward_cursor(conn)
    assert cloud_pull_seq == 42


def test_pull_down_zero_rows_still_advances_cursor_to_cloud_clamp(conn):
    """Owner's own zero-rows clamp can hand back a corrected cursor even
    with no events (e.g. a hub recovering from a stale/over-large `since`)
    -- pull_down must still persist that corrected value so the hub
    self-heals rather than repeating the same corrected pull forever."""
    client = FakeCloudClient(pull_queue=[{"events": [], "cursor": 5}])
    n = forwarder.pull_down(conn, client, apply_pulled=lambda events: None)

    assert n == 0
    _forwarded_to_seq, cloud_pull_seq = forwarder.read_forward_cursor(conn)
    assert cloud_pull_seq == 5


# ── Round trip: no echo loop (composes the echo-guard + pull_down tests) ────

def test_round_trip_pull_then_forward_produces_no_echo_loop(conn):
    """The property the whole design in lan-restaurant-design.md sec6
    depends on: once `pull_down` has delivered cloud events onto the site
    log, a subsequent `forward_up` must push NONE of them back up. This is
    tests `test_forward_up_echo_guard_never_pushes_upstream_origin_rows`
    and `test_pull_down_applies_events_inserts_into_site_log_and_advances_cursor`
    composed end to end, asserted explicitly rather than left implicit."""
    pulled = [_event(), _event()]
    pull_client = FakeCloudClient(pull_queue=[{"events": pulled, "cursor": 7}])
    forwarder.pull_down(conn, pull_client, apply_pulled=lambda events: None)

    forward_client = FakeCloudClient()
    forwarded = forwarder.forward_up(conn, forward_client)

    assert forwarded == 0
    assert forward_client.push_calls == []


# ── pull_down: failure rolls back everything (mutation would break this) ───

def test_pull_down_apply_failure_leaves_cursor_and_site_log_unchanged(conn):
    """The `apply_pulled` half of the "one transaction, all or none"
    guarantee: an exception from the injected callable must roll back the
    site-log insert AND leave `cloud_pull_seq` exactly where it was, so
    the identical range is re-pulled next tick rather than silently
    skipped."""
    pulled = [_event()]
    client = FakeCloudClient(pull_queue=[{"events": pulled, "cursor": 9}])

    def failing_apply(events):
        raise RuntimeError("apply_pull_result exploded")

    with pytest.raises(RuntimeError):
        forwarder.pull_down(conn, client, apply_pulled=failing_apply)

    _forwarded_to_seq, cloud_pull_seq = forwarder.read_forward_cursor(conn)
    assert cloud_pull_seq == 0
    row_count = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    assert row_count == 0


# ── run_once: independent try/except halves ─────────────────────────────────

def test_run_once_reports_push_error_but_still_performs_the_pull(conn):
    """`run_once` must never let a push failure block the pull half -- a
    stuck outbox (e.g. one persistently malformed local event) must not
    stop this hub from continuing to receive OTHER devices' updates via
    the cloud."""
    store.append_events(conn, [_event()], origin_device_id="dev-a")
    conn.commit()

    class PushFailsPullSucceeds:
        def __init__(self):
            self.push_calls = []
            self.pull_calls = []

        def push(self, events):
            self.push_calls.append(events)
            raise NetworkError("NETWORK_UNAVAILABLE", "offline, full URL redacted here")

        def pull(self, since):
            self.pull_calls.append(since)
            return {"events": [], "cursor": since}

    client = PushFailsPullSucceeds()
    result = forwarder.run_once(conn, client, apply_pulled=lambda events: None)

    assert result == {
        "forwarded": 0,
        "pulled": 0,
        "push_error": "NETWORK_UNAVAILABLE",
        "pull_error": None,
    }
    assert len(client.pull_calls) == 1  # pull was still attempted despite the push failure


def test_run_once_reports_pull_error_but_still_performed_the_push(conn):
    store.append_events(conn, [_event()], origin_device_id="dev-a")
    conn.commit()

    class PushSucceedsPullFails:
        def __init__(self):
            self.push_calls = []
            self.pull_calls = []

        def push(self, events):
            self.push_calls.append(events)
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            self.pull_calls.append(since)
            raise NetworkError("NETWORK_UNAVAILABLE", "offline")

    client = PushSucceedsPullFails()
    result = forwarder.run_once(conn, client, apply_pulled=lambda events: None)

    assert result["forwarded"] == 1
    assert result["push_error"] is None
    assert result["pulled"] == 0
    assert result["pull_error"] == "NETWORK_UNAVAILABLE"
    assert len(client.push_calls) == 1  # push still completed despite the pull failure
