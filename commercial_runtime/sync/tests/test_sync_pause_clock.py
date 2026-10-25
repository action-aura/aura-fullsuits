"""Keep-selling C3 (retail schema v58): the licence-pause clock.

The 72h sales stop used to count a deliberate pause as "offline": a shop that renewed after a
100-hour lapse was refused a sale until the first successful sync, because `seconds_since_last_
success` still covered the whole lapse. SyncService now stamps the last instant it SAW the pause
(`last_paused_seen_at`, persisted through the optional `record_paused_seen` hook of
SyncFreshnessStore) and `get_health()` reports `seconds_behind_excluding_pause` =
now - max(last success, last pause seen).

FIX ROUND 2 (E1): only the sync TIMER TICK (`run_once`) persists the clock; get_health() / the
request path only updates the in-memory value. The first version wrote from get_health() while
holding `_health_lock` on a 30 s busy-timeout connection, and create_sale calls get_health() inside
its own BEGIN IMMEDIATE -> a measured 65.30 s stalled sale. The persist tests below therefore drive
`run_once()`, and the lock tests hold a real write lock on a second connection.

The tests assert THE CHECK RAN (hook call counts, the persisted row), not only an outcome. The
retail wiring of the same seam, end to end through the real sale route, is in
products/retail/tests/retail_offline_sales_stop_test.py (section 5).

Run (one file per process):
    pytest commercial_runtime/sync/tests/test_sync_pause_clock.py -q
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.sync import sync_service as sync_service_module
from commercial_runtime.sync.sync_service import SyncFreshnessStore, SyncService
from commercial_runtime.sync.tests.test_sync_service import FakeRelayClient, db_path, get_conn  # noqa: F401

_TABLE = (
    "CREATE TABLE IF NOT EXISTS sync_freshness ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), last_push_success_at TEXT, last_pull_success_at TEXT, "
    "offline_override_at TEXT, last_paused_seen_at TEXT)"
)


@pytest.fixture
def fresh(get_conn):
    conn = get_conn()
    conn.execute(_TABLE)
    conn.execute("INSERT OR IGNORE INTO sync_freshness (id) VALUES (1)")
    conn.commit()
    conn.close()
    return get_conn


class _Store:
    """A real SQL-backed store (the shape retail's schema.py provides) that counts hook calls."""

    def __init__(self, get_conn, with_pause_hook=True, raise_on_pause=False):
        self.get_conn = get_conn
        self.pause_writes = 0
        self.raise_on_pause = raise_on_pause
        self.with_pause_hook = with_pause_hook

    def load(self, conn):
        r = conn.execute("SELECT last_push_success_at, last_pull_success_at, offline_override_at, "
                         "last_paused_seen_at FROM sync_freshness WHERE id=1").fetchone()
        return {"push": r[0], "pull": r[1], "override_at": r[2], "paused_seen_at": r[3]}

    def record(self, conn, half, when):
        col = "last_push_success_at" if half == "push" else "last_pull_success_at"
        conn.execute(f"UPDATE sync_freshness SET {col}=? WHERE id=1", (when,))

    def record_override(self, conn, when):
        conn.execute("UPDATE sync_freshness SET offline_override_at=? WHERE id=1", (when,))

    def record_paused_seen(self, conn, when):
        self.pause_writes += 1
        if self.raise_on_pause:
            raise RuntimeError("disk on fire")
        conn.execute("UPDATE sync_freshness SET last_paused_seen_at=? WHERE id=1", (when,))

    def freshness(self):
        if self.with_pause_hook:
            return SyncFreshnessStore(self.load, self.record, self.record_override, self.record_paused_seen)
        return SyncFreshnessStore(self.load, self.record, self.record_override)


class _Switch:
    def __init__(self, paused):
        self.paused = paused

    def __call__(self):
        return self.paused


def _service(get_conn, store, switch):
    return SyncService(lambda: FakeRelayClient(), get_conn, local_freshness_store=store.freshness(),
                       sync_paused=switch)


def _column(get_conn):
    conn = get_conn()
    try:
        return conn.execute("SELECT last_paused_seen_at FROM sync_freshness WHERE id=1").fetchone()[0]
    finally:
        conn.close()


def _seed_success(get_conn, hours_ago):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn = get_conn()
    conn.execute("UPDATE sync_freshness SET last_push_success_at=?, last_pull_success_at=? WHERE id=1", (ts, ts))
    conn.commit()
    conn.close()


def _seed_paused_seen(get_conn, hours_ago):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn = get_conn()
    conn.execute("UPDATE sync_freshness SET last_paused_seen_at=? WHERE id=1", (ts,))
    conn.commit()
    conn.close()


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


def test_a_paused_tick_persists_the_clock_and_health_reports_it(fresh):
    # Was `..._observation_...` driven by get_health(); E1 moved the write to the timer tick (see
    # the module docstring). Still proves the persist runs and lands in the row.
    store = _Store(fresh)
    service = _service(fresh, store, _Switch(True))
    assert _column(fresh) is None
    service.run_once()
    health = service.get_health()
    assert store.pause_writes == 1, "the persist hook never ran"
    stamped = _column(fresh)
    assert stamped is not None, "the row did not change"
    assert health["last_paused_seen_at"] >= stamped  # memory is at least as fresh as the row
    assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(stamped)).total_seconds()) < 30


def test_an_unpaused_service_never_writes_the_clock(fresh):
    store = _Store(fresh)
    service = _service(fresh, store, _Switch(False))
    for _ in range(3):
        service.run_once()
        health = service.get_health()
    assert store.pause_writes == 0
    assert _column(fresh) is None
    assert health["last_paused_seen_at"] is None


def test_the_persist_is_throttled_to_once_per_interval(fresh, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(sync_service_module, "_monotonic", clock)
    store = _Store(fresh)
    service = _service(fresh, store, _Switch(True))
    for _ in range(5):
        service.run_once()
    assert store.pause_writes == 1, "a busy tick must not write on every call"
    clock.now += sync_service_module.PAUSE_CLOCK_WRITE_INTERVAL_SECONDS - 1
    service.run_once()
    assert store.pause_writes == 1, "still inside the interval"
    clock.now += 2
    service.run_once()
    assert store.pause_writes == 2, "the interval elapsed, so the clock must be refreshed"
    assert sync_service_module.PAUSE_CLOCK_WRITE_INTERVAL_SECONDS == 60.0, (
        "the bound on the error is documented as 60 s; change the comments with the number")


def test_entering_the_pause_again_writes_immediately_even_inside_the_interval(fresh, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(sync_service_module, "_monotonic", clock)
    store = _Store(fresh)
    switch = _Switch(True)
    service = _service(fresh, store, switch)
    service.run_once()
    switch.paused = False
    service.run_once()
    switch.paused = True
    clock.now += 1
    service.run_once()
    assert store.pause_writes == 2, "a transition INTO the pause is always recorded"


def test_seconds_behind_excluding_pause_runs_from_the_later_of_success_and_pause(fresh):
    store = _Store(fresh)
    # renewed, 100 h since success, pause last seen 1 minute ago -> about a minute
    _seed_success(fresh, 100)
    _seed_paused_seen(fresh, 1 / 60)
    h = _service(fresh, store, _Switch(False)).get_health()
    assert h["seconds_since_last_success"] > 99 * 3600
    assert 30 < h["seconds_behind_excluding_pause"] < 120
    # both clocks 73 h old -> the 72 h stop still has something to bite on
    _seed_success(fresh, 73)
    _seed_paused_seen(fresh, 73)
    h = _service(fresh, store, _Switch(False)).get_health()
    assert h["seconds_behind_excluding_pause"] > 72 * 3600
    # a pause OLDER than the last success changes nothing (max, not min)
    _seed_success(fresh, 5)
    _seed_paused_seen(fresh, 50)
    h = _service(fresh, store, _Switch(False)).get_health()
    assert abs(h["seconds_behind_excluding_pause"] - h["seconds_since_last_success"]) < 5
    # never paused -> exactly today's figure
    conn = fresh()
    conn.execute("UPDATE sync_freshness SET last_paused_seen_at=NULL WHERE id=1")
    conn.commit()
    conn.close()
    h = _service(fresh, store, _Switch(False)).get_health()
    assert abs(h["seconds_behind_excluding_pause"] - h["seconds_since_last_success"]) < 5


def test_never_synced_keeps_the_figure_none_even_with_a_pause_on_record(fresh):
    store = _Store(fresh)
    _seed_paused_seen(fresh, 1)
    h = _service(fresh, store, _Switch(False)).get_health()
    assert h["never_synced"] is True
    assert h["seconds_since_last_success"] is None
    assert h["seconds_behind_excluding_pause"] is None


def test_the_clock_survives_a_restart(fresh):
    store = _Store(fresh)
    _service(fresh, store, _Switch(True)).run_once()
    stamped = _column(fresh)
    assert stamped is not None
    restarted = _service(fresh, _Store(fresh), _Switch(False))  # fresh process, same database
    assert restarted.get_health()["last_paused_seen_at"] == stamped


def test_a_store_without_the_pause_hook_is_harmless(fresh):
    store = _Store(fresh, with_pause_hook=False)
    service = _service(fresh, store, _Switch(True))
    health = service.get_health()
    assert health["paused_by_licence"] is True
    assert store.pause_writes == 0
    assert _column(fresh) is None
    assert health["last_paused_seen_at"] is not None, "the in-memory clock still advances"


def test_a_failing_persist_never_breaks_the_pause_or_the_tick(fresh, caplog):
    store = _Store(fresh, raise_on_pause=True)
    service = _service(fresh, store, _Switch(True))
    with caplog.at_level(logging.WARNING):
        service.run_once()
        service.push_once()
        service.pull_once()
        health = service.get_health()
    assert store.pause_writes >= 1, "the persist was attempted"
    assert health["paused_by_licence"] is True
    assert any("pause clock" in r.getMessage() for r in caplog.records), "a failed persist must be logged"


# ---------------------------------------------------------------------------------------------
# E1: read paths never write; the tick never waits on a held write lock
# ---------------------------------------------------------------------------------------------

def _hold_write_lock(get_conn):
    """What a sale does: BEGIN IMMEDIATE plus a real write, on its own connection."""
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE sync_freshness SET id=id WHERE id=1")
    return conn


def test_get_health_inside_a_write_lock_returns_fast_and_never_writes(fresh):
    """create_sale calls get_health() INSIDE its own BEGIN IMMEDIATE. The measured defect: that call
    persisted the pause clock and waited out a 30 s busy timeout (a 65 s sale). Asserts BOTH that it
    is fast AND that the persist hook never ran (a fast call that still wrote would only be lucky)."""
    store = _Store(fresh)
    service = _service(fresh, store, _Switch(True))
    sale = _hold_write_lock(fresh)
    try:
        t0 = time.monotonic()
        health = service.get_health()
        elapsed = time.monotonic() - t0
    finally:
        sale.rollback()
        sale.close()
    assert health["paused_by_licence"] is True, "the pause was not evaluated"
    assert health["last_paused_seen_at"] is not None, "memory must still be stamped by a read"
    assert store.pause_writes == 0, "a read path called the persist hook"
    assert _column(fresh) is None, "a read path wrote the database"
    assert elapsed < 1.0, f"get_health inside a sale transaction took {elapsed:.2f}s"


def test_a_tick_while_the_database_is_locked_is_quick_and_persists_on_the_next_free_tick(fresh, caplog):
    store = _Store(fresh)
    service = _service(fresh, store, _Switch(True))
    sale = _hold_write_lock(fresh)
    try:
        with caplog.at_level(logging.WARNING):
            t0 = time.monotonic()
            service.run_once()  # must not raise
            first = time.monotonic() - t0
            service.run_once()  # second failure: still no raise, no second warning
    finally:
        sale.rollback()
        sale.close()
    assert first < 2.0, f"a locked-database tick waited {first:.2f}s (default busy timeout is 30 s)"
    assert store.pause_writes == 2, "each locked tick must have ATTEMPTED the persist (retry)"
    assert _column(fresh) is None
    assert sum("pause clock" in r.getMessage() for r in caplog.records) == 1, "the warning must be deduped"
    service.run_once()  # lock released: the retry lands
    assert store.pause_writes == 3
    assert _column(fresh) is not None, "the persist was never retried after the lock cleared"


def test_the_tick_persist_does_not_hold_the_health_lock(fresh, monkeypatch):
    """get_health() on a request thread must not queue behind a tick that is waiting on the DB."""
    monkeypatch.setattr(sync_service_module, "_PAUSE_PERSIST_BUSY_MS", 1000)
    store = _Store(fresh)
    service = _service(fresh, store, _Switch(True))
    sale = _hold_write_lock(fresh)
    tick = threading.Thread(target=service.run_once)
    try:
        tick.start()
        time.sleep(0.15)  # the tick is now inside its (1 s) busy wait
        assert tick.is_alive(), "the tick finished early; the lock test proves nothing"
        t0 = time.monotonic()
        service.get_health()
        elapsed = time.monotonic() - t0
    finally:
        tick.join()
        sale.rollback()
        sale.close()
    assert elapsed < 0.4, f"get_health waited {elapsed:.2f}s behind the tick's database wait"


# ---------------------------------------------------------------------------------------------
# E3: a stamp from the future must not switch the 72h stop off
# ---------------------------------------------------------------------------------------------

def _seed_future_paused_seen(get_conn, seconds_ahead):
    ts = (datetime.now(timezone.utc) + timedelta(seconds=seconds_ahead)).isoformat()
    conn = get_conn()
    conn.execute("UPDATE sync_freshness SET last_paused_seen_at=? WHERE id=1", (ts,))
    conn.commit()
    conn.close()


def test_a_far_future_stored_stamp_is_ignored(fresh):
    """Measured on the real server: stamp = now + 365 d, 100 h behind, not paused -> the figure went
    negative and the sale was allowed (200 instead of 403)."""
    _seed_success(fresh, 100)
    _seed_future_paused_seen(fresh, 365 * 86400)
    h = _service(fresh, _Store(fresh), _Switch(False)).get_health()
    assert h["seconds_behind_excluding_pause"] > 99 * 3600, h["seconds_behind_excluding_pause"]
    assert h["last_paused_seen_at"] is None, "a future stamp must not even be surfaced"


def test_a_stamp_within_the_skew_tolerance_is_honoured(fresh):
    _seed_success(fresh, 100)
    _seed_future_paused_seen(fresh, 60)
    h = _service(fresh, _Store(fresh), _Switch(False)).get_health()
    assert h["last_paused_seen_at"] is not None
    assert h["seconds_behind_excluding_pause"] == 0.0, "an honoured stamp restarts the clock (clamped at 0)"


def test_the_figure_is_never_negative_even_if_a_future_stamp_reaches_it():
    """Second line of defence: called directly, bypassing the load-time filter, with a stamp inside
    the tolerance (so the filter lets it through) -- the raw difference is negative."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    half = {"last_success_at": ts}
    future = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
    assert SyncService._elapsed_excluding_pause(half, half, future) == 0.0
    far = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    assert SyncService._elapsed_excluding_pause(half, half, far) > 99 * 3600
    naive = datetime.now().replace(tzinfo=None).isoformat()
    assert SyncService._elapsed_excluding_pause(half, half, naive) > 99 * 3600
