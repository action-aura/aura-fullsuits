"""Registry v9 / Track S: the two PRODUCTION callers app.py owes the identity
layer, proven by COUNTING CALLS to the real boot path (`init_app()`), not by
checking an outcome.

  * `announce_local_device` -- reason='boot' exactly once from `init_app()`, and
    reason='heartbeat' from the registry SyncService's own timer. Before this
    file the function had no production caller at all.
  * `enforce_cash_pair_all` -- the AUDIT-032 boot sweep, once, for the local
    company, right after the registry migration and before retail's.

An outcome check ("the device row exists") would pass for many reasons that are
not "the wiring ran" (a previous boot left the row; a route wrote it). The
recorders below replace the two identity functions, so the only way they are
called is through the wiring under test.

Own process (module-level `init_app()`), like the other boot-wiring tests.
Run:  pytest products/retail/tests/retail_s_boot_announce_wiring_test.py -v
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_boot_announce_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# An address nothing listens on: the registry SyncService must be BUILT (it is
# what carries the heartbeat) but nothing real is ever reached.
os.environ["AURA_SYNC_RELAY_URL"] = "http://127.0.0.1:1"
os.environ["AURA_SYNC_RELAY_INSECURE"] = "1"

COMPANY = "co-boot-announce"
ANNOUNCE_CALLS = []   # reason strings, in order
SWEEP_CALLS = []      # company ids, in order

from commercial_runtime.identity import device_context as _device_context  # noqa: E402
from commercial_runtime.identity import user_accounts as _user_accounts  # noqa: E402
from commercial_runtime.sync import sync_service as _sync_module  # noqa: E402


def _fake_announce(conn, company_id, reason="changed", **kwargs):
    ANNOUNCE_CALLS.append((reason, company_id, kwargs.get("app_version"),
                           kwargs.get("retail_schema_version")))
    return {"device": None, "announced": False, "admin_announced": False}


def _fake_sweep(conn, company_id):
    SWEEP_CALLS.append(company_id)
    return []


_device_context.announce_local_device = _fake_announce
_user_accounts.enforce_cash_pair_all = _fake_sweep
# A fresh install has no company yet, and both seams correctly do nothing
# without one; give them one so the wiring is what is under test.
_sync_module.local_company_id_from_registry = lambda: COMPANY

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    if _app_module._sync_service is not None:
        _app_module._sync_service.stop()
    if _app_module._registry_sync_service is not None:
        _app_module._registry_sync_service.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def test_init_app_announces_the_device_once_with_reason_boot():
    boots = [c for c in ANNOUNCE_CALLS if c[0] == "boot"]
    assert len(boots) == 1, ANNOUNCE_CALLS
    reason, company, app_version, schema_version = boots[0]
    assert company == COMPANY
    assert app_version == _app_module.APP_VERSION
    assert schema_version == _app_module.RETAIL_SCHEMA_VERSION


def test_init_app_runs_the_cash_pair_boot_sweep_once_for_the_local_company():
    assert SWEEP_CALLS == [COMPANY]


def test_registry_service_timer_calls_the_heartbeat_and_retail_service_does_not():
    """Drives the registry SyncService's REAL timer (10ms first delay) and
    counts `reason='heartbeat'` calls. The retail stream is given no hook, so it
    must never announce."""
    reg = _app_module._registry_sync_service
    assert reg is not None
    assert _app_module._sync_service._on_tick is None
    before = sum(1 for c in ANNOUNCE_CALLS if c[0] == "heartbeat")
    reg.stop()
    reg._stopped.clear()
    reg._next_interval_seconds = lambda: 3600.0  # do not chase a 2nd timer
    reg._schedule_next(0.01)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if sum(1 for c in ANNOUNCE_CALLS if c[0] == "heartbeat") > before:
            break
        time.sleep(0.05)
    reg.stop()
    heartbeats = [c for c in ANNOUNCE_CALLS if c[0] == "heartbeat"]
    assert len(heartbeats) == before + 1, ANNOUNCE_CALLS
    assert heartbeats[-1][1] == COMPANY
    # Boot is still counted exactly once: a heartbeat is not a second boot.
    assert sum(1 for c in ANNOUNCE_CALLS if c[0] == "boot") == 1


def test_a_failing_announce_never_stops_the_till_booting_or_ticking(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("announce blew up")

    monkeypatch.setattr(_device_context, "announce_local_device", _boom)
    _app_module._announce_boot_device()        # must not raise
    _app_module._announce_heartbeat_device()   # must not raise


def test_app_still_boots_and_serves_health():
    with app.test_client() as client:
        assert client.get('/api/health').status_code == 200
