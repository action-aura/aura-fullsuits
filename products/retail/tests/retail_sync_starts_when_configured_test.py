"""Aura Retail -- multi-device sync foundation, Task 5: the positive-path
mirror of retail_sync_inert_when_unconfigured_test.py. Proves that setting
SYNC_RELAY_BASE_URL (AURA_SYNC_RELAY_URL) actually constructs and starts the
SyncService, and registers it for nudge() -- own subprocess/module, same
reasoning as that file's docstring for why this must be a separate test
file rather than a second scenario in the same process.

Does not exercise a real push/pull round trip against a real Owner (that is
covered by the manual E2E in task-5-report.md and by
commercial_runtime/sync/tests/test_sync_service.py's FakeRelayClient-based
unit tests) -- this file only proves the *wiring*: object construction and
`.start()` actually happening when configured.

Run:
    pytest products/retail/tests/retail_sync_starts_when_configured_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_starts_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# Deliberately an address nothing listens on -- this file only proves the
# service gets CONSTRUCTED and STARTED, never that a real relay is reachable
# (push/pull failures against it are exercised elsewhere, and would be
# silently swallowed by run_once() regardless -- see test_sync_service.py).
os.environ["AURA_SYNC_RELAY_URL"] = "http://127.0.0.1:1"
os.environ["AURA_SYNC_RELAY_INSECURE"] = "1"

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    if _app_module._sync_service is not None:
        _app_module._sync_service.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def test_sync_service_is_constructed_when_url_configured():
    assert _app_module._sync_service is not None


def test_sync_service_start_was_actually_called_by_init_app():
    # start() schedules a real threading.Timer -- _timer being non-None is
    # direct proof .start() ran (init_app() calls it), not just that the
    # SyncService object exists.
    assert _app_module._sync_service._timer is not None


def test_sync_service_is_registered_for_nudge():
    from commercial_runtime.sync import sync_service as sync_service_module

    assert sync_service_module._active_service is _app_module._sync_service


def test_client_factory_builds_a_real_relay_client_pointed_at_configured_url():
    client = _app_module._build_sync_client()
    assert client._config.base_url == "http://127.0.0.1:1"


def test_app_still_boots_and_serves_health_with_sync_configured():
    with app.test_client() as client:
        resp = client.get('/api/health')
        assert resp.status_code == 200
