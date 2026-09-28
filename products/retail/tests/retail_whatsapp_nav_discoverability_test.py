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


# P1a (contracts.md item 5) -- "retail_whatsapp_nav_discoverability_test is
# updated to assert that the entry exists, sits in a group, is owner-visible,
# and loads the themed page." Widened here rather than replaced: the two
# checks above still prove the OLD failure mode (served/built but
# unreachable) cannot recur; this adds the P1a-specific assertions crew S's
# nav-group work and crew D's page theming must both keep true together.
def test_whatsapp_nav_entry_sits_in_the_connected_group_and_is_owner_only():
    """A structural (source-text) check, matching this whole file's own
    style -- app-shell.js is crew S's file (contracts.md's "own only your
    files"), never edited here; this only reads what it shipped. Finds the
    'whatsapp' nav entry object and the 'connected' navGroup's `items` list
    independently, then cross-checks them, so a group that silently drops
    'whatsapp' (or an entry that loses `ownerOnly`) fails this test by name
    rather than by a passing-for-the-wrong-reason substring match."""
    client = app.test_client()
    r = client.get('/static/app-shell.js')
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    entry_idx = body.find("id: 'whatsapp'")
    assert entry_idx != -1, "app-shell.js nav has no entry with id: 'whatsapp'"
    entry_end = body.find('}', entry_idx)
    entry_src = body[entry_idx:entry_end + 1]
    assert 'ownerOnly: true' in entry_src, (
        f"the 'whatsapp' nav entry is not ownerOnly (P1a: the Connected services group is owner-only): {entry_src!r}"
    )

    group_idx = body.find("key: 'connected'")
    assert group_idx != -1, "app-shell.js has no navGroup with key: 'connected'"
    group_end = body.find('}', group_idx)
    group_src = body[group_idx:group_end + 1]
    assert "'whatsapp'" in group_src, f"the 'connected' navGroup's items do not include 'whatsapp': {group_src!r}"


def test_whatsapp_page_loads_themed():
    """'Loads the themed page' (contracts.md item 5) -- a structural proof
    that the page carries the theme-boot contract and the shared token
    stylesheets, matching customer-display.html's own convention exactly
    (see that file's boot script). The REAL rendered proof -- computed
    background colour equal to the shell's, for two themes -- is a real
    browser and lives in retail_p1a_display_e2e.py (Playwright is not
    importable under this suite's venv Python, see that file's own header);
    this is the fast, no-browser regression guard that the source itself
    still carries every piece that real-browser proof depends on."""
    client = app.test_client()
    r = client.get('/static/whatsapp.html')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "localStorage.getItem('aura_theme_v2')" in body, "whatsapp.html no longer reads the shared theme-boot key"
    assert 'css/main.css' in body, "whatsapp.html no longer loads the shared token stylesheet"
    assert 'css/rtl.css' in body, "whatsapp.html no longer loads the shared RTL stylesheet"


# Fix round F4 (verifier finding): "nothing tests the headline WhatsApp
# behaviour" -- these two go RED if renderReportCards()'s boot call, or the
# collapsed-by-default state of the Advanced section, ever regress. Fast,
# source-level checks (no browser) -- the real-browser twin of the first one
# is retail_p1a_display_e2e.py's F3 scenario (exactly 4 visible cards).

def test_boot_calls_render_report_cards():
    client = app.test_client()
    r = client.get('/static/whatsapp.js')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    boot_idx = body.find('(async function boot() {')
    assert boot_idx != -1, "whatsapp.js no longer has a recognisable boot() IIFE"
    boot_end = body.find('})();', boot_idx)
    boot_src = body[boot_idx:boot_end]
    assert 'renderReportCards();' in boot_src, (
        "boot() no longer calls renderReportCards() -- the page would open with the "
        "Reports card stuck on its 'Loading…' placeholder forever"
    )


def test_templates_advanced_details_is_collapsed_by_default():
    client = app.test_client()
    r = client.get('/static/whatsapp.html')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    idx = body.find('id="templates-advanced"')
    assert idx != -1, "whatsapp.html no longer has the #templates-advanced <details> element"
    # The <details> tag itself is a short, fixed-shape opening tag -- read
    # backwards to its own '<details' to inspect just that one tag's
    # attributes, not the (possibly very long) content after it.
    tag_start = body.rfind('<details', 0, idx)
    tag_end = body.find('>', idx)
    tag_src = body[tag_start:tag_end + 1]
    assert ' open' not in tag_src and not tag_src.rstrip('>').endswith('open'), (
        f"#templates-advanced must be COLLAPSED by default (no `open` attribute) -- an ordinary shop "
        f"owner sending a receipt should never see raw Meta template fields unasked: {tag_src!r}"
    )
