"""Aura Retail -- hub-self-sync wiring, AUTOMATIC ELECTION path (W2).

Same subject as retail_site_relay_hub_self_sync_wiring_test.py (W1), but
through the AUTOMATIC path: `AURA_SITE_RELAY_ENABLED` unset, so
`_start_site_relay_if_enabled` builds a `SiteRelayCoordinator` and holds an
election rather than binding immediately. `SiteRelayCoordinator` itself is
REPLACED with a fake that captures its constructor kwargs and does nothing
on `.start()` (the same pattern
retail_sync_hot_start_after_activation_test.py's own T14 already uses) --
so this file drives the `start_relay`/`stop_relay`/`attempt_join` SEAMS
directly, with no real UDP beacon and no real election timing, while still
proving those seams are the REAL `_bind_relay_now`/`_unbind_relay_now`/
`_attempt_join_now` closures `_start_site_relay_if_enabled` actually builds.

LAB SAFETY: no real coordinator is ever constructed (the fake replaces the
class before `init_app()` runs), so nothing here ever binds UDP 45455 or
sends a beacon -- see this task's own plan item 0.7.

Own process. Run:
    pytest products/retail/tests/retail_site_relay_hub_self_sync_election_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_hub_self_sync_election_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SITE_RELAY_ENABLED", None)  # AUTOMATIC mode
os.environ.update(AURA_SITE_RELAY_PORT="0", AURA_SITE_RELAY_BIND_HOST="127.0.0.1")
os.environ.pop("AURA_SYNC_RELAY_URL", None)

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
        # A usable (well-formed) URL, not None -- this scenario is the
        # ORDINARY "boot, never hub before, but a cloud relay IS configured"
        # case from this task's own plan table, which is what makes
        # `_SYNC_RELAY_URL_IS_USABLE` true at import and builds
        # `_sync_relay_selector` in CLOUD mode before any election ever
        # ticks. A closed loopback port -- nothing dials it in this file.
        sync_relay_base_url="http://127.0.0.1:1",
    ))


_persist_activated_hub_record()

# ── Fake coordinator, installed BEFORE init_app() (the T14 pattern) ───────
from commercial_runtime.sync.site_relay import coordinator as _coordinator_module  # noqa: E402

_captured = {'kwargs': None, 'instances': []}


class _CapturingCoordinator:
    def __init__(self, **kwargs):
        _captured['kwargs'] = kwargs
        _captured['instances'].append(self)

    def start(self):
        pass

    def stop(self):
        pass


_coordinator_module.SiteRelayCoordinator = _CapturingCoordinator

# Beacon stub too -- belt and braces (see W1's own comment): even though no
# real coordinator runs, `_bind_relay_now` (once called through the captured
# `start_relay` seam below) still goes through the real `start_site_relay`,
# which would otherwise try to construct a REAL BeaconBroadcaster.
from commercial_runtime.sync.site_relay import beacon as _beacon_module  # noqa: E402


class _NoOpBeaconBroadcaster:
    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


_beacon_module.BeaconBroadcaster = _NoOpBeaconBroadcaster

import app as _app_module  # noqa: E402

_app_module.SITE_FORWARDER_INTERVAL_SECONDS = 3600.0

from commercial_runtime.sync.site_relay import forwarder_loop as _forwarder_loop_module  # noqa: E402
from commercial_runtime.sync.site_relay import listener as _listener_module  # noqa: E402

_forwarder_start_calls = {'count': 0}
_real_forwarder_loop_start = _forwarder_loop_module.ForwarderLoop.start


def _counting_forwarder_start(self, *a, **kw):
    _forwarder_start_calls['count'] += 1
    return _real_forwarder_loop_start(self, *a, **kw)


_forwarder_loop_module.ForwarderLoop.start = _counting_forwarder_start

_housekeeping_start_calls = {'count': 0}
_real_start_housekeeping = _listener_module.start_housekeeping


def _counting_start_housekeeping(**kwargs):
    _housekeeping_start_calls['count'] += 1
    return _real_start_housekeeping(**kwargs)


_listener_module.start_housekeeping = _counting_start_housekeeping

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


# ── (a) after init_app(): coordinator built, nothing bound, CLOUD, no
#        forwarder loop yet ────────────────────────────────────────────────

def test_a_coordinator_was_built_nothing_bound_selector_is_cloud():
    assert _captured['kwargs'] is not None, "SiteRelayCoordinator was never constructed"
    assert _captured['kwargs'].get('my_installation_id') == _INSTALLATION_ID
    assert _app_module._site_relay_server is None
    assert _app_module._sync_relay_selector is not None
    assert _app_module._sync_relay_selector.is_local() is False
    assert _app_module._site_forwarder_loop is None


# ── (b) calling captured start_relay() binds on port 0 and flips to LOCAL,
#        forwarder=1, housekeeping=1 ────────────────────────────────────────

def test_b_start_relay_binds_and_flips_to_local():
    start_relay = _captured['kwargs']['start_relay']
    handle = start_relay()

    assert handle is not None
    assert isinstance(handle.port, int) and handle.port > 0
    assert _app_module._site_relay_server is handle
    assert _app_module._sync_relay_selector.is_local() is True
    assert _forwarder_start_calls['count'] == 1
    assert _housekeeping_start_calls['count'] == 1


# ── (c) a second start_relay() returns the SAME handle, forwarder still 1 ──

def test_c_second_start_relay_is_idempotent():
    start_relay = _captured['kwargs']['start_relay']
    first_handle = _app_module._site_relay_server

    second_handle = start_relay()

    assert second_handle is first_handle
    assert _forwarder_start_calls['count'] == 1


# ── (d) stop_relay(handle) unbinds; selector STAYS local; forwarder still
#        alive; new log text ────────────────────────────────────────────────

def test_d_stop_relay_unbinds_but_selector_stays_local(caplog):
    import logging
    caplog.set_level(logging.INFO, logger='app')

    stop_relay = _captured['kwargs']['stop_relay']
    handle = _app_module._site_relay_server
    assert handle is not None, "sanity: must still be bound from test (b)"

    stop_relay(handle)

    assert _app_module._site_relay_server is None
    assert _app_module._sync_relay_selector.is_local() is True, (
        "decision S1: standing down must NOT revert this till's own sync to the cloud")
    assert _app_module._site_forwarder_loop is not None
    assert _app_module._site_forwarder_loop._thread.is_alive(), (
        "the forwarder must keep running after a stand-down (election.py's "
        "'WHY STANDING DOWN LOSES NOTHING', updated for decision S1)")
    messages = [r.getMessage() for r in caplog.records]
    assert any('converges with the new hub through the cloud' in m for m in messages), messages


# ── (e) attempt_join() with a spy on discover_and_join gives 0 calls, and
#        the "not joining" INFO appears once ───────────────────────────────

def test_e_attempt_join_refuses_while_local(monkeypatch, caplog):
    import logging
    from commercial_runtime.sync.site_relay import autojoin as _autojoin_module

    caplog.set_level(logging.INFO, logger='app')
    join_calls = {'count': 0}

    def _spy_discover_and_join(**kwargs):
        join_calls['count'] += 1

    monkeypatch.setattr(_autojoin_module, 'discover_and_join', _spy_discover_and_join)

    attempt_join = _captured['kwargs']['attempt_join']
    # THREE calls, not one (adversarial verifier, 2026-09-24): the real
    # coordinator calls `attempt_join` on EVERY CLIENT tick
    # (coordinator.py `tick()`, TICK_INTERVAL_SECONDS == the 5s beacon
    # interval), and a till that was ever hub stays LOCAL for life
    # (decision S1 + the boot resume). One call could not tell "logged once"
    # from "logged on every tick" -- measured before the fix: 3 calls wrote
    # the refusal line 3 times, i.e. ~17,000 identical INFO lines a day on
    # every stood-down till.
    attempt_join()
    attempt_join()
    attempt_join()

    assert join_calls['count'] == 0
    messages = [r.getMessage() for r in caplog.records]
    refusals = [m for m in messages if 'Not joining another hub' in m]
    assert len(refusals) == 1, (
        f'expected the refusal logged exactly once across 3 CLIENT ticks, got {len(refusals)}')
