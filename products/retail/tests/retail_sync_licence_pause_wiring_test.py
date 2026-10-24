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
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
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
