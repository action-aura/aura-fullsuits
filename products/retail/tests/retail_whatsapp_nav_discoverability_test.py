"""
Aura Retail -- WhatsApp reports page discoverability regression.

Same failure mode retail_licensing_nav_discoverability_test.py guards
against for licensing.html (a real bug this codebase already shipped and
fixed once) -- and the SAME bug independently exists today, unfixed, for
einvoicing.html: zero references to it anywhere under products/retail
(confirmed by grepping app-shell.js, index.html, and subsystem-retail.js).
This test exists so whatsapp.html cannot silently repeat either page's
mistake: served, built, wired to its API -- and unreachable.

whatsapp.html is linked from the Admin Center page (subsystem-retail.js's
_renderAdminCenter), not from app-shell.js's sidebar -- it is an admin-only
surface, same placement as the reorder-requests card already on that page,
not a top-level nav entry every device gets.

Run:
    pytest products/retail/tests/retail_whatsapp_nav_discoverability_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_whatsapp_nav_"))
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


def test_admin_center_links_to_the_whatsapp_page():
    """subsystem-retail.js's Admin Center render must contain a reachable
    path to whatsapp.html -- this is the exact check that would have failed
    if this page were built the way einvoicing.html was (served, never
    linked)."""
    client = app.test_client()
    r = client.get('/static/subsystem-retail.js')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert '/static/whatsapp.html' in body, (
        "subsystem-retail.js has no reference to /static/whatsapp.html -- "
        "the WhatsApp reports page is unreachable from the Admin Center."
    )


def test_whatsapp_page_links_back_to_the_app():
    client = app.test_client()
    r = client.get('/static/whatsapp.html')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'href="/"' in body, (
        "whatsapp.html has no link back to '/' -- a user who reaches this "
        "page has no way back into the app."
    )


def test_whatsapp_page_itself_is_served_and_references_its_own_script():
    client = app.test_client()
    r = client.get('/static/whatsapp.html')
    assert r.status_code == 200
    assert b'WhatsApp Reports' in r.data
    assert b'whatsapp.js' in r.data

    r2 = client.get('/static/whatsapp.js')
    assert r2.status_code == 200
