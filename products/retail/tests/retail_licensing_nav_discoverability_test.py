"""
Aura Retail -- device licensing page discoverability regression.

The bug: licensing.html/licensing.js (fully built and wired to
/api/licensing/*) was never linked from anywhere in the shipped app shell --
no sidebar nav entry, no settings section, no restricted-license banner.
Confirmed by grepping every frontend file for 'licensing'/'licensing.html'
and finding zero references from app-shell.js or index.html. Once a real
install has OWNER_LICENSING_BASE_URL configured (or an existing install
drifts into RESTRICTED/GRACE_PERIOD/EXPIRED/SUSPENDED), there was no
discoverable way to reach activation/reactivation short of being told the
raw /static/licensing.html URL out-of-band.

The fix: app-shell.js now renders a "License" button in the sidebar
(SubsystemApp.openLicensing(), same-window navigation -- window.open/new-tab
is unreliable inside the pywebview desktop window and the Edge --app
fallback, see products/retail/desktop/launcher_retail.py) that navigates to
/static/licensing.html, and licensing.html now carries a link back to '/' so
the round trip actually closes, mirroring the Android app's
onOpenLicensing/onBack pair (android/aura-retail/.../ui/AppRoot.kt).

This test asserts discoverability directly against the served static
assets (the same way the bug itself was found), not just that the routes
happen to exist -- a served-but-unlinked page is exactly the failure mode
being guarded against here.

Run:
    pytest products/retail/tests/retail_licensing_nav_discoverability_test.py -v
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
FRONTEND_DIR = PRODUCT_DIR / 'frontend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_licensing_nav_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def test_app_shell_links_to_the_licensing_page():
    """app-shell.js must contain a reachable path to licensing.html -- this
    is the exact check that would have failed before the fix (grepping the
    shipped shell script for any reference to the licensing page found
    nothing)."""
    client = app.test_client()
    r = client.get('/static/app-shell.js')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert '/static/licensing.html' in body, (
        "app-shell.js has no reference to /static/licensing.html -- the "
        "license activation page is unreachable from the app shell again."
    )
    # The nav trigger itself must exist and actually be wired to a button.
    assert 'openLicensing' in body


def test_licensing_page_links_back_to_the_app():
    """licensing.html must let the user return to the main shell -- before
    the fix this page was a dead end with no way back to '/'."""
    client = app.test_client()
    r = client.get('/static/licensing.html')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'href="/"' in body, (
        "licensing.html has no link back to '/' -- a user who reaches the "
        "licensing page has no way back into the app."
    )


def test_licensing_page_itself_is_still_served():
    # Sanity: the route this whole fix depends on reaching must still work.
    client = app.test_client()
    r = client.get('/static/licensing.html')
    assert r.status_code == 200
    assert b'Aura Retail Licensing' in r.data
