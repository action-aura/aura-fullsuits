"""Aura Retail -- hub-self-sync wiring, EXPLICIT PIN path (W1).

Proves `app.py::_enter_hub_self_sync` is actually reached by a REAL boot
through `init_app()`, with `AURA_SITE_RELAY_ENABLED=1` (an operator's
explicit "this machine is the hub" pin) and an ALREADY-ACTIVATED licence
record persisted before import -- so the relay binds and hub-self-sync
fully switches to LOCAL synchronously, inside `init_app()` itself, with no
restart and no second call needed.

THE DEFECT THIS PROVES CLOSED (this task's own plan, section 0): before
this wave, a hub kept its own SyncService pair pointed at Owner's cloud
relay while paired LAN devices talked only to the site log -- two
disconnected worlds. This file proves the PRODUCT's real boot path routes
the hub's own sync through its own site log, not merely that the pieces
work in isolation (that is what test_site_relay_hub_self_sync.py in
commercial_runtime/sync/tests/ already proves).

LAB SAFETY (this task's own plan, item 0.7 -- read before running near a
live hub): the real lab hub on this same machine listens on UDP 45455 for
beacons. This file:
  - replaces `BeaconBroadcaster` with a no-op stub BEFORE `init_app()` runs,
    and asserts the stub was the class actually constructed;
  - binds the site relay on `AURA_SITE_RELAY_PORT=0` /
    `AURA_SITE_RELAY_BIND_HOST=127.0.0.1` (never 5443, never 0.0.0.0);
  - uses an installation id prefixed `"zzzz-"`, so even a leaked beacon
    could never out-rank the real lab hub in an election (not that one runs
    here at all -- this is the EXPLICIT-PIN path, which holds no election).

Own process (env vars resolved once at import, same convention as every
neighbouring sync/site-relay test file in this directory). Run:
    pytest products/retail/tests/retail_site_relay_hub_self_sync_wiring_test.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_hub_self_sync_wiring_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.update(
    AURA_SITE_RELAY_ENABLED="1",
    AURA_SITE_RELAY_PORT="0",
    AURA_SITE_RELAY_BIND_HOST="127.0.0.1",
    # A closed loopback port -- nothing listens there. The forwarder's own
    # ticks are disabled below (SITE_FORWARDER_INTERVAL_SECONDS patched to
    # 3600 before init_app, and the real timer is never awaited in this
    # file), so this URL is never actually dialed by a background thread;
    # it only has to be well-formed enough to pass validation.
    AURA_SYNC_RELAY_URL="http://127.0.0.1:1",
)

_INSTALLATION_ID = f"zzzz-hub-{uuid.uuid4()}"
_LICENSING_DB_PATH = DATA / "database" / "subsystems" / "licensing.db"


def _persist_activated_hub_record():
    from commercial_runtime.licensing_contracts.state_machine import LicenseState
    from commercial_runtime.licensing_contracts.state_repository import (
        LicenseStateRecord, LicenseStateRepository)

    repo = LicenseStateRepository(_LICENSING_DB_PATH)
    repo.save(LicenseStateRecord(
        licensing_schema_version=2,
        product_code='AURA_RETAIL',
        platform='WINDOWS',
        current_state=LicenseState.ACTIVE_ONLINE.value,
        owner_installation_id=_INSTALLATION_ID,
        sync_relay_base_url="http://127.0.0.1:1",
    ))


_persist_activated_hub_record()

# ── Beacon stub, installed BEFORE init_app() ever runs (lab safety) ────────
from commercial_runtime.sync.site_relay import beacon as _beacon_module  # noqa: E402

_beacon_construction_count = {'count': 0}


class _NoOpBeaconBroadcaster:
    def __init__(self, **kwargs):
        _beacon_construction_count['count'] += 1

    def start(self):
        pass

    def stop(self):
        pass


_beacon_module.BeaconBroadcaster = _NoOpBeaconBroadcaster

import app as _app_module  # noqa: E402

# Patched before init_app() so the forwarder loop this file lets run for
# real never actually ticks during the test (see module docstring).
_app_module.SITE_FORWARDER_INTERVAL_SECONDS = 3600.0

# Spy-count the two things _enter_hub_self_sync starts, via the SAME
# construction-site pattern retail_sync_hot_start_after_activation_test.py
# already uses for SyncService.start.
from commercial_runtime.sync.site_relay import forwarder_loop as _forwarder_loop_module  # noqa: E402
from commercial_runtime.sync.site_relay import listener as _listener_module  # noqa: E402

_forwarder_start_calls = {'count': 0}
_real_forwarder_loop_start = _forwarder_loop_module.ForwarderLoop.start


def _counting_forwarder_start(self, *a, **kw):
    _forwarder_start_calls['count'] += 1
    return _real_forwarder_loop_start(self, *a, **kw)


_forwarder_loop_module.ForwarderLoop.start = _counting_forwarder_start

_housekeeping_start_calls = {'count': 0}
_housekeeping_kwargs_seen = []
_real_start_housekeeping = _listener_module.start_housekeeping


def _counting_start_housekeeping(**kwargs):
    _housekeeping_start_calls['count'] += 1
    _housekeeping_kwargs_seen.append(kwargs)
    return _real_start_housekeeping(**kwargs)


_listener_module.start_housekeeping = _counting_start_housekeeping

# Spy on start_site_relay's own `housekeeping` kwarg -- see PY-6 spec: "the
# relay received housekeeping=False". Patched on the listener module,
# same module `_start_site_relay_if_enabled` imports its `start_site_relay`
# name from.
_real_start_site_relay = _listener_module.start_site_relay
_start_site_relay_kwargs_seen = []


def _recording_start_site_relay(**kwargs):
    _start_site_relay_kwargs_seen.append(kwargs)
    return _real_start_site_relay(**kwargs)


_listener_module.start_site_relay = _recording_start_site_relay
# app.py imported `start_site_relay` by name into its own module namespace
# inside `_start_site_relay_if_enabled` via a LOCAL import (`from
# commercial_runtime.sync.site_relay.listener import (local_lan_addresses,
# start_site_relay)`), so patching the listener module's attribute above is
# picked up on the next call -- there is no separate `app`-level name to
# patch for this one, unlike `beacon.BeaconBroadcaster`.

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


# ── Assertions ───────────────────────────────────────────────────────────

def test_the_relay_is_bound_and_its_port_is_not_5443():
    handle = _app_module._site_relay_server
    assert handle is not None
    assert isinstance(handle.port, int) and handle.port > 0
    assert handle.port != 5443


def test_start_site_relay_received_housekeeping_false():
    assert _start_site_relay_kwargs_seen, "start_site_relay was never called"
    assert _start_site_relay_kwargs_seen[0].get('housekeeping') is False


def test_the_selector_is_local():
    assert _app_module._sync_relay_selector is not None
    assert _app_module._sync_relay_selector.is_local() is True


def test_both_services_factories_return_local_site_relay_client():
    from commercial_runtime.sync.site_relay.local_client import LocalSiteRelayClient

    for label, service in (('retail', _app_module._sync_service),
                           ('registry', _app_module._registry_sync_service)):
        assert service is not None, f'{label} SyncService was not built'
        client = service._client_factory()
        assert isinstance(client, LocalSiteRelayClient), (
            f'{label} service client_factory() returned {type(client)!r}, expected '
            f'LocalSiteRelayClient -- this instance is still talking to the cloud')


def test_forwarder_and_housekeeping_each_started_exactly_once():
    assert _forwarder_start_calls['count'] == 1, _forwarder_start_calls
    assert _housekeeping_start_calls['count'] == 1, _housekeeping_start_calls
    # SYNC-JOIN (2026-10-13): was `is _hub_own_consumer_floors`; app.py now wires the
    # wrapper that prepends a 0 floor. The own-floors guarantee is still checked inside.
    _provider_carries_hub_floors_and_syncjoin_zero(
        _housekeeping_kwargs_seen[0].get('consumer_floor_provider'), _app_module)


def test_forwarder_cloud_factory_builds_a_real_client_for_the_configured_cloud():
    """Added by the adversarial verifier (2026-09-24). `_forwarder_cloud_
    client` is the ONLY thing that points the forwarder at Owner, and had no
    test at all: replacing its body with `return None` left every file in
    this wave GREEN -- which is precisely the measured defect this wave
    exists to close (hub `site_forward_cursor` stuck at forwarded=0, cloud
    seq=0), reintroduced with nothing noticing. Constructing the client dials
    nothing, so this stays lab-safe."""
    from commercial_runtime.sync.relay_client import SyncRelayClient

    client = _app_module._forwarder_cloud_client()
    assert isinstance(client, SyncRelayClient), (
        f'forwarder cloud factory returned {client!r} with a cloud relay configured -- '
        f'the forwarder would sit in NO_CLOUD forever')
    assert client.base_url == "http://127.0.0.1:1"
    # The hub forwards under ITS OWN installation id -- the same identity
    # `_build_sync_client` signs with -- which is what makes Owner's
    # "exclude the requester" rule the forwarder's echo guard.
    assert client._installation_id == _INSTALLATION_ID


def test_beacon_stub_was_constructed_real_one_was_not():
    assert _beacon_construction_count['count'] >= 1, (
        "the beacon was never constructed at all -- check signer/installation_id wiring"
    )


def test_end_to_end_outbox_row_and_phone_push_both_apply():
    """A real outbox row (same columns retail_api.py uses) goes through
    push_once() into the site log under this hub's own origin; a phone's
    pushed category event goes through pull_once() and lands in
    `categories`."""
    from database.schema import get_retail_conn
    from commercial_runtime.identity.registry_db import get_conn as _registry_get_conn
    from commercial_runtime.sync.site_relay import store

    # `local_company_id_from_registry` (the retail SyncService's own
    # `local_company_id_provider`) requires this device's onboarding to
    # have completed (a `company_settings` row) before it will apply a
    # pulled category event -- see that function's own docstring. This
    # fresh test install has never run onboarding, so seed the row
    # directly, the same "this device's own company_id" shape
    # `create_admin` would have written.
    reg_conn = _registry_get_conn()
    try:
        reg_conn.execute(
            "INSERT OR IGNORE INTO company_settings (id, company_id) VALUES (?,?)",
            (str(uuid.uuid4()), "1"),
        )
        reg_conn.commit()
    finally:
        reg_conn.close()

    entity_id = str(uuid.uuid4())
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), "category", entity_id, "create",
             json.dumps({"id": entity_id, "company_id": 1, "name": "Wired", "description": ""}),
             "2026-09-24T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    _app_module._sync_service.push_once()

    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT origin_device_id FROM site_sync_events WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        assert row is not None, "the outbox row never reached the site log"
        assert row["origin_device_id"] == _INSTALLATION_ID

        # `_sender_schema_version` is required: `_sync_service` is built with
        # `local_schema_version_provider=lambda: RETAIL_SCHEMA_VERSION` (see
        # `_build_windows_sync_services`), which arms the schema-version
        # skew guard -- an event with no verifiable sender version is
        # quarantined rather than applied (correctly; see that guard's own
        # docstring), so a real phone's payload always carries this key.
        from database.schema import RETAIL_SCHEMA_VERSION

        phone_entity_id = str(uuid.uuid4())
        phone_event = {
            "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": phone_entity_id,
            "event_type": "create",
            "payload": {
                "id": phone_entity_id, "company_id": 1, "name": "From Phone", "description": "",
                "_sender_schema_version": RETAIL_SCHEMA_VERSION,
            },
            "created_at": "2026-09-24T00:00:01+00:00",
        }
        store.append_events(conn, [phone_event], origin_device_id="phone-real-1")
        conn.commit()
    finally:
        conn.close()

    _app_module._sync_service.pull_once()

    conn = get_retail_conn()
    try:
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (phone_entity_id,)).fetchone()
    finally:
        conn.close()
    assert cat is not None and cat["name"] == "From Phone"


def test_reentrancy_start_sync_after_activation_completes_forwarder_still_one():
    # daemon=True is load-bearing for the mutation proof (M13: RLock ->
    # Lock): a plain Lock here deadlocks this thread FOREVER (it already
    # holds the lock via _start_sync_after_activation, then _enter_hub_
    # self_sync tries to re-acquire it) -- a non-daemon thread would then
    # hang the whole pytest process past this test's own 5s join, rather
    # than letting the assertion below fail normally and the suite exit.
    thread = threading.Thread(target=_app_module._start_sync_after_activation, daemon=True)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "_start_sync_after_activation hung (RLock re-entrancy regression)"
    assert _forwarder_start_calls['count'] == 1, (
        f"a second call must not start a second forwarder loop, got {_forwarder_start_calls['count']}")


def _provider_carries_hub_floors_and_syncjoin_zero(provider, app_module):
    """The wired provider must (a) be the SYNC-JOIN wrapper, (b) still carry the
    hub's OWN two stream floors (the original guarantee this test pinned by
    identity), and (c) lead with 0 so nothing is pruned until bootstrap exists."""
    assert provider is app_module._hub_housekeeping_floors
    real = app_module._hub_own_consumer_floors
    try:
        app_module._hub_own_consumer_floors = lambda: [41, 43]
        assert provider() == [0, 41, 43]
    finally:
        app_module._hub_own_consumer_floors = real
