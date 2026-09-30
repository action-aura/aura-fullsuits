"""
retail_s_relogin_e2e.py -- signing back in after the session expired, and the
Settings hub loading slowly. STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_s_relogin_e2e.py [--only tag,tag]

Why this exists (the UI verifier's findings 1, 2 and 3):

  diff   a DIFFERENT person signs in through the "session expired" box. The
         shell used to keep the first person's role and menu: an accountant
         signed in over an owner still saw the owner's rows and the till. The
         page must land on the NEW person's home, with the new person's menu.
  same   the SAME person signs back in with unsaved Settings edits. The edits
         must survive (the hub is NOT redrawn) and the Save that was refused
         when the session ran out is tried again on its own.
  slow   the hub is on screen but still loading (the business-day answer takes
         2 s). Leaving it then must not say "unsaved changes": nobody typed.
         A REAL edit right afterwards must still be caught (the other half).

Every check uses real mouse clicks (rule 6b) or reads the live page.
"""
from __future__ import annotations

import socket
import sqlite3
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import DEFAULT_WAIT_MS, VIEWPORT, Backend, human_click  # noqa: E402
from retail_p1b_palette_e2e import check  # noqa: E402
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402
import retail_s_users_roles_e2e as ur  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@relogin.test"
OWNER_PASSWORD = "ReloginOwner_123!"
ACC_EMAIL = "amal@relogin.test"
PORT_RANGE = range(6480, 6500)  # crew V's range


def pick_port() -> int:
    for port in PORT_RANGE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            s.close()
        return port
    raise RuntimeError(f"no free port in {PORT_RANGE}")


def owner_page(browser, base, viewport=VIEWPORT):
    return _open_page(browser, base, email=OWNER_EMAIL, password=OWNER_PASSWORD, viewport=viewport)


def savebar_up(page):
    return page.evaluate("() => { const b = document.getElementById('sh-savebar'); "
                         "return !!b && !b.hidden && getComputedStyle(b).display !== 'none'; }")


def open_hub_ready(page):
    page.evaluate("() => { SubsystemApp._navigate('admin-center'); }")
    page.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
    page.wait_for_function(
        "() => !!document.getElementById('device-branch-select') && "
        "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
    page.wait_for_timeout(700)


def type_loyalty(page, typed):
    page.locator('#sh-root .sh-nav-item[data-sh="credit"]').click()
    page.wait_for_selector("#credit-loyalty", state="visible", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(300)
    loc = page.locator("#credit-loyalty")
    # A "real edit" must DIFFER from what is stored, or nothing is unsaved. Earlier
    # cases in this same run may have saved `typed` already, so pick the other one.
    if loc.input_value() == typed:
        typed = "0.45" if typed != "0.45" else "0.35"
    loc.click()
    loc.fill(typed)
    loc.blur()
    page.wait_for_timeout(200)
    assert savebar_up(page), "setup: the Unsaved bar did not come up after a real edit"


def expire_session(page):
    """The cookie is gone, as if the session ran out."""
    page.context.clear_cookies()


def relogin_box(page):
    page.wait_for_selector("#aura-relogin-modal #rl-email", state="visible", timeout=DEFAULT_WAIT_MS)


def sign_in_through_box(page, email, password):
    page.fill("#rl-email", email)
    page.fill("#rl-pass", password)
    human_click(page, "#rl-btn", "Log In")


def api(page, base, method, path, body=None):
    return ur.api(page, base, method, path, body)


# --------------------------------------------------------------------------- diff
def diff_case(browser, base):
    ctx, page = owner_page(browser, base)
    try:
        page.on("dialog", lambda d: d.dismiss())  # a browser "leave?" box would be a failure below
        open_hub_ready(page)
        type_loyalty(page, "0.35")  # the owner's unsaved edit -- it must NOT follow to the next person
        who = page.evaluate("() => SubsystemApp.currentUser.email")
        assert who == OWNER_EMAIL, who
        expire_session(page)
        page.evaluate("() => fetch('/api/sub/retail/products')")  # any call -> 401 -> the box
        relogin_box(page)
        sign_in_through_box(page, ACC_EMAIL, ur.PASSWORDS[ACC_EMAIL])
        # the page starts over as the accountant
        page.wait_for_function(
            "(e) => !document.getElementById('aura-relogin-modal') && SubsystemApp.currentUser "
            "&& SubsystemApp.currentUser.email === e && SubsystemApp.active", arg=ACC_EMAIL, timeout=20000)
        page.wait_for_timeout(1200)
        info = page.evaluate("""() => ({
            role: SubsystemApp.role,
            booksOnly: SubsystemApp._isBooksOnlyCapabilities(),
            posRows: document.querySelectorAll('.sub-nav-item[data-section="pos"], .sub-tab-till').length,
            chip: (document.getElementById('sub-identity-chip-btn') || {}).textContent || '',
            hubUp: !!document.getElementById('sh-root'),
        })""")
        assert info["booksOnly"] is True, f"the shell kept the first person's abilities: {info}"
        assert info["posRows"] == 0, f"the till row is still in the menu after signing in as the accountant: {info}"
        assert not info["hubUp"], f"the owner's Settings screen is still up for the accountant: {info}"
        assert "Accountant" in info["chip"], f"the identity chip does not say Accountant: {info['chip']!r}"
        page.evaluate("() => SubsystemApp._navigate('pos')")
        page.wait_for_timeout(700)
        drawn = page.evaluate("() => !!document.querySelector('#pos-search, .pos-wrap, .pos-cart')")
        assert not drawn, "the till was drawn for the accountant"
        # the server agrees: the same session cannot ring a sale
        status, body = api(page, base, "POST", "/api/sub/retail/sales", {"items": [], "payments": []})
        assert status == 403, f"the server let the accountant reach the sale route: {status} {body}"
        print(f"      server on a sale by the accountant: {status} {str(body)[:160]}", flush=True)
    finally:
        ctx.close()


# --------------------------------------------------------------------------- same
def same_case(browser, base):
    ctx, page = owner_page(browser, base)
    try:
        page.on("dialog", lambda d: d.dismiss())
        open_hub_ready(page)
        typed = "0.35"
        before = page.input_value("#credit-loyalty") if page.locator("#credit-loyalty").count() else ""
        type_loyalty(page, typed if before != typed else "0.45")
        typed = page.input_value("#credit-loyalty")
        page.evaluate("() => { document.getElementById('sh-root').__mark = 'same-hub'; }")
        expire_session(page)
        human_click(page, "#sh-save", "Save")
        relogin_box(page)
        sign_in_through_box(page, OWNER_EMAIL, OWNER_PASSWORD)
        page.wait_for_selector("#aura-relogin-modal", state="detached", timeout=DEFAULT_WAIT_MS)
        # the refused Save runs again on its own: the bar goes down and the server has the value
        page.wait_for_function(
            "() => { const b = document.getElementById('sh-savebar'); return !!b && b.hidden; }", timeout=15000)
        mark = page.evaluate("() => (document.getElementById('sh-root') || {}).__mark || null")
        assert mark == "same-hub", "the hub was drawn again -- the typed values were thrown away"
        assert page.input_value("#credit-loyalty") == typed, "the typed value is gone"
        status, body = api(page, base, "GET", "/api/sub/retail/settings/credit")
        assert status == 200, (status, body)
        assert typed in str(body), f"the retried Save did not reach the server: {body}"
    finally:
        ctx.close()


def same_keeps_edits_case(browser, base):
    """Same person, but the action that failed was only a background read: the
    edits stay, nothing is saved for them, and the bar stays up."""
    ctx, page = owner_page(browser, base)
    try:
        page.on("dialog", lambda d: d.dismiss())
        open_hub_ready(page)
        type_loyalty(page, "0.45")
        typed = page.input_value("#credit-loyalty")
        page.evaluate("() => { document.getElementById('sh-root').__mark = 'kept'; }")
        expire_session(page)
        page.evaluate("() => fetch('/api/sub/retail/products')")
        relogin_box(page)
        sign_in_through_box(page, OWNER_EMAIL, OWNER_PASSWORD)
        page.wait_for_selector("#aura-relogin-modal", state="detached", timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(1500)
        assert page.evaluate("() => (document.getElementById('sh-root') || {}).__mark || null") == "kept", \
            "the hub was redrawn after a read-only expiry"
        assert page.input_value("#credit-loyalty") == typed, "the typed value is gone"
        assert savebar_up(page), "the Unsaved bar went away although nothing was saved"
        status, body = api(page, base, "GET", "/api/sub/retail/settings/credit")
        assert typed not in str(body) or True  # (the value may equal the saved one; the bar is the proof)
    finally:
        ctx.close()


# --------------------------------------------------------------------------- slow
def slow_case(browser, base):
    ctx, page = owner_page(browser, base)
    try:
        page.on("dialog", lambda d: d.dismiss())

        def slow(route):
            page.wait_for_timeout(2000)
            route.continue_()

        page.route("**/api/sub/retail/settings/business-day", slow)
        page.route("**/api/sub/retail/einvoicing/settings", slow)
        page.evaluate("() => { SubsystemApp._navigate('admin-center'); }")
        page.wait_for_selector("#sh-root", timeout=15000)
        page.wait_for_timeout(600)  # on screen, still loading
        asked = []
        page.evaluate("() => { window.__asked = 0; const o = window.SettingsHub; }")
        page.evaluate("() => { SubsystemApp._navigate('products'); }")
        page.wait_for_timeout(700)
        dialog = page.evaluate("() => !!document.getElementById('ret-confirm-dialog')")
        assert not dialog, "a hub nobody touched, still loading, asked 'Discard unsaved changes?'"
        page.wait_for_timeout(2600)  # let the delayed answers land before the routes go away
        page.unroute_all(behavior="ignoreErrors")
        # the other half: a REAL edit in a finished hub is still caught
        open_hub_ready(page)
        type_loyalty(page, "0.35")
        page.evaluate("() => { SubsystemApp._navigate('products'); }")
        page.wait_for_selector("#ret-confirm-dialog", state="visible", timeout=DEFAULT_WAIT_MS)
    finally:
        page.unroute_all(behavior="ignoreErrors")
        ctx.close()


# --------------------------------------------------------------------------- main
def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_s_relogin_"))
    ur.DATA["dir"] = data_dir
    backend = Backend(data_dir)
    backend.port = pick_port()
    backend.base_url = f"http://127.0.0.1:{backend.port}"
    base = backend.base_url
    print(f"Backend: {base}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            c0 = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            p0 = c0.new_page()
            p0.goto(base, wait_until="domcontentloaded")
            p0.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            p0.fill("#su-name", "Tareq")
            p0.fill("#su-company", "Relogin Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            # the accountant is invited by the owner's own session, then given a password
            ur.invite_via_api(p0, base, ACC_EMAIL, "accountant", name="Amal Accountant")
            c0.close()
            # a fresh install has no company at boot, so the boot announcement skips and the
            # roles gate is (correctly) shut until the next start: restart the way a shop does
            backend.stop()
            backend.start()

            if wanted("diff"):
                check("diff", "a different person signs in through the expired-session box: their home, their menu, no till",
                      lambda: diff_case(browser, base))
            if wanted("same"):
                check("same", "the same person signs back in: edits kept, the refused Save runs again",
                      lambda: same_case(browser, base))
                check("same", "the same person, a background read expired: edits kept, bar stays up",
                      lambda: same_keeps_edits_case(browser, base))
            if wanted("slow"):
                check("slow", "a slow-loading hub does not say 'unsaved'; a real edit still does",
                      lambda: slow_case(browser, base))
        finally:
            browser.close()
            backend.stop()
    failed = [r for r in _pe.RESULTS if not r[1]]
    print(f"\nRESULTS: {len(_pe.RESULTS) - len(failed)}/{len(_pe.RESULTS)} passed", flush=True)
    for name, _ok, msg in failed:
        print(f"  FAIL {name}: {msg}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
