"""Tests for `commercial_runtime.sync.site_relay.forwarder_loop` -- the
daemon-thread timer that actually calls `forwarder.run_once` on a tick.

THE DEFECT THIS MODULE CLOSES (see this task's own plan, finding 0.1):
`forwarder.py` is a complete, already-unit-tested pure module (push/pull/
cursor bookkeeping), but nothing in production ever called it -- it was
imported only by its own test file. `ForwarderLoop` is the missing timer,
mirroring the tick-versus-thread split `coordinator.py` already uses for
the LAN hub election.

Real-schema bootstrap, same convention as `test_site_relay_forwarder.py` in
this same directory. Run this file on its own, one process per file:

    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_forwarder_loop.py -q --color=no
"""
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
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

_DATA_DIR = Path(tempfile.mkdtemp(prefix="site_relay_forwarder_loop_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.sync.relay_client import NetworkError  # noqa: E402
from commercial_runtime.sync.site_relay import forwarder, forwarder_loop, store  # noqa: E402


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


class _RecordingCloudClient:
    """Same queue idiom as `test_site_relay_forwarder.py`'s FakeCloudClient,
    with per-call recording of `push`'s batch SIZE (not identity) so F2 can
    assert the paging shape without re-implementing forwarder.py's own
    tests."""

    def __init__(self, pull_queue=None):
        self.push_calls = []
        self.pull_calls = []
        self._pull_queue = list(pull_queue) if pull_queue is not None else None

    def push(self, events):
        self.push_calls.append(len(events))
        return {"stored": len(events), "received": len(events)}

    def pull(self, since):
        self.pull_calls.append(since)
        if self._pull_queue is not None:
            item = self._pull_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return {"events": [], "cursor": since}


# ── F1: no cloud -- NO_CLOUD state, cursor untouched, one INFO log ──────────

def test_f1_no_cloud_factory_gives_no_cloud_state_and_logs_once(conn, caplog):
    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: None)
    caplog.set_level(logging.INFO)

    result = loop.tick()
    assert result["state"] == "NO_CLOUD"
    cursor_before = forwarder.read_forward_cursor(conn)

    result2 = loop.tick()
    assert result2["state"] == "NO_CLOUD"
    cursor_after = forwarder.read_forward_cursor(conn)
    assert cursor_before == cursor_after == (0, 0)

    no_cloud_logs = [r for r in caplog.records if "NO_CLOUD" in r.getMessage() or "no cloud" in r.getMessage().lower()]
    assert len(no_cloud_logs) == 1, f"expected exactly one NO_CLOUD log line, got {len(no_cloud_logs)}"


# ── F2: paging -- 450 rows go up over 3 pages, apply_pulled is always the
#        no-op ──────────────────────────────────────────────────────────────

def test_f2_pages_up_to_max_pages_per_tick_and_uses_no_apply(conn, monkeypatch):
    n = 450
    events = [_event() for _ in range(n)]
    store.append_events(conn, events, origin_device_id="dev-a")
    conn.commit()

    apply_used = []
    real_run_once = forwarder.run_once

    def _spy_run_once(conn_, cloud_client, *, apply_pulled):
        apply_used.append(apply_pulled is forwarder_loop._no_apply)
        return real_run_once(conn_, cloud_client, apply_pulled=apply_pulled)

    monkeypatch.setattr(forwarder, "run_once", _spy_run_once)

    client = _RecordingCloudClient()
    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: client)

    result = loop.tick()

    assert result["state"] == "RAN"
    assert client.push_calls == [200, 200, 50]
    assert all(apply_used), "every run_once call must use forwarder_loop._no_apply"
    # The loop may run one extra, empty-page call to DISCOVER there is
    # nothing left to forward (the page after the last non-empty one, which
    # moves 0 and stops the loop) -- what matters is that push_calls itself
    # shows exactly the 3 real pages (200, 200, 50), already asserted above.
    assert len(apply_used) in (3, 4), apply_used


# ── F3: cloud factory raises -- FAILED state, no exception escapes; a push
#        NetworkError still lets the pull half run ────────────────────────

def test_f3_cloud_factory_raising_gives_failed_state_no_exception(conn):
    def _boom():
        raise RuntimeError("cannot resolve cloud url")

    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=_boom)
    result = loop.tick()  # must not raise
    assert result["state"] == "FAILED"
    assert result["error"]


def test_f3_push_network_error_still_runs_pull(conn):
    store.append_events(conn, [_event()], origin_device_id="dev-a")
    conn.commit()

    class _PushFails:
        def __init__(self):
            self.pull_calls = []

        def push(self, events):
            raise NetworkError("NETWORK_UNAVAILABLE", "offline")

        def pull(self, since):
            self.pull_calls.append(since)
            return {"events": [], "cursor": since}

    client = _PushFails()
    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: client)
    result = loop.tick()

    assert result["push_error"] == "NETWORK_UNAVAILABLE"
    assert len(client.pull_calls) == 1


# ── F4: dedup logging -- one WARNING for a repeated error, one INFO for
#        recovery ───────────────────────────────────────────────────────────

def test_f4_repeated_error_logs_once_recovery_logs_once(conn, caplog):
    caplog.set_level(logging.INFO)
    state = {"fail": True}

    class _FlakyClient:
        def push(self, events):
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            if state["fail"]:
                raise NetworkError("NETWORK_UNAVAILABLE", "offline")
            return {"events": [], "cursor": since}

    client = _FlakyClient()
    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: client)

    loop.tick()
    loop.tick()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected exactly one WARNING for a repeated error, got {len(warnings)}"

    caplog.clear()
    state["fail"] = False
    loop.tick()
    recovered = [r for r in caplog.records
                 if r.levelno == logging.INFO and "recover" in r.getMessage().lower()]
    assert len(recovered) == 1


# -- F4b: the WARNING says WHICH cloud (host:port), never the URL ------------

def test_f4b_error_warning_names_cloud_host_port_but_never_the_full_url(conn, caplog):
    """Added by the adversarial verifier (2026-09-24): the plan (B9) requires
    a new forwarder error to log the cloud's host:port, and G10's mitigation
    rests on it -- a till whose `sync_relay_base_url` was overwritten with
    another hub's LAN address by autojoin fails forever, and the ONLY
    evidence an operator gets is this line. Before the fix it read
    "state=RAN ... pull_error=NETWORK_UNAVAILABLE" with no host at all, so a
    dead Owner and a stale LAN address looked identical. host:port only:
    the path can carry a tenant slug and userinfo can carry credentials."""
    caplog.set_level(logging.INFO)

    class _Unreachable:
        base_url = "https://ops:s3cret@cloud.example:8443/relay/v1"

        def push(self, events):
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            raise NetworkError("NETWORK_UNAVAILABLE", "offline")

    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: _Unreachable())
    loop.tick()

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, warnings
    assert "cloud.example:8443" in warnings[0], warnings[0]
    for leak in ("/relay/v1", "https://", "s3cret", "ops:"):
        assert leak not in warnings[0], (leak, warnings[0])


# ── F5: start/stop lifecycle ────────────────────────────────────────────────

def test_f5_start_is_idempotent_ticks_at_interval_stop_joins(conn):
    client = _RecordingCloudClient()
    loop = forwarder_loop.ForwarderLoop(
        get_conn=_fresh_conn, cloud_client_factory=lambda: client, interval_seconds=0.05,
    )

    loop.start()
    loop.start()  # idempotent -- must not start a second thread
    # COUNT THE THREADS, not the ticks (adversarial verifier, 2026-09-24):
    # the tick-count assertion below is satisfied just as well by TWO loops
    # as by one -- deleting start()'s is-alive guard left this test GREEN.
    # Two forwarder threads would race each other through forward_up on one
    # site_forward_cursor, re-pushing the same pages.
    forwarder_threads = [t for t in threading.enumerate()
                         if t.name == "aura-site-relay-forwarder" and t.is_alive()]
    assert len(forwarder_threads) == 1, f"start() twice gave {len(forwarder_threads)} forwarder threads"
    time.sleep(0.3)
    loop.stop(timeout=2.0)

    assert len(client.pull_calls) >= 2, f"expected at least 2 ticks in 0.3s at 0.05s interval, got {len(client.pull_calls)}"
    assert not forwarder_threads[0].is_alive(), "stop() returned but the forwarder thread is still running"


# ── REHEARSAL-1007 B1 (d): a failing forwarder makes /sync/health unhealthy ──

def _hub_own_health(**overrides) -> dict:
    """The shape `SyncService.get_health()` returns on a hub whose own books
    read and write its own site log: healthy by construction, 0 pending --
    exactly what the rehearsal's till B reported while cut off."""
    healthy_half = {"healthy": True, "consecutive_failures": 0, "last_failure_at": None,
                    "last_failure_reason": None, "last_success_at": "2026-10-07T12:00:00+00:00"}
    health = {"configured": True, "healthy": True, "pending_count": 0,
              "never_synced": False, "seconds_since_last_success": 3,
              "push": dict(healthy_half), "pull": dict(healthy_half)}
    health.update(overrides)
    return health


def test_g1_tls_failing_forwarder_turns_hub_health_unhealthy_with_pending(conn):
    """The rehearsal measurement, replayed: the forwarder's cloud is
    unreachable over TLS on every tick and the site log holds rows that never
    left. MUTATION-PROOF: making `overlay_upstream_health` return `health`
    unchanged turns this RED (`healthy` stays True, `pending_count` 0)."""
    store.append_events(conn, [_event(), _event()], origin_device_id="this-hub")
    conn.commit()

    class _TlsFails:
        base_url = "https://192.168.1.219:5443"

        def push(self, events):
            raise NetworkError("TLS_VERIFICATION_FAILED", "self-signed")

        def pull(self, since):
            raise NetworkError("TLS_VERIFICATION_FAILED", "self-signed")

    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: _TlsFails())
    loop.tick()
    loop.tick()
    loop.tick()

    out = forwarder_loop.overlay_upstream_health(_hub_own_health(), loop.snapshot())

    assert out["healthy"] is False
    assert out["pending_count"] == 2, "the two unforwarded rows are pending, not 0"
    assert out["push"]["healthy"] is False and out["pull"]["healthy"] is False
    assert out["push"]["consecutive_failures"] == 3
    assert out["push"]["last_failure_reason"] == "UPSTREAM:TLS_VERIFICATION_FAILED"
    assert out["upstream"]["cloud_host"] == "192.168.1.219:5443"
    # Left alone on purpose -- the 72-hour offline policy reads these.
    assert out["seconds_since_last_success"] == 3 and out["never_synced"] is False


def test_g2_healthy_forwarder_and_lan_only_shop_change_nothing_but_pending(conn):
    """The allow half: a forwarder that works leaves the hub healthy, and a
    LAN-only shop (`NO_CLOUD`) is healthy with nothing added -- those rows
    are never meant to leave, so counting them would nag forever."""
    store.append_events(conn, [_event()], origin_device_id="this-hub")
    conn.commit()

    ok_loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn,
                                           cloud_client_factory=lambda: _RecordingCloudClient())
    ok_loop.tick()  # forwards the one row
    ok = forwarder_loop.overlay_upstream_health(_hub_own_health(), ok_loop.snapshot())
    assert ok["healthy"] is True
    assert ok["pending_count"] == 0
    assert ok["push"]["consecutive_failures"] == 0

    store.append_events(conn, [_event()], origin_device_id="this-hub")
    conn.commit()
    lan_loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: None)
    lan_loop.tick()
    lan = forwarder_loop.overlay_upstream_health(_hub_own_health(), lan_loop.snapshot())
    assert lan["healthy"] is True
    assert lan["pending_count"] == 0
    assert lan["upstream"]["state"] == "NO_CLOUD"


def test_g3_recovery_clears_the_failure_and_unconfigured_health_is_untouched(conn):
    # A row to send, or `forward_up` never calls push at all.
    store.append_events(conn, [_event()], origin_device_id="this-hub")
    conn.commit()
    state = {"fail": True}

    class _Flaky:
        def push(self, events):
            if state["fail"]:
                raise NetworkError("NETWORK_UNAVAILABLE", "offline")
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            return {"events": [], "cursor": since}

    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: _Flaky())
    loop.tick()
    assert forwarder_loop.overlay_upstream_health(_hub_own_health(), loop.snapshot())["healthy"] is False
    state["fail"] = False
    loop.tick()
    snap = loop.snapshot()
    assert snap["consecutive_failures"] == 0 and snap["last_success_at"]
    assert forwarder_loop.overlay_upstream_health(_hub_own_health(), snap)["healthy"] is True

    assert forwarder_loop.overlay_upstream_health({"configured": False}, snap) == {"configured": False}


def test_g4_drain_mode_uploads_but_never_pulls_down(conn):
    """REHEARSAL-1007 B1 (c): the kill-switch drain loop sends a former
    hub's leftover rows up and never writes cloud rows into a site log
    nobody reads any more."""
    store.append_events(conn, [_event(), _event(), _event()], origin_device_id="former-hub")
    conn.commit()
    client = _RecordingCloudClient()
    loop = forwarder_loop.ForwarderLoop(get_conn=_fresh_conn, cloud_client_factory=lambda: client,
                                        pull_down=False)
    result = loop.tick()
    assert result["forwarded"] == 3
    assert client.push_calls == [3]
    assert client.pull_calls == []
    assert forwarder.count_unforwarded(conn) == 0
