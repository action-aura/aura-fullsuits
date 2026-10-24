"""Aura Retail -- keep-selling rule, Android half: the REAL app's Android branch hands the
licence-pause callable to BOTH `make_sync_internal_blueprint` registrations (retail stream at
/api/sync, registry stream at /api/registry-sync), and Kotlin's `GET .../_internal/pause-status`
therefore reflects the real licensing.db state on every request.

commercial_runtime/sync/tests/test_internal_routes.py proves the route given a callable. Before this
file nothing failed if app.py's Android branch passed `sync_paused=None` (the route would answer
`false` forever and a lapsed Android shop would keep syncing). Each registration is captured by
wrapping the real factory (the real blueprint is still built); the assertions then go through the
real HTTP route with the real internal secret, so "something non-None" cannot pass.

Own module (boots the real app as ANDROID) -- run one file per process:
    pytest products/retail/tests/retail_android_pause_status_wiring_test.py -v
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

SECRET = "android-pause-status-test-secret"
DATA = Path(tempfile.mkdtemp(prefix="aura_retail_android_pause_wiring_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0", AURA_PLATFORM="ANDROID",
                  AURA_INTERNAL_SHARED_SECRET=SECRET)
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository  # noqa: E402
from commercial_runtime.sync import internal_routes as _internal_routes  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="ANDROID")

FACTORY_KWARGS: list = []   # one dict per make_sync_internal_blueprint(...) call, in order
_real_factory = _internal_routes.make_sync_internal_blueprint


def _recording_factory(*args, **kwargs):
    FACTORY_KWARGS.append(dict(kwargs))
    return _real_factory(*args, **kwargs)


_internal_routes.make_sync_internal_blueprint = _recording_factory

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    _internal_routes.make_sync_internal_blueprint = _real_factory
    shutil.rmtree(DATA, ignore_errors=True)


def _set_state(state: str):
    repo = LicenseStateRepository(DATA / "database" / "subsystems" / "licensing.db")
    record = repo.load()
    record.current_state = state
    repo.save(record)


def _status(prefix: str) -> dict:
    resp = app.test_client().get(f"{prefix}/_internal/pause-status",
                                 headers={"X-Aura-Internal-Secret": SECRET})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_both_android_registrations_were_handed_a_pause_callable():
    assert len(FACTORY_KWARGS) == 2, "the Android branch must register exactly the retail + registry blueprints"
    prefixes = [kw.get("url_prefix", "/api/sync") for kw in FACTORY_KWARGS]
    assert prefixes == ["/api/sync", "/api/registry-sync"]
    for kw in FACTORY_KWARGS:
        assert callable(kw.get("sync_paused")), "an Android registration got sync_paused=None"


def test_pause_status_follows_the_real_licence_state_on_both_streams():
    for prefix in ("/api/sync", "/api/registry-sync"):
        _set_state("ACTIVE_ONLINE")
        assert _status(prefix) == {"paused_by_licence": False}
        _set_state("SUBSCRIPTION_LAPSED")
        assert _status(prefix) == {"paused_by_licence": True}
        _set_state("ACTIVE_ONLINE")  # renewal: next request, no restart
        assert _status(prefix) == {"paused_by_licence": False}


def test_other_non_active_states_do_not_pause_android_sync():
    for state in ("RESTRICTED", "EXPIRED", "GRACE_PERIOD", "SUSPENDED"):
        _set_state(state)
        assert _status("/api/sync") == {"paused_by_licence": False}, state
    _set_state("ACTIVE_ONLINE")
