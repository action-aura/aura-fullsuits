"""Aura Retail -- keep-selling rule, wiring half: the REAL app's sync services pause on an UNPAID
subscription and resume the moment the licence is renewed, with no restart.

commercial_runtime/sync/tests/test_sync_licence_pause.py proves SyncService honours a
`sync_paused` callable. This file proves app.py actually HANDS one to the two SyncService streams it
builds (retail stream, registry stream), that it reads the licence FRESH each call (renewal needs no
restart), and that an unreadable/unknown licence means "not paused" (sync was never licence-gated
before this rule). It does NOT cover the hub pieces (the two ForwarderLoop constructions and
start_site_relay): those are proven in retail_sync_licence_pause_hub_wiring_test.py.

Own module (sets AURA_SYNC_RELAY_URL, boots the real app) -- run one file per process:
    pytest products/retail/tests/retail_sync_licence_pause_wiring_test.py -v
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_pause_wiring_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0" (keep-selling fix round 2, E4): same hazard as
# retail_offline_sales_stop_test.py -- left unset, the app self-elects LAN hub a few seconds after
# init_app() and register_active_service() swaps services behind the test's back. The hub's own
# pause wiring is proven (with the hub pinned ON) in retail_sync_licence_pause_hub_wiring_test.py.
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)
# An address nothing listens on: the services must be CONSTRUCTED and wired; we never let a tick
# reach the network (the pause is exactly what is under test).
os.environ["AURA_SYNC_RELAY_URL"] = "http://127.0.0.1:1"
os.environ["AURA_SYNC_RELAY_INSECURE"] = "1"

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    for svc in (_app_module._sync_service, _app_module._registry_sync_service):
        if svc is not None:
            svc.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _set_state(state: str):
    repo = LicenseStateRepository(DATA / "database" / "subsystems" / "licensing.db")
    record = repo.load()
    record.current_state = state
    repo.save(record)


def test_both_sync_services_were_handed_a_pause_callable():
    assert _app_module._sync_service is not None and _app_module._registry_sync_service is not None
    assert _app_module._sync_service._sync_paused is not None
    assert _app_module._registry_sync_service._sync_paused is not None


def test_active_licence_does_not_pause_either_stream():
    _set_state("ACTIVE_ONLINE")
    assert _app_module._sync_service._sync_paused() is False
    assert _app_module._registry_sync_service._sync_paused() is False
    assert _app_module._sync_service.get_health()["paused_by_licence"] is False


def test_lapsed_subscription_pauses_both_streams_and_renewal_resumes_without_restart():
    _set_state("SUBSCRIPTION_LAPSED")
    assert _app_module._sync_service._sync_paused() is True
    assert _app_module._registry_sync_service._sync_paused() is True
    assert _app_module._licence_pauses_sync_now() is True
    assert _app_module._sync_service.get_health()["paused_by_licence"] is True
    # Renewal lands (check-in rewrites the stored state) -- SAME service objects, no restart.
    _set_state("ACTIVE_ONLINE")
    assert _app_module._sync_service._sync_paused() is False
    assert _app_module._licence_pauses_sync_now() is False
    assert _app_module._sync_service.get_health()["paused_by_licence"] is False


def test_the_real_apps_store_persists_the_pause_clock_so_a_renewed_shop_is_not_counted_offline():
    """KEEP-SELLING C3 wiring: app.py must hand the retail service a freshness store that carries
    `record_paused_seen`. Without it the clock lives in memory only and a restart after the lapse
    would put the 72h stop back (the live-proven 403). Reads the REAL retail.db column."""
    from datetime import datetime, timedelta, timezone
    svc = _app_module._sync_service
    assert svc._local_freshness_store.record_paused_seen is not None, "app.py did not wire the hook"

    def column():
        conn = _app_module.get_retail_conn()
        try:
            return conn.execute("SELECT last_paused_seen_at FROM sync_freshness WHERE id=1").fetchone()[0]
        finally:
            conn.close()

    # An earlier test in this file already lapsed the licence and so stamped the column; start clean
    # so the assertion below proves THIS observation wrote it.
    conn = _app_module.get_retail_conn()
    try:
        conn.execute("UPDATE sync_freshness SET last_paused_seen_at=NULL WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    _set_state("ACTIVE_ONLINE")
    svc._pause_clock_written_at = None  # the throttle must not hide this write from the test
    assert column() is None
    _set_state("SUBSCRIPTION_LAPSED")
    svc.run_once()  # E1: only the timer tick persists the clock; get_health() is memory-only
    stamped = column()
    assert stamped is not None, "a lapsed service observed the pause but the real store wrote nothing"
    # Pretend the last real sync was 100 h ago, then renew: the lapse must not count.
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    svc._health["push"]["last_success_at"] = old
    svc._health["pull"]["last_success_at"] = old
    _set_state("ACTIVE_ONLINE")
    health = svc.get_health()
    assert health["paused_by_licence"] is False
    assert health["seconds_since_last_success"] > 99 * 3600
    assert health["seconds_behind_excluding_pause"] < 5 * 60


def test_other_non_active_states_do_not_pause_sync():
    """Only the unpaid lapse pauses sync; RESTRICTED etc. keep today's (ungated) behaviour."""
    for state in ("RESTRICTED", "EXPIRED", "GRACE_PERIOD", "SUSPENDED"):
        _set_state(state)
        assert _app_module._sync_service._sync_paused() is False, state
    _set_state("ACTIVE_ONLINE")


def test_an_unrecognised_stored_state_is_not_a_pause():
    _set_state("SOMETHING_FROM_A_FUTURE_VERSION")
    assert _app_module._sync_service._sync_paused() is False
    _set_state("ACTIVE_ONLINE")


def test_an_unreadable_licence_store_is_not_a_pause():
    class Boom:
        def load(self):
            raise RuntimeError("licensing.db locked")

    assert _app_module._read_licence_pauses_sync(Boom()) is False
