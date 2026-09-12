"""Aura Retail -- multi-device sync foundation, Task 5 config-inertness
regression.

Proves SYNC_RELAY_BASE_URL being unset genuinely means "the sync loop never
gets constructed at all" -- not merely "constructed but never started". Own
subprocess/module (see products/run_all_tests.py's docstring: config.py /
app.py resolve environment variables once at import time, so this must be
the only test file in this process asserting on this particular env-var
combination -- matches every other test file in this suite's convention).

Run:
    pytest products/retail/tests/retail_sync_inert_when_unconfigured_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_inert_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# The actual condition under test: no AURA_SYNC_RELAY_URL at all (the real
# default for the overwhelming majority of installs, which never configure
# sync). Popped explicitly rather than assumed absent, in case something
# upstream in this process's environment set it.
os.environ.pop("AURA_SYNC_RELAY_URL", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def test_sync_service_is_never_constructed_when_url_unset():
    # This is the real proof of "genuinely does nothing": app.py only ever
    # constructs a SyncRelayClient/SyncService/requests.Session inside the
    # `if SYNC_RELAY_BASE_URL and ...:` block -- _sync_service staying None
    # means none of that ever ran, not that it ran and then stayed idle.
    assert _app_module._sync_service is None


def test_registry_sync_service_is_never_constructed_when_url_unset():
    """Phase 5 wave B2, Decision 6 (Task C) -- the registry-stream instance
    is built inside the SAME `if` block as the retail one, so it must be
    exactly as inert as `_sync_service` when sync is unconfigured."""
    assert _app_module._registry_sync_service is None


def test_no_service_is_registered_for_the_nudge_to_call():
    from commercial_runtime.sync import sync_service as sync_service_module

    assert sync_service_module._active_service is None


def test_nudge_is_a_true_noop_with_nothing_registered():
    """Calling nudge() (the same call retail_api.py's category routes make
    unconditionally after every commit) must be harmless when sync was
    never configured -- no exception, no thread, no attribute error."""
    from commercial_runtime.sync.sync_service import nudge

    nudge()  # must not raise


def test_app_still_boots_and_serves_health_with_sync_unconfigured():
    with app.test_client() as client:
        resp = client.get('/api/health')
        assert resp.status_code == 200
        assert resp.get_json() == {'status': 'ok'}
