"""Aura Retail -- hub-self-sync BOOT RESUME (W3): a till that has HOSTED a
site log before (in a prior process -- simulated here by seeding one
`site_sync_events` row before `import app`) must resume LOCAL mode
IMMEDIATELY on the next boot, before the first sync tick, rather than
reverting to Owner's cloud relay first and only later flipping.

THE LOSS THIS PREVENTS (decision A4(b)/A7 in this task's own plan): a hub
restart is the ordinary case for a shop machine (Windows Update, a power
cycle). If the pair started on the cloud and only flipped to LOCAL once
`_bind_relay_now` ran again (which -- on the automatic path -- can be up to
`HUB_LISTEN_SECONDS` after boot), any tick in that window would apply the
pulled cloud stream through a `SyncService` whose OWN `sync_cursor` is still
pointed at the cloud, and this till's own LAN rows already sitting in its
site log would sit unconsumed for that entire window.

THIS SCENARIO IS DELIBERATELY LAN-ONLY (no `AURA_SYNC_RELAY_URL`, no
persisted `sync_relay_base_url`): decision A5 in this task's own plan --
`_enter_hub_self_sync` must build a cloud-less pair (`relay_base_url=''`)
and still flip it to LOCAL and start it, for a shop with no internet at
all. Uses the AUTOMATIC path (fake coordinator, the same T14/W2 pattern) so
`init_app()` itself never binds a listener -- only the BOOT-RESUME seam in
`init_app()` runs anything here.

Own process. Run:
    pytest products/retail/tests/retail_site_relay_hub_self_sync_resume_test.py -v
"""
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_hub_self_sync_resume_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SITE_RELAY_ENABLED", None)  # AUTOMATIC mode
os.environ.update(AURA_SITE_RELAY_PORT="0", AURA_SITE_RELAY_BIND_HOST="127.0.0.1")
os.environ.pop("AURA_SYNC_RELAY_URL", None)  # LAN-only: no operator-set URL

_INSTALLATION_ID = f"zzzz-hub-{uuid.uuid4()}"
_LICENSING_DB_PATH = DATA / "database" / "subsystems" / "licensing.db"


def _persist_activated_lan_only_record():
    """Activated, but with NO sync_relay_base_url at all -- LAN-only."""
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
        sync_relay_base_url=None,
    ))


def _seed_prior_site_log():
    """Before `import app` (and therefore before `database.schema` runs its
    real migration chain through this test's own bootstrap), run the REAL
    migration directly so `site_sync_events` exists, then insert one row --
    the signal `_hub_self_sync_was_active_before()` reads to recognise "this
    till has hosted a site log before", simulating a PRIOR process's own
    hub-self-sync activity."""
    sys.path.insert(0, str(BACKEND_DIR))
    from database import schema as _retail_schema

    _retail_schema.init_retail()
    conn = sqlite3.connect(_retail_schema._get_path("retail"))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO site_sync_events (id, entity_type, entity_id, event_type, payload, "
            "created_at, origin_device_id) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), "category", str(uuid.uuid4()), "create", "{}",
             "2026-09-24T00:00:00+00:00", "phone-x"),
        )
        conn.commit()
    finally:
        conn.close()


_persist_activated_lan_only_record()
_seed_prior_site_log()

# ── Fake coordinator (same pattern as W2/T14) -- init_app() must not bind a
#    real listener; only the boot-RESUME seam is under test here. ─────────
from commercial_runtime.sync.site_relay import coordinator as _coordinator_module  # noqa: E402

_captured = {'kwargs': None}


class _CapturingCoordinator:
    def __init__(self, **kwargs):
        _captured['kwargs'] = kwargs

    def start(self):
        pass

    def stop(self):
        pass


_coordinator_module.SiteRelayCoordinator = _CapturingCoordinator

import app as _app_module  # noqa: E402

_app_module.SITE_FORWARDER_INTERVAL_SECONDS = 3600.0

from commercial_runtime.sync.sync_service import SyncService as _SyncServiceClass  # noqa: E402

_start_calls = {'count': 0}
_real_sync_service_start = _SyncServiceClass.start


def _counting_sync_start(self, *a, **kw):
    _start_calls['count'] += 1
    return _real_sync_service_start(self, *a, **kw)


_SyncServiceClass.start = _counting_sync_start

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    if _app_module._site_forwarder_loop is not None:
        _app_module._site_forwarder_loop.stop(timeout=2.0)
    if _app_module._site_housekeeping_stop is not None:
        _app_module._site_housekeeping_stop.set()
    if _app_module._sync_service is not None:
        _app_module._sync_service.stop()
    if _app_module._registry_sync_service is not None:
        _app_module._registry_sync_service.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def test_selector_is_local_with_no_bind_at_all():
    assert _app_module._site_relay_server is None, (
        "sanity: automatic mode must not have bound anything during this boot -- "
        "the coordinator is faked and its start() is a no-op")
    assert _app_module._sync_relay_selector is not None
    assert _app_module._sync_relay_selector.is_local() is True


def test_pair_is_built_lan_only():
    from commercial_runtime.sync.site_relay.local_client import LocalSiteRelayClient

    assert _app_module._sync_service is not None
    assert _app_module._registry_sync_service is not None
    client = _app_module._sync_service._client_factory()
    assert isinstance(client, LocalSiteRelayClient)


def test_sync_service_start_called_exactly_once_per_service_never_four():
    assert _start_calls['count'] == 2, (
        f"expected exactly one .start() per service (2 total: retail + registry), "
        f"got {_start_calls['count']} -- a boot-resume double-start would mean two "
        f"competing timers on the same SyncService instance"
    )


def test_register_active_service_holds_the_retail_instance():
    from commercial_runtime.sync.sync_service import _active_service

    assert _active_service is _app_module._sync_service
