"""Tests for SyncService, mirroring
commercial_runtime/licensing_contracts/tests/test_checkin_scheduler.py's
pattern: a fake at the *client* boundary (FakeRelayClient, analogous to that
file's FakeClient) rather than a fake HTTP session -- SyncRelayClient's own
transport behavior is already covered end-to-end by test_relay_client.py.
Local persistence uses a real sqlite file under tmp_path (same convention
test_checkin_scheduler.py/test_state_repository.py use for
LicenseStateRepository), built with the exact categories/sync_outbox/
sync_cursor schema products/retail/backend/database/schema.py creates.
"""
import json
import sqlite3
import threading
import time
import uuid

import pytest

from commercial_runtime.sync.relay_client import NetworkError, RelayRejected
from commercial_runtime.sync.sync_service import (
    SyncService,
    nudge,
    register_active_service,
    unregister_active_service,
)


class FakeRelayClient:
    """Queue of canned push/pull outcomes, consumed in order -- mirrors
    test_checkin_scheduler.py's FakeClient. An item that is an Exception
    instance is raised instead of returned, exactly like that fixture."""

    def __init__(self, push_responses=None, pull_responses=None):
        self._push_responses = list(push_responses or [])
        self._pull_responses = list(pull_responses or [])
        self.push_calls = []
        self.pull_calls = []

    def push(self, events):
        self.push_calls.append(events)
        item = self._push_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def pull(self, since):
        self.pull_calls.append(since)
        item = self._pull_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


_SCHEMA = """
CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL,
    description TEXT
);
CREATE TABLE sync_outbox (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
"""


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def get_conn(db_path):
    # Same connection settings as products/retail/backend/database/schema.py's
    # real _conn() -- WAL mode + a real busy_timeout so a reader/writer or
    # two overlapping writers block-and-retry instead of raising "database
    # is locked" the way a bare sqlite3.connect() default would (this
    # matters for the concurrency/nudge tests below, which deliberately run
    # two real connections against the same file at once).
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c
    return _get_conn


def _insert_outbox_row(get_conn, entity_id=None, event_type="create", payload=None):
    entity_id = entity_id or str(uuid.uuid4())
    payload = payload if payload is not None else {"id": entity_id, "company_id": 1, "name": "Widgets", "description": ""}
    conn = get_conn()
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "category", entity_id, event_type, json.dumps(payload), "2026-08-06T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return entity_id, payload


def _outbox_rows(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sync_outbox").fetchall()]
    conn.close()
    return rows


def _categories(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM categories").fetchall()]
    conn.close()
    return rows


def _cursor(get_conn):
    conn = get_conn()
    row = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
    conn.close()
    return row["last_seq"]


# ── push_once() ──────────────────────────────────────────────────────────

def test_push_once_with_empty_outbox_never_calls_the_relay(get_conn):
    client = FakeRelayClient()
    service = SyncService(lambda: client, get_conn)
    service.push_once()
    assert client.push_calls == []


def test_push_once_success_clears_the_pushed_rows(get_conn):
    _insert_outbox_row(get_conn)
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[{"stored": 2, "received": 2}])
    service = SyncService(lambda: client, get_conn)

    service.push_once()

    assert _outbox_rows(get_conn) == []
    assert len(client.push_calls) == 1
    assert len(client.push_calls[0]) == 2


def test_push_once_sends_events_with_decoded_payload(get_conn):
    entity_id, payload = _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[{"stored": 1, "received": 1}])
    service = SyncService(lambda: client, get_conn)

    service.push_once()

    sent_event = client.push_calls[0][0]
    assert sent_event["entity_id"] == entity_id
    assert sent_event["entity_type"] == "category"
    assert sent_event["event_type"] == "create"
    assert sent_event["payload"] == payload  # decoded from the stored JSON string


def test_push_once_failure_leaves_outbox_untouched_and_does_not_crash(get_conn):
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")])
    service = SyncService(lambda: client, get_conn)

    with pytest.raises(NetworkError):
        service.push_once()

    # Nothing was deleted -- the exact row that would have been pushed is
    # still there for the next attempt.
    assert len(_outbox_rows(get_conn)) == 1


def test_push_once_business_rejection_also_leaves_outbox_untouched(get_conn):
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[RelayRejected("INVALID_SIGNATURE", "bad sig")])
    service = SyncService(lambda: client, get_conn)

    with pytest.raises(RelayRejected):
        service.push_once()

    assert len(_outbox_rows(get_conn)) == 1


def test_push_once_only_deletes_rows_that_were_actually_pushed(get_conn):
    """A concurrent insert (e.g. a route committing a new category) landing
    between the SELECT and the DELETE must never be swept up by an
    unqualified DELETE -- push_once() deletes by the specific ids it read,
    never by a blanket 'DELETE FROM sync_outbox'."""
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[{"stored": 1, "received": 1}])

    real_get_conn = get_conn

    class _ConnWrapper:
        """Wraps the real connection so a second row can be inserted (by a
        different connection, simulating a concurrent writer) after the
        SELECT this push_once() call already made but before its DELETE."""

        def __init__(self, inner):
            self._inner = inner
            self._selected = False

        def execute(self, sql, params=()):
            result = self._inner.execute(sql, params)
            if sql.strip().upper().startswith("SELECT * FROM SYNC_OUTBOX") and not self._selected:
                self._selected = True
                _insert_outbox_row(real_get_conn, entity_id="concurrent-row")
            return result

        def __getattr__(self, item):
            return getattr(self._inner, item)

    def wrapped_get_conn():
        return _ConnWrapper(real_get_conn())

    service = SyncService(lambda: client, wrapped_get_conn)
    service.push_once()

    remaining = _outbox_rows(get_conn)
    assert len(remaining) == 1
    assert remaining[0]["entity_id"] == "concurrent-row"


# ── pull_once() ──────────────────────────────────────────────────────────

def _pull_event(entity_id, event_type, payload):
    return {
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": entity_id,
        "event_type": event_type, "payload": payload, "created_at": "2026-08-06T00:00:00+00:00",
    }


def test_pull_once_upserts_on_create(get_conn):
    cat_id = str(uuid.uuid4())
    event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": 1, "name": "Electronics", "description": "d"})
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 3}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1
    assert rows[0]["id"] == cat_id
    assert rows[0]["name"] == "Electronics"
    assert _cursor(get_conn) == 3


def test_pull_once_upserts_on_update_overwriting_existing_row(get_conn):
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": 1, "name": "Old Name", "description": ""})
    update_event = _pull_event(cat_id, "update", {"id": cat_id, "company_id": 1, "name": "New Name", "description": "updated"})
    client = FakeRelayClient(pull_responses=[{"events": [create_event], "cursor": 1}, {"events": [update_event], "cursor": 2}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1  # still one row, not two
    assert rows[0]["name"] == "New Name"
    assert rows[0]["description"] == "updated"
    assert _cursor(get_conn) == 2


def test_pull_once_hard_deletes_on_delete(get_conn):
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": 1, "name": "Gone Soon", "description": ""})
    delete_event = _pull_event(cat_id, "delete", {"id": cat_id})
    client = FakeRelayClient(pull_responses=[{"events": [create_event], "cursor": 1}, {"events": [delete_event], "cursor": 2}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    assert len(_categories(get_conn)) == 1
    service.pull_once()

    assert _categories(get_conn) == []
    assert _cursor(get_conn) == 2


# ── Cross-device company_id bug fix (2026-08-07) ─────────────────────────
# Confirmed live during multi-device testing: a pulled event's own
# `payload["company_id"]` is the SENDING device's company_id (derived
# locally, once, at that device's own onboarding -- see
# onboarding_routes.py::create_admin). Two devices on the SAME license,
# activated with different admin emails, have two different company_id
# values. _apply_event must stamp the RECEIVING device's own company_id
# (from local_company_id_provider) on every pulled row -- never the
# payload's -- or the row becomes permanently invisible to that device's
# own `WHERE company_id=?` queries (retail_api.py::list_categories).

def test_pull_once_stamps_the_receiving_devices_own_company_id_not_the_payloads(get_conn):
    """The exact bug found in live device testing, reproduced directly:
    device A pushed a category with ITS OWN company_id ("company-A") in the
    payload; applying that pulled event on device B (whose own company_id
    is "company-B", supplied via local_company_id_provider, entirely
    independent of the payload) must write the row under "company-B" --
    never "company-A" -- so device B's own list_categories-style
    `WHERE company_id=?` filter (reproduced below against the real
    categories table) finds it."""
    cat_id = str(uuid.uuid4())
    event = _pull_event(
        cat_id, "create",
        {"id": cat_id, "company_id": "company-A", "name": "Beverages", "description": "from device A"},
    )
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "company-B")

    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1
    assert rows[0]["id"] == cat_id
    assert rows[0]["company_id"] == "company-B"  # device B's own id, never "company-A"

    # Reproduces list_categories's own `WHERE c.company_id=?` filter against
    # the receiving device's own company_id -- this is the row actually
    # being invisible in the UI that live testing caught.
    conn = get_conn()
    visible = conn.execute("SELECT * FROM categories WHERE company_id=?", ("company-B",)).fetchall()
    invisible_under_senders_id = conn.execute("SELECT * FROM categories WHERE company_id=?", ("company-A",)).fetchall()
    conn.close()
    assert len(visible) == 1
    assert invisible_under_senders_id == []


def test_pull_once_without_a_company_id_provider_raises_instead_of_silently_misfiling(get_conn):
    """A SyncService wired with no local_company_id_provider at all (a
    misconfiguration) must fail loudly the moment a category create/update
    actually needs it -- never fall back to writing the payload's
    (wrong-device) company_id, and never insert a NULL/garbage company_id
    silently. run_once()'s own per-call try/except is what turns this into
    an ordinary retried-next-tick condition for a real caller; pull_once()
    itself must still raise so that swallowing is a deliberate choice made
    one layer up, not baked into apply_pull_result."""
    cat_id = str(uuid.uuid4())
    event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": "company-A", "name": "X", "description": ""})
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn)  # no local_company_id_provider

    with pytest.raises(RuntimeError):
        service.pull_once()

    # Nothing partially applied, cursor untouched -- same all-or-nothing
    # guarantee as any other mid-apply failure (see the partial-batch test
    # below).
    assert _categories(get_conn) == []
    assert _cursor(get_conn) == 0


def test_pull_once_delete_only_batch_never_needs_a_company_id_provider(get_conn):
    """A delete is keyed by categories.id alone (a client-generated UUID,
    globally unique -- see schema.py's v1->v2 migration note) and never
    writes a company_id, so a batch containing only deletes must apply
    cleanly even with no local_company_id_provider configured."""
    cat_id = str(uuid.uuid4())
    conn = get_conn()
    conn.execute("INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?)",
                 (cat_id, "company-B", "Pre-existing", ""))
    conn.commit()
    conn.close()

    delete_event = _pull_event(cat_id, "delete", {"id": cat_id})
    client = FakeRelayClient(pull_responses=[{"events": [delete_event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn)  # no local_company_id_provider

    service.pull_once()  # must not raise

    assert _categories(get_conn) == []


def test_pull_once_ignores_unknown_entity_types(get_conn):
    event = {
        "id": str(uuid.uuid4()), "entity_type": "product", "entity_id": "p-1",
        "event_type": "create", "payload": {"id": "p-1"}, "created_at": "2026-08-06T00:00:00+00:00",
    }
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 5}])
    service = SyncService(lambda: client, get_conn)

    service.pull_once()  # must not raise

    assert _categories(get_conn) == []
    assert _cursor(get_conn) == 5  # cursor still advances -- the event was seen, just not applicable here


def test_pull_once_failure_leaves_cursor_untouched(get_conn):
    client = FakeRelayClient(pull_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")])
    service = SyncService(lambda: client, get_conn)

    with pytest.raises(NetworkError):
        service.pull_once()

    assert _cursor(get_conn) == 0


def test_pull_once_partial_batch_apply_failure_never_advances_cursor_or_partially_commits(get_conn, monkeypatch):
    """The specific invariant the coordinator flagged: if applying event N
    of a batch raises partway through, event N-1's already-applied write
    must not be left committed, and the cursor must not advance -- the next
    attempt must re-pull and re-apply the WHOLE batch from the same `since`,
    never a gap and never a half-applied batch. Forced deterministically by
    making the second _apply_event call raise, rather than depending on any
    particular real-world payload shape to trigger a DB-level error."""
    cat_id_1 = str(uuid.uuid4())
    cat_id_2 = str(uuid.uuid4())
    event_1 = _pull_event(cat_id_1, "create", {"id": cat_id_1, "company_id": 1, "name": "First", "description": ""})
    event_2 = _pull_event(cat_id_2, "create", {"id": cat_id_2, "company_id": 1, "name": "Second", "description": ""})
    client = FakeRelayClient(pull_responses=[{"events": [event_1, event_2], "cursor": 9}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    real_apply = SyncService._apply_event
    calls = {"n": 0}

    def _apply_event_second_one_explodes(self, conn, ev, local_company_id=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure applying the second event in the batch")
        return real_apply(self, conn, ev, local_company_id)

    monkeypatch.setattr(SyncService, "_apply_event", _apply_event_second_one_explodes)

    with pytest.raises(RuntimeError):
        service.pull_once()

    # Event 1 was applied to the connection before the explosion, but the
    # connection was never committed (pull_once()'s finally: conn.close()
    # with no prior commit() discards the whole transaction) -- so event 1's
    # write must NOT be visible either. Proven against a real sqlite file,
    # not asserted from code inspection.
    assert _categories(get_conn) == []
    assert _cursor(get_conn) == 0  # unchanged -- next attempt re-pulls since=0, the whole batch again


def test_run_once_swallows_push_failure_and_still_attempts_pull(get_conn):
    client = FakeRelayClient(
        push_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")],
        pull_responses=[{"events": [], "cursor": 0}],
    )
    _insert_outbox_row(get_conn)
    service = SyncService(lambda: client, get_conn)

    service.run_once()  # must not raise

    assert len(client.pull_calls) == 1  # pull still attempted even though push failed


def test_run_once_swallows_pull_failure(get_conn):
    client = FakeRelayClient(pull_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")])
    service = SyncService(lambda: client, get_conn)

    service.run_once()  # must not raise


# ── start()/stop() lifecycle ─────────────────────────────────────────────

def test_start_and_stop_lifecycle_does_not_raise(get_conn):
    client = FakeRelayClient(pull_responses=[{"events": [], "cursor": 0}] * 5)
    service = SyncService(lambda: client, get_conn)
    service.start(interval_seconds=3600)
    service.stop()  # must cancel cleanly, no dangling timer firing later


# ── nudge() / module-level registration ─────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_active_service():
    unregister_active_service()
    yield
    unregister_active_service()


def test_nudge_with_no_registered_service_is_a_harmless_noop():
    nudge()  # must not raise, must not spawn anything observable


def test_nudge_fires_push_once_without_blocking_the_caller(get_conn):
    """Proves nudge() is a real fire-and-forget dispatch, not an inline
    call: push() blocks on an Event the test controls, and nudge() must
    return long before that Event is ever set."""
    _insert_outbox_row(get_conn)
    release = threading.Event()
    entered = threading.Event()

    class _BlockingClient:
        def push(self, events):
            entered.set()
            release.wait(timeout=5)
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            return {"events": [], "cursor": since}

    client = _BlockingClient()
    service = SyncService(lambda: client, get_conn)
    register_active_service(service)

    start = time.monotonic()
    nudge()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5  # returned immediately, did not wait for push() to finish
    assert entered.wait(timeout=2)  # but the background thread really did start pushing

    release.set()  # let it finish so the test doesn't leak a hung thread
    # Give the background thread a moment to actually finish and drain the outbox.
    for _ in range(50):
        if _outbox_rows(get_conn) == []:
            break
        time.sleep(0.05)
    assert _outbox_rows(get_conn) == []


def test_nudge_and_a_concurrent_scheduled_tick_never_run_push_once_at_the_same_time(get_conn):
    """Real concurrency test (not code inspection): SyncService's internal
    lock must serialize push_once() calls made from two different threads
    (the nudge() background thread and what would otherwise be the 10s
    timer's own thread) -- proves the FakeRelayClient never observes two
    overlapping push() calls."""
    _insert_outbox_row(get_conn)
    _insert_outbox_row(get_conn)

    concurrent_calls = {"active": 0, "max_active": 0}
    lock_for_counter = threading.Lock()

    class _CountingClient:
        def push(self, events):
            with lock_for_counter:
                concurrent_calls["active"] += 1
                concurrent_calls["max_active"] = max(concurrent_calls["max_active"], concurrent_calls["active"])
            time.sleep(0.15)  # hold the "critical section" long enough for a real race to show up
            with lock_for_counter:
                concurrent_calls["active"] -= 1
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            return {"events": [], "cursor": since}

    client = _CountingClient()
    service = SyncService(lambda: client, get_conn)
    register_active_service(service)

    # Thread A: simulates the scheduled timer tick calling run_once()/push_once()
    # directly. Thread B: a route-triggered nudge(), which spawns its OWN
    # background thread internally.
    thread_a = threading.Thread(target=service.push_once)
    thread_a.start()
    nudge()  # thread B, via the module-level dispatch
    thread_a.join(timeout=5)

    # nudge()'s own background thread may still be finishing (it no-ops once
    # thread_a already drained the outbox, but wait for it to be sure the
    # counter's final state is settled before asserting).
    for _ in range(50):
        if concurrent_calls["active"] == 0:
            break
        time.sleep(0.05)

    assert concurrent_calls["max_active"] <= 1  # never two push() calls in flight at once
