"""Aura Retail -- hub-self-sync, EXPLICIT PIN BOUND BEFORE ACTIVATION (W4).

Written by the adversarial verifier (2026-09-24) because this path had NO
test at all. `_enter_hub_self_sync`'s own docstring names three call sites;
call site (c) is `_start_sync_after_activation`'s trailing

    if _site_relay_server is not None:
        _enter_hub_self_sync()

-- the case where an operator's explicit `AURA_SITE_RELAY_ENABLED=1` bound
the relay at boot, BEFORE this till had an installation id (so self-sync
deferred), and activation lands later in the same process. Measured before
this file existed: replacing that call with `pass` left all of W1/W2/W3 and
retail_sync_hot_start_after_activation_test.py GREEN (8+5+4+14 passed) --
i.e. the hub would have stayed an island until the next restart and nothing
would have noticed. W1 cannot see it (it boots ALREADY activated), and W1's
re-entrancy test reaches the same call with self-sync already on, where the
call is a no-op.

This file also pins three app.py helpers that had no behavioural test, only
an identity check or none (each found by a surviving mutation):
  * `_hub_own_consumer_floors` -- dropping the REGISTRY floor survived every
    test; that would let housekeeping prune `users` rows the hub's registry
    stream has not applied yet.
  * `_hub_self_sync_was_active_before` -- dropping the `relay_url ==
    LOCAL_RELAY_URL` signal survived; that signal is the only one left once
    housekeeping has pruned the site log empty.
  * `_forwarder_cloud_client` on a LAN-only till -- must be None, so the
    production loop reports NO_CLOUD (W1 pins the cloud-configured half).

LAB SAFETY (plan item 0.7): beacon replaced with a no-op stub before
init_app; relay bound on port 0 / 127.0.0.1; installation id prefixed
"zzzz-"; the explicit-pin path holds no election, so nothing listens on or
sends to UDP 45455. LAN-only (no AURA_SYNC_RELAY_URL, no persisted URL), so
nothing ever dials a network.

Own process. Run:
    pytest products/retail/tests/retail_site_relay_hub_self_sync_activate_after_pin_test.py -v
"""
import logging
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_hub_self_sync_pin_then_activate_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.update(
    AURA_SITE_RELAY_ENABLED="1",          # explicit operator pin: bind at boot, no election
    AURA_SITE_RELAY_PORT="0",
    AURA_SITE_RELAY_BIND_HOST="127.0.0.1",
)
os.environ.pop("AURA_SYNC_RELAY_URL", None)  # LAN-only

_INSTALLATION_ID = f"zzzz-hub-{uuid.uuid4()}"
_LICENSING_DB_PATH = DATA / "database" / "subsystems" / "licensing.db"

# NOTE: no licence record is persisted here -- this till boots UNACTIVATED.

from commercial_runtime.sync.site_relay import beacon as _beacon_module  # noqa: E402


class _NoOpBeaconBroadcaster:
    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


_beacon_module.BeaconBroadcaster = _NoOpBeaconBroadcaster

# Capture app.py's own log lines from the very first one init_app() writes
# (caplog only sees records emitted inside a test function).
_app_log_records = []


class _ListHandler(logging.Handler):
    def emit(self, record):
        _app_log_records.append(record)


_app_logger = logging.getLogger('app')
_app_logger.addHandler(_ListHandler(level=logging.INFO))
_app_logger.setLevel(logging.INFO)

import app as _app_module  # noqa: E402

_app_module.SITE_FORWARDER_INTERVAL_SECONDS = 3600.0

from commercial_runtime.sync.site_relay import forwarder_loop as _forwarder_loop_module  # noqa: E402
from commercial_runtime.sync.site_relay import listener as _listener_module  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService as _SyncServiceClass  # noqa: E402

_counts = {'forwarder_start': 0, 'housekeeping_start': 0, 'sync_start': 0}
_housekeeping_kwargs_seen = []

_real_forwarder_loop_start = _forwarder_loop_module.ForwarderLoop.start
_real_start_housekeeping = _listener_module.start_housekeeping
_real_sync_service_start = _SyncServiceClass.start


def _counting_forwarder_start(self, *a, **kw):
    _counts['forwarder_start'] += 1
    return _real_forwarder_loop_start(self, *a, **kw)


def _counting_start_housekeeping(**kwargs):
    _counts['housekeeping_start'] += 1
    _housekeeping_kwargs_seen.append(kwargs)
    return _real_start_housekeeping(**kwargs)


def _counting_sync_start(self, *a, **kw):
    _counts['sync_start'] += 1
    return _real_sync_service_start(self, *a, **kw)


_forwarder_loop_module.ForwarderLoop.start = _counting_forwarder_start
_listener_module.start_housekeeping = _counting_start_housekeeping
_SyncServiceClass.start = _counting_sync_start

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    if _app_module._site_forwarder_loop is not None:
        _app_module._site_forwarder_loop.stop(timeout=2.0)
    if _app_module._site_housekeeping_stop is not None:
        _app_module._site_housekeeping_stop.set()
    handle = _app_module._site_relay_server
    if handle is not None:
        if getattr(handle, 'beacon', None) is not None:
            handle.beacon.stop()
        handle.server.shutdown()
        handle.server.server_close()
    if _app_module._sync_service is not None:
        _app_module._sync_service.stop()
    if _app_module._registry_sync_service is not None:
        _app_module._registry_sync_service.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _persist_activation():
    from commercial_runtime.licensing_contracts.state_machine import LicenseState
    from commercial_runtime.licensing_contracts.state_repository import (
        LicenseStateRecord, LicenseStateRepository)

    LicenseStateRepository(_LICENSING_DB_PATH).save(LicenseStateRecord(
        licensing_schema_version=2,
        product_code='AURA_RETAIL',
        platform='WINDOWS',
        current_state=LicenseState.ACTIVE_ONLINE.value,
        owner_installation_id=_INSTALLATION_ID,
        sync_relay_base_url=None,
    ))


# -- 1. boot, unactivated: relay bound, self-sync DEFERRED (said once) ------

def test_1_boot_binds_relay_but_defers_self_sync_until_activation():
    handle = _app_module._site_relay_server
    assert handle is not None, "explicit pin must bind synchronously inside init_app()"
    assert handle.port != 5443

    assert _app_module._sync_service is None
    assert _app_module._sync_relay_selector is None
    assert _app_module._site_forwarder_loop is None
    assert _counts == {'forwarder_start': 0, 'housekeeping_start': 0, 'sync_start': 0}, _counts

    deferred = [r for r in _app_log_records if 'Hub self-sync deferred' in r.getMessage()]
    assert len(deferred) == 1, [r.getMessage() for r in _app_log_records]


# -- 2. activation mid-process: the REAL hook switches the hub on -----------

def test_2_activation_mid_process_turns_hub_self_sync_on_with_no_restart():
    from commercial_runtime.sync import sync_service as _sync_service_module
    from commercial_runtime.sync.site_relay.local_client import LocalSiteRelayClient

    _persist_activation()
    _app_module._on_licence_activated()   # the real on_activation_success hook

    selector = _app_module._sync_relay_selector
    assert selector is not None and selector.is_local() is True, (
        "activation landed while this till was already the (explicitly pinned) hub, "
        "but its own books were not routed through the site log -- the island is back")
    for label, service in (('retail', _app_module._sync_service),
                           ('registry', _app_module._registry_sync_service)):
        assert service is not None, f'{label} SyncService was not built'
        assert isinstance(service._client_factory(), LocalSiteRelayClient), label
    assert _sync_service_module._active_service is _app_module._sync_service
    assert _counts == {'forwarder_start': 1, 'housekeeping_start': 1, 'sync_start': 2}, _counts
    assert _housekeeping_kwargs_seen[0]['consumer_floor_provider'] is _app_module._hub_own_consumer_floors


def test_3_a_second_activation_hook_changes_nothing():
    _app_module._on_licence_activated()
    assert _counts == {'forwarder_start': 1, 'housekeeping_start': 1, 'sync_start': 2}, _counts


# -- 4. LAN-only: the production cloud factory says "no cloud" --------------

def test_4_lan_only_forwarder_reports_no_cloud_through_the_production_factory():
    assert _app_module._forwarder_cloud_client() is None
    result = _app_module._site_forwarder_loop.tick()
    assert result['state'] == 'NO_CLOUD', result


# -- 5. housekeeping floors cover BOTH of the hub's own streams -------------

def _set_cursor(conn, relay_url, last_seq):
    conn.execute("UPDATE sync_cursor SET relay_url = ?, last_seq = ? WHERE id = 1",
                 (relay_url, last_seq))
    conn.commit()


def test_5_consumer_floors_read_retail_and_registry_and_pin_the_real_sweep():
    from commercial_runtime.identity import registry_db
    from commercial_runtime.sync.site_relay import replay
    from commercial_runtime.sync.site_relay.local_client import LOCAL_RELAY_URL
    from database.schema import get_retail_conn

    # Cancel the two SyncService timers first so no tick can move either
    # cursor under this test (their first tick is 10s out, but determinism
    # beats a margin).
    _app_module._sync_service.stop()
    _app_module._registry_sync_service.stop()

    retail = get_retail_conn()
    registry = registry_db.get_conn()
    try:
        _set_cursor(retail, LOCAL_RELAY_URL, 7)
        _set_cursor(registry, LOCAL_RELAY_URL, 3)
        assert _app_module._hub_own_consumer_floors() == [7, 3]

        # End to end through the REAL sweep with the REAL provider: ten site
        # rows, the forwarder past all of them, no paired devices -- the only
        # thing holding rows back is the hub's own registry stream at 3.
        retail.execute("DELETE FROM site_sync_events")
        retail.execute("DELETE FROM sqlite_sequence WHERE name = 'site_sync_events'")
        for i in range(10):
            retail.execute(
                "INSERT INTO site_sync_events (id, entity_type, entity_id, event_type, payload, "
                "created_at, origin_device_id) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "user", str(uuid.uuid4()), "create", "{}",
                 "2026-09-24T00:00:00+00:00", "phone-x"))
        retail.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 10 WHERE id = 1")
        retail.commit()

        result = _listener_module.run_housekeeping_sweep(
            get_conn=get_retail_conn,
            nonce_ttl_seconds=replay.DEFAULT_NONCE_TTL_SECONDS,
            consumer_floor_provider=_app_module._hub_own_consumer_floors,
        )
        assert result is not None
        assert result['events_pruned'] == 3, (
            f"pruned {result['events_pruned']} rows; the registry stream had only consumed "
            f"up to seq 3, so rows 4..10 must survive")
        remaining = retail.execute("SELECT MIN(seq), COUNT(*) FROM site_sync_events").fetchone()
        assert tuple(remaining) == (4, 7)

        # A stream still bound to another relay has consumed NOTHING of the
        # site log -- floor 0, never its foreign last_seq.
        _set_cursor(registry, "https://cloud.example", 999)
        assert _app_module._hub_own_consumer_floors() == [7, 0]
    finally:
        retail.close()
        registry.close()


# -- 6. boot-resume signal: relay_url alone is enough (log pruned empty) ----

def test_6_was_active_before_reads_the_cursor_even_with_an_empty_site_log():
    from commercial_runtime.sync.site_relay.local_client import LOCAL_RELAY_URL
    from database.schema import get_retail_conn

    conn = get_retail_conn()
    try:
        conn.execute("DELETE FROM site_sync_events")
        conn.commit()

        _set_cursor(conn, LOCAL_RELAY_URL, 42)
        assert _app_module._hub_self_sync_was_active_before() is True, (
            "a hub whose site log housekeeping pruned empty must still resume LOCAL at boot")

        _set_cursor(conn, None, 0)
        assert _app_module._hub_self_sync_was_active_before() is False

        conn.execute(
            "INSERT INTO site_sync_events (id, entity_type, entity_id, event_type, payload, "
            "created_at, origin_device_id) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), "category", str(uuid.uuid4()), "create", "{}",
             "2026-09-24T00:00:00+00:00", "phone-x"))
        conn.commit()
        assert _app_module._hub_self_sync_was_active_before() is True
    finally:
        conn.close()
