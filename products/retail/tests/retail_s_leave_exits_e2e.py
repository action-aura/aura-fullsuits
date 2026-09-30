"""
retail_s_leave_exits_e2e.py -- every way OUT of the Settings hub asks first, proved
in a REAL browser with real mouse clicks. STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_s_leave_exits_e2e.py [--only tag,tag]

Why this exists: the leave guard (retail_p1b_leave_guard_e2e.py) only proves the
hook for the exits that go through SubsystemApp._navigate() and logout(). A
verifier found that a person could still lose an unsaved Settings hub by walking
out through the doors that do NOT: a full page load to another page of the
product, the brand logo, "make this the main computer", and the browser itself.
Each of those is a tag here. Every "expect the prompt" case has the opposite
control next to it (a CLEAN hub must leave without asking; "Discard" must leave),
because a guard that always says stay would otherwise pass.

The dirty state is a REAL edit: Money & customers -> loyalty rate typed into
#credit-loyalty with a real click and real keys, so the Unsaved bar is up.

Tags:
  ext      sidebar rows WhatsApp / E-Invoicing / Shop network (full page loads)
  more     phone More sheet: external row asks, and the sheet is CLOSED after
           "Keep editing" (it used to stay open behind the prompt)
  licence  identity-menu "Plan & devices", gear-menu "License & devices" and the
           phone More sheet row all reach openLicensing()
  logo     the brand logo (exit() -> launch()) asks
  claim    "Make this the main computer" asks BEFORE it calls the server: count
           the POSTs to /api/devices/me/claim-admin (cancel 0, discard 1)
  unload   the browser's own reload/back/close: a dirty hub gets the browser's
           beforeunload prompt, a clean one does not
  busy     a prompt that never settles cannot lock every exit forever (stale
           after 2 s with no dialog on screen) -- but a REAL dialog on screen
           still blocks a second prompt
"""
from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    DEFAULT_WAIT_MS, VIEWPORT, Backend, human_click,
)
from retail_p1b_palette_e2e import check  # noqa: E402
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@leaveexits.test"
OWNER_PASSWORD = "LeaveExitsOwner_123!"
PHONE = {"width": 390, "height": 844}
PORT_RANGE = range(6260, 6280)  # crew U's range (contract: 6260-6279)
EXTERNAL = {
    "whatsapp": "/static/whatsapp.html",
    "einvoicing": "/static/einvoicing.html",
    "site-relay": "/static/site-relay.html",
}


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


def open_page(browser, base, viewport=VIEWPORT):
    return _open_page(browser, base, email=OWNER_EMAIL, password=OWNER_PASSWORD, viewport=viewport)


# --------------------------------------------------------------------------- helpers
def dirty_hub(page):
    """Open the hub and make a REAL unsaved edit. Returns the typed value."""
    page.evaluate("() => { SubsystemApp._navigate('admin-center'); }")
    page.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
    page.wait_for_function(
        "() => !!document.getElementById('device-branch-select') && "
        "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
    page.wait_for_timeout(500)
    page.locator('#sh-root .sh-nav-item[data-sh="credit"]').click()
    page.wait_for_selector("#credit-loyalty", state="visible", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(300)
    before = page.input_value("#credit-loyalty")
    typed = "0.35" if before != "0.35" else "0.45"
    loc = page.locator("#credit-loyalty")
    loc.click()
    loc.fill(typed)
    loc.blur()
    page.wait_for_timeout(200)
    assert savebar_up(page), "setup: the Unsaved bar did not come up after a real edit"
    return typed


def clean_hub(page):
    """Open the hub and change nothing."""
    page.evaluate("() => { SubsystemApp._navigate('admin-center'); }")
    page.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
    page.wait_for_function(
        "() => !!document.getElementById('device-branch-select') && "
        "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
    page.wait_for_timeout(500)
    assert not savebar_up(page), "setup: the Unsaved bar is up on a hub nobody touched"


def savebar_up(page):
    return page.evaluate("() => { const b = document.getElementById('sh-savebar'); "
                         "return !!b && !b.hidden && getComputedStyle(b).display !== 'none'; }")


def dialog_up(page):
    return page.evaluate("() => !!document.getElementById('ret-confirm-dialog')")


def wait_dialog(page):
    page.wait_for_selector("#ret-confirm-dialog", state="visible", timeout=DEFAULT_WAIT_MS)


def keep_editing(page):
    human_click(page, "#ret-confirm-cancel", "Keep editing")
    page.wait_for_selector("#ret-confirm-dialog", state="detached", timeout=DEFAULT_WAIT_MS)


def discard(page):
    human_click(page, "#ret-confirm-ok", "Discard")


def expect_stayed(page, url_before, typed, what):
    page.wait_for_timeout(600)
    assert page.url == url_before, f"{what}: the page LEFT to {page.url} although the person said keep editing"
    assert savebar_up(page), f"{what}: the Unsaved bar is gone after 'keep editing'"
    assert page.input_value("#credit-loyalty") == typed, f"{what}: the typed value did not survive 'keep editing'"


def reveal_row(page, section):
    """Open the sidebar group holding `section` the way a person does (click its
    header) when it is folded shut."""
    key = page.evaluate(
        """(s) => { const row = document.querySelector('.sub-nav-item[data-section="' + s + '"]');
                    if (!row) return null; const box = row.closest('[data-group-items]');
                    return (box && box.hidden) ? box.getAttribute('data-group-items') : ''; }""", section)
    assert key is not None, f"sidebar has no row for {section}"
    if key:
        human_click(page, f'.sub-nav-group-label[data-group-toggle="{key}"]', f"group header for {section}")
        page.wait_for_timeout(250)


def click_row(page, section):
    reveal_row(page, section)
    human_click(page, f'.sub-nav-item[data-section="{section}"]', f"sidebar row {section}")


# --------------------------------------------------------------------------- ext
def ext_case(browser, base, section):
    ctx, page = open_page(browser, base)
    try:
        typed = dirty_hub(page)
        url = page.url
        click_row(page, section)
        wait_dialog(page)
        keep_editing(page)
        expect_stayed(page, url, typed, f"row {section}")
        # control 1: 'Discard' really leaves for that page
        click_row(page, section)
        wait_dialog(page)
        discard(page)
        page.wait_for_url(f"**{EXTERNAL[section]}*", timeout=DEFAULT_WAIT_MS)
    finally:
        ctx.close()


def ext_clean_case(browser, base, section):
    """Control: a hub with NO unsaved edits leaves at once, no prompt."""
    ctx, page = open_page(browser, base)
    try:
        clean_hub(page)
        click_row(page, section)
        page.wait_for_url(f"**{EXTERNAL[section]}*", timeout=DEFAULT_WAIT_MS)
    finally:
        ctx.close()


# --------------------------------------------------------------------------- more
def more_case(browser, base):
    ctx, page = open_page(browser, base, viewport=PHONE)
    try:
        typed = dirty_hub(page)
        url = page.url
        human_click(page, "#sub-tab-more", "More tab")
        page.locator(".sub-more-row").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        sel = '.sub-more-row[onclick*="_leaveToPage(\'/static/whatsapp.html\')"]'
        human_click(page, sel, "More: WhatsApp", scroll_list=True)
        wait_dialog(page)
        keep_editing(page)
        expect_stayed(page, url, typed, "More sheet WhatsApp row")
        sheet_open = page.evaluate("() => !!document.getElementById('sub-more-sheet')")
        assert not sheet_open, "the More sheet is still open behind 'Keep editing'"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- licence
def licence_case(browser, base, via):
    phone = via == "more"
    ctx, page = open_page(browser, base, viewport=PHONE if phone else VIEWPORT)
    try:
        typed = dirty_hub(page)
        url = page.url
        if via == "identity":
            human_click(page, "#sub-identity-chip-btn", "identity chip")
            human_click(page, '#sub-identity-menu .sub-identity-menu-item[onclick*="openLicensing"]', "Plan & devices")
        elif via == "gear":
            human_click(page, "#sub-settings-gear-btn", "Settings gear")
            human_click(page, '#sub-settings-menu .sub-identity-menu-item[onclick*="openLicensing"]', "License & devices")
        else:
            human_click(page, "#sub-tab-more", "More tab")
            page.locator(".sub-more-row").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            human_click(page, '.sub-more-row[onclick*="openLicensing"]', "More: licence row", scroll_list=True)
        wait_dialog(page)
        keep_editing(page)
        expect_stayed(page, url, typed, f"licence via {via}")
    finally:
        ctx.close()


def licence_leaves_case(browser, base):
    """Control: 'Discard' really reaches the licensing page."""
    ctx, page = open_page(browser, base)
    try:
        dirty_hub(page)
        human_click(page, "#sub-identity-chip-btn", "identity chip")
        human_click(page, '#sub-identity-menu .sub-identity-menu-item[onclick*="openLicensing"]', "Plan & devices")
        wait_dialog(page)
        discard(page)
        page.wait_for_url("**/static/licensing.html*", timeout=DEFAULT_WAIT_MS)
    finally:
        ctx.close()


# --------------------------------------------------------------------------- logo
def logo_case(browser, base):
    ctx, page = open_page(browser, base)
    try:
        typed = dirty_hub(page)
        url = page.url
        human_click(page, ".sub-sidebar-brand.aura-logo", "brand logo")
        wait_dialog(page)
        keep_editing(page)
        expect_stayed(page, url, typed, "brand logo")
        assert page.evaluate("() => SubsystemApp.currentSection") == "admin-center", "logo moved the screen on 'keep editing'"
        human_click(page, ".sub-sidebar-brand.aura-logo", "brand logo")
        wait_dialog(page)
        discard(page)
        page.wait_for_function("() => SubsystemApp.currentSection === 'dashboard'", timeout=DEFAULT_WAIT_MS)
    finally:
        ctx.close()


def logo_clean_case(browser, base):
    ctx, page = open_page(browser, base)
    try:
        clean_hub(page)
        human_click(page, ".sub-sidebar-brand.aura-logo", "brand logo")
        page.wait_for_function("() => SubsystemApp.currentSection === 'dashboard'", timeout=DEFAULT_WAIT_MS)
        assert not dialog_up(page), "a clean hub still asked before the logo"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- claim
def claim_case(browser, base, dirty, answer):
    ctx, page = open_page(browser, base)
    try:
        posts = []
        page.on("request", lambda r: posts.append(r.url) if "/api/devices/me/claim-admin" in r.url and r.method == "POST" else None)
        if dirty:
            dirty_hub(page)
        else:
            clean_hub(page)
        # the same function the gear-menu row calls; the row itself is only drawn
        # when this computer is NOT already the main one
        page.evaluate("() => { window.__claim = SubsystemApp._claimAdminDeviceFromMenu(); }")
        if dirty:
            wait_dialog(page)
            if answer == "keep":
                keep_editing(page)
                page.wait_for_timeout(700)
                assert posts == [], f"the server was called although the person said keep editing: {posts}"
            else:
                discard(page)
                page.wait_for_timeout(900)
                assert len(posts) == 1, f"'Discard' should let the claim through exactly once, saw {posts}"
        else:
            page.wait_for_timeout(900)
            assert not dialog_up(page), "a clean hub still asked before the claim"
            assert len(posts) == 1, f"a clean hub should claim straight away, saw {posts}"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- unload
def unload_case(browser, base, dirty):
    ctx, page = open_page(browser, base)
    try:
        seen = []

        def on_dialog(d):
            seen.append(d.type)
            d.dismiss() if dirty else d.accept()

        page.on("dialog", on_dialog)
        if dirty:
            dirty_hub(page)
        else:
            clean_hub(page)
            page.locator("#sh-root .sh-nav-item").first.click()  # a real gesture, like a person
        page.evaluate("() => { window.__marker = 'still-here'; }")
        page.evaluate("() => { setTimeout(() => location.reload(), 0); }")
        page.wait_for_timeout(1500)
        if dirty:
            assert "beforeunload" in seen, f"a dirty hub was reloaded with NO browser prompt (dialogs seen: {seen})"
            assert page.evaluate("() => window.__marker") == "still-here", "the page reloaded although the person dismissed the prompt"
        else:
            assert seen == [], f"a clean hub triggered a browser prompt: {seen}"
            page.wait_for_load_state("domcontentloaded")
            assert page.evaluate("() => window.__marker === undefined"), "control: the clean reload did not happen"
    finally:
        ctx.close()


def unload_after_discard_case(browser, base):
    """After 'Discard' in OUR prompt the browser must not ask a second time."""
    ctx, page = open_page(browser, base)
    try:
        seen = []
        page.on("dialog", lambda d: (seen.append(d.type), d.accept()))
        dirty_hub(page)
        click_row(page, "whatsapp")
        wait_dialog(page)
        discard(page)
        page.wait_for_url("**/static/whatsapp.html*", timeout=DEFAULT_WAIT_MS)
        assert seen == [], f"the browser asked again right after 'Discard': {seen}"
    finally:
        ctx.close()


def unload_allowed_leave_case(browser, base):
    """A guard that ANSWERS 'leave' but is still dirty (its work is not cleared by
    answering) must not make the browser ask a second time. The hub clears its own
    dirtiness on Discard, so it cannot prove `_allowUnload`; this stubborn guard can."""
    ctx, page = open_page(browser, base)
    try:
        seen = []
        page.on("dialog", lambda d: (seen.append(d.type), d.accept()))
        clean_hub(page)
        page.evaluate("() => { window.SubsystemApp.registerLeaveGuard(() => true, { isDirty: () => true }); }")
        click_row(page, "whatsapp")
        page.wait_for_url("**/static/whatsapp.html*", timeout=DEFAULT_WAIT_MS)
        assert seen == [], f"the browser asked although the guard already said leave: {seen}"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- busy
HOLD ="""() => {
  window.__calls = 0; window.__release = null;
  if (window.__unreg) window.__unreg();
  window.__unreg = SubsystemApp.registerLeaveGuard(() => { window.__calls += 1; return new Promise((r) => { window.__release = r; }); });
}"""


def busy_stale_case(browser, base):
    ctx, page = open_page(browser, base)
    try:
        page.evaluate(HOLD)
        page.evaluate("() => { SubsystemApp._navigate('products'); }")
        page.wait_for_function("() => window.__calls === 1", timeout=DEFAULT_WAIT_MS)
        assert page.evaluate("() => SubsystemApp._leavePromptBusy") is True
        page.wait_for_timeout(2400)  # stale: busy > 2 s and no dialog on screen
        page.evaluate("() => { SubsystemApp._navigate('pos'); }")
        page.wait_for_timeout(500)
        calls = page.evaluate("() => window.__calls")
        assert calls == 2, f"a prompt that never settled locked every later exit (guard asked {calls}x, expected 2)"
    finally:
        ctx.close()


def busy_real_dialog_case(browser, base):
    """The escape must NOT fire while a real dialog is on screen."""
    ctx, page = open_page(browser, base)
    try:
        page.evaluate(HOLD)
        page.evaluate("() => { SubsystemApp._navigate('products'); }")
        page.wait_for_function("() => window.__calls === 1", timeout=DEFAULT_WAIT_MS)
        page.evaluate("() => { const d = document.createElement('div'); d.setAttribute('role', 'dialog'); d.id = 'x-real-dialog'; document.body.appendChild(d); }")
        page.wait_for_timeout(2400)
        page.evaluate("() => { SubsystemApp._navigate('pos'); }")
        page.wait_for_timeout(500)
        calls = page.evaluate("() => window.__calls")
        assert calls == 1, f"a second prompt opened over a real dialog (guard asked {calls}x, expected 1)"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- main
def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_s_leave_exits_"))
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
            p0.fill("#su-company", "Leave Exits Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()

            if wanted("ext"):
                for sec in EXTERNAL:
                    check("ext", f"dirty hub + sidebar row {sec}: prompt, keep editing stays, discard leaves",
                          lambda s=sec: ext_case(browser, base, s))
                check("ext", "control: a clean hub leaves for WhatsApp with no prompt",
                      lambda: ext_clean_case(browser, base, "whatsapp"))
            if wanted("more"):
                check("more", "phone More sheet WhatsApp row asks, and the sheet closes on 'Keep editing'",
                      lambda: more_case(browser, base))
            if wanted("licence"):
                for via in ("identity", "gear", "more"):
                    check("licence", f"dirty hub + licence entry via {via}: prompt, keep editing stays",
                          lambda v=via: licence_case(browser, base, v))
                check("licence", "control: discard reaches the licensing page", lambda: licence_leaves_case(browser, base))
            if wanted("logo"):
                check("logo", "dirty hub + brand logo: prompt, keep editing stays, discard goes home",
                      lambda: logo_case(browser, base))
                check("logo", "control: a clean hub goes home with no prompt", lambda: logo_clean_case(browser, base))
            if wanted("claim"):
                check("claim", "dirty hub + claim: keep editing -> ZERO claim requests",
                      lambda: claim_case(browser, base, True, "keep"))
                check("claim", "dirty hub + claim: discard -> exactly ONE claim request",
                      lambda: claim_case(browser, base, True, "discard"))
                check("claim", "control: a clean hub claims straight away, no prompt",
                      lambda: claim_case(browser, base, False, None))
            if wanted("unload"):
                check("unload", "dirty hub + reload: the browser's own prompt appears and the page stays",
                      lambda: unload_case(browser, base, True))
                check("unload", "control: a clean hub reloads with no browser prompt",
                      lambda: unload_case(browser, base, False))
                check("unload", "after 'Discard' in our prompt the browser does not ask a second time",
                      lambda: unload_after_discard_case(browser, base))
                check("unload", "a guard that says leave but stays dirty: the browser does not ask a second time",
                      lambda: unload_allowed_leave_case(browser, base))
            if wanted("busy"):
                check("busy", "a prompt that never settles frees itself after 2 s (no dialog on screen)",
                      lambda: busy_stale_case(browser, base))
                check("busy", "but never while a real dialog is on screen",
                      lambda: busy_real_dialog_case(browser, base))
        finally:
            backend.stop()
            browser.close()

    failed = [r for r in _pe.RESULTS if not r[1]]
    print(f"\nRESULTS: {len(_pe.RESULTS) - len(failed)}/{len(_pe.RESULTS)} passed", flush=True)
    for name, ok, msg in failed:
        print(f"  FAIL {name}: {msg}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
