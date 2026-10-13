"""
retail_p1b_leave_guard_e2e.py -- SubsystemApp.registerLeaveGuard, proved in a REAL
browser with real clicks and real keys. STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1b_leave_guard_e2e.py [--only tag,tag]

The guard is what the Settings hub uses to ask "you have unsaved changes" before
the person walks away. This suite registers a RECORDING guard from the page, so
it proves the hook itself; it does not prove the hub's dialog (crew H's suite).

Tags (one guard each, so a mutation proof can re-run it alone):

  decide   a guard that returns false keeps you on the screen; true, undefined,
           a throw and a rejected promise all let you leave (strictly `false`
           means stay -- a broken guard must never trap the person).
  busy     while a prompt is open a second navigate is ignored (the guard is
           asked ONCE), and the shell is usable again once it is answered.
  same     re-opening the section you are already on never asks (the 60 s
           dashboard refresh and the language switch depend on this).
  surfaces the sidebar, the phone tab bar, the More sheet, the settings menu
           and the Ctrl+K palette all go through the guard.
  follow   the palette's follow-up action (New product) does not run when the
           guard said stay, and DOES run when it said go (the control).
  logout   signing out asks too: stay = no logout request and still signed in;
           go = the logout request is sent. Identity menu and the phone More
           sheet.
  unreg    the function registerLeaveGuard returns really removes the guard.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    DEFAULT_WAIT_MS, VIEWPORT, Backend, _owner_company_id, human_click,
)
from retail_p1b_palette_e2e import (  # noqa: E402
    check, open_with_ctrl_k, pick_port,
)
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@leaveguard.test"
OWNER_PASSWORD = "LeaveGuardOwner_123!"
PHONE = {"width": 390, "height": 844}

INSTALL = """(mode) => {
  window.__calls = []; window.__mode = mode;
  if (window.__unreg) window.__unreg();
  window.__unreg = SubsystemApp.registerLeaveGuard((target, kind) => {
    window.__calls.push([target, kind]);
    const m = window.__mode;
    if (m === 'throw') throw new Error('boom');
    if (m === 'reject') return Promise.reject(new Error('rejected'));
    if (m === 'hold') return new Promise((r) => { window.__release = r; });
    if (m === 'undefined') return undefined;
    return m === 'true';
  });
}"""
STATE = "() => ({section: SubsystemApp.currentSection, calls: window.__calls, busy: SubsystemApp._leavePromptBusy})"


def open_page(browser, base, **kw):
    return _open_page(browser, base, email=OWNER_EMAIL, password=OWNER_PASSWORD, **kw)


def guarded(browser, base, mode, viewport=VIEWPORT):
    ctx, page = open_page(browser, base, viewport=viewport)
    page.evaluate(INSTALL, mode)
    return ctx, page


def state(page):
    return page.evaluate(STATE)


def settle(page, ms=700):
    page.wait_for_timeout(ms)


def expect_stay(page, target, kind="navigate", start="dashboard"):
    settle(page)
    st = state(page)
    assert st["section"] == start, f"LEFT {start} for {st['section']} although the guard said stay (calls={st['calls']})"
    assert st["calls"] == [[target, kind]], f"guard calls were {st['calls']}, expected [[{target!r}, {kind!r}]]"


def expect_go(page, target):
    page.wait_for_function("(t) => SubsystemApp.currentSection === t", arg=target, timeout=DEFAULT_WAIT_MS)
    st = state(page)
    assert st["calls"] and st["calls"][0][0] == target, f"guard calls were {st['calls']}"


# --------------------------------------------------------------------------- decide
def decide_case(browser, base, mode, leaves, extra_errors=False):
    ctx, page = guarded(browser, base, mode)
    try:
        logged = []
        page.on("console", lambda m: logged.append(m.text) if m.type == "error" else None)
        human_click(page, '.sub-nav-item[data-section="pos"]', "Point of Sale row")
        if leaves:
            expect_go(page, "pos")
        else:
            expect_stay(page, "pos")
        if extra_errors:
            settle(page, 200)
            assert any("leave-guard" in x for x in logged), f"a failing guard was not logged: {logged}"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- busy
def busy_case(browser, base):
    ctx, page = guarded(browser, base, "hold")
    try:
        human_click(page, '.sub-nav-item[data-section="pos"]', "Point of Sale row")
        page.wait_for_function("() => window.__calls.length === 1", timeout=DEFAULT_WAIT_MS)
        assert state(page)["busy"] is True, "the shell does not know a prompt is open"
        # A second, different destination while the first prompt is unanswered.
        page.evaluate("() => { window.__second = SubsystemApp._navigate('products'); }")
        settle(page, 300)
        st = state(page)
        assert len(st["calls"]) == 1, f"a second prompt was opened while one was showing: {st['calls']}"
        assert st["section"] == "dashboard", f"moved while a prompt was open: {st['section']}"
        second = page.evaluate("() => window.__second instanceof Promise ? window.__second.then((v) => v) : window.__second")
        assert second is False, f"the ignored second navigate returned {second!r}, not false"
        # Answer "go": ONLY the first destination happens.
        page.evaluate("() => window.__release(true)")
        expect_go(page, "pos")
        settle(page, 400)
        assert state(page)["section"] == "pos", f"the ignored navigate leaked through: {state(page)['section']}"
        # And the shell is not wedged: a later leave asks again.
        page.evaluate("() => { window.__mode = 'false'; }")
        human_click(page, '.sub-nav-item[data-section="dashboard"]', "Home row")
        settle(page, 500)
        st = state(page)
        assert st["busy"] is False and len(st["calls"]) == 2 and st["section"] == "pos", f"shell wedged after a prompt: {st}"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- same
def same_case(browser, base):
    ctx, page = guarded(browser, base, "false")
    try:
        # Direct re-entry (what the dashboard refresh timer, the language
        # switch and the import wizard's Done all do) ...
        r = page.evaluate("() => SubsystemApp._navigate(SubsystemApp.currentSection)")
        assert r is True, f"same-section _navigate returned {r!r}; callers expect it to run at once"
        # ... and a person clicking the row they are already on.
        human_click(page, '.sub-nav-item.active', "the active row")
        settle(page, 600)
        st = state(page)
        assert st["calls"] == [], f"re-opening the current section asked the guard: {st['calls']}"
        assert st["section"] == "dashboard"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- surfaces
def sidebar_case(browser, base):
    ctx, page = guarded(browser, base, "false")
    try:
        human_click(page, '.sub-nav-item[data-section="pos"]', "Point of Sale row")
        expect_stay(page, "pos")
    finally:
        ctx.close()


def tab_case(browser, base):
    ctx, page = guarded(browser, base, "false", viewport=PHONE)
    try:
        human_click(page, '.sub-tab[data-tab="products"]', "Stock tab")
        expect_stay(page, "products")
    finally:
        ctx.close()


def more_case(browser, base):
    ctx, page = guarded(browser, base, "false", viewport=PHONE)
    try:
        human_click(page, "#sub-tab-more", "More tab")
        page.locator(".sub-more-row").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        rows = page.evaluate("() => [...document.querySelectorAll('.sub-more-row')].map(r => (r.getAttribute('onclick')||'').match(/_navigate\\('([^']+)'\\)/)?.[1] || null)")
        target = next((r for r in rows if r and r not in ("dashboard", "products", "pos", "customers")), None)
        assert target, f"More sheet has no navigating row: {rows}"
        human_click(page, f'.sub-more-row[onclick*="_navigate(\'{target}\')"]', f"More row {target}")
        expect_stay(page, target)
    finally:
        ctx.close()


def menu_case(browser, base):
    ctx, page = guarded(browser, base, "false")
    try:
        # UI-3b: the gear opens the hub directly (no menu), so the click that must be guarded is the gear itself.
        human_click(page, "#sub-settings-gear-btn", "Settings gear")
        expect_stay(page, "admin-center")
    finally:
        ctx.close()


def palette_case(browser, base):
    ctx, page = guarded(browser, base, "false")
    try:
        open_with_ctrl_k(page)
        page.keyboard.type("Products")
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        settle(page, 800)
        st = state(page)
        assert st["section"] == "dashboard", f"the palette LEFT the screen although the guard said stay: {st}"
        assert st["calls"] == [["products", "navigate"]], f"palette did not ask the guard: {st['calls']}"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- follow
def follow_case(browser, base, mode, runs):
    ctx, page = guarded(browser, base, mode)
    try:
        page.evaluate("() => { window.__added = 0; RetailSystem._openAddProduct = () => { window.__added += 1; }; }")
        open_with_ctrl_k(page)
        page.keyboard.type("New product")
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        if runs:
            page.wait_for_function("() => window.__added > 0", timeout=DEFAULT_WAIT_MS)
        else:
            # The follow-up runs AFTER the palette's own paint waits (two of them,
            # each up to PAINT_WAIT_MS). Wait past both, or a follow-up that wrongly
            # fires late is missed and this "passes" for the wrong reason.
            settle(page, 2 * page.evaluate("() => AuraPalette.PAINT_WAIT_MS") + 1500)
            added = page.evaluate("() => window.__added")
            assert added == 0, f"'New product' still ran its follow-up ({added}x) after the guard said stay"
            assert state(page)["section"] == "dashboard", "left the screen although the guard said stay"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- logout
def logout_case(browser, base, mode, phone, goes):
    ctx, page = guarded(browser, base, mode, viewport=PHONE if phone else VIEWPORT)
    try:
        reqs = []
        page.on("request", lambda r: reqs.append(r.url) if "/api/auth/logout" in r.url else None)
        if phone:
            human_click(page, "#sub-tab-more", "More tab")
            page.locator(".sub-more-row.danger").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            human_click(page, ".sub-more-row.danger", "More: Log Out", scroll_list=True)
        else:
            human_click(page, "#sub-identity-chip-btn", "identity chip")
            human_click(page, "#sub-identity-menu .is-danger", "Log Out")
        if goes:
            page.wait_for_function("() => !SubsystemApp.active", timeout=DEFAULT_WAIT_MS)
            assert reqs, "guard said go but no logout request was sent"
            assert page.evaluate(STATE)["calls"] == [["logout", "logout"]]
        else:
            settle(page, 900)
            assert reqs == [], f"logout request sent although the guard said stay: {reqs}"
            assert page.evaluate("() => !!SubsystemApp.active"), "signed out although the guard said stay"
            assert page.evaluate(STATE)["calls"] == [["logout", "logout"]], "logout did not ask the guard"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- unreg
def unreg_case(browser, base):
    ctx, page = guarded(browser, base, "false")
    try:
        page.evaluate("() => window.__unreg()")
        human_click(page, '.sub-nav-item[data-section="pos"]', "Point of Sale row")
        page.wait_for_function("() => SubsystemApp.currentSection === 'pos'", timeout=DEFAULT_WAIT_MS)
        assert state(page)["calls"] == [], f"an unregistered guard was still asked: {state(page)['calls']}"
    finally:
        ctx.close()


def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_p1b_leave_"))
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
            p0.fill("#su-company", "Leave Guard Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()

            if wanted("decide"):
                check("decide", "guard returns false: clicking Point of Sale stays on Home",
                      lambda: decide_case(browser, base, "false", False))
                check("decide", "guard returns true: clicking Point of Sale leaves",
                      lambda: decide_case(browser, base, "true", True))
                check("decide", "guard returns undefined: leaves (only a strict false stays)",
                      lambda: decide_case(browser, base, "undefined", True))
                check("decide", "guard throws: leaves, and the failure is logged",
                      lambda: decide_case(browser, base, "throw", True, extra_errors=True))
                check("decide", "guard rejects: leaves, and the failure is logged",
                      lambda: decide_case(browser, base, "reject", True, extra_errors=True))
            if wanted("busy"):
                check("busy", "a second navigate while the prompt is open is ignored, and the shell recovers",
                      lambda: busy_case(browser, base))
            if wanted("same"):
                check("same", "re-opening the current section never asks the guard",
                      lambda: same_case(browser, base))
            if wanted("surfaces"):
                check("surfaces", "sidebar row goes through the guard", lambda: sidebar_case(browser, base))
                check("surfaces", "phone tab bar goes through the guard", lambda: tab_case(browser, base))
                check("surfaces", "phone More sheet goes through the guard", lambda: more_case(browser, base))
                check("surfaces", "settings menu goes through the guard", lambda: menu_case(browser, base))
                check("surfaces", "Ctrl+K palette goes through the guard", lambda: palette_case(browser, base))
            if wanted("follow"):
                check("follow", "palette 'New product': guard says stay -> the add form does not open",
                      lambda: follow_case(browser, base, "false", False))
                check("follow", "control: guard says go -> the add form opens",
                      lambda: follow_case(browser, base, "true", True))
            if wanted("logout"):
                check("logout", "identity menu Log Out: stay -> still signed in, no logout request",
                      lambda: logout_case(browser, base, "false", False, False))
                check("logout", "phone More sheet Log Out: stay -> still signed in, no logout request",
                      lambda: logout_case(browser, base, "false", True, False))
                check("logout", "control: guard says go -> the logout request is sent",
                      lambda: logout_case(browser, base, "true", False, True))
            if wanted("unreg"):
                check("unreg", "the returned function removes the guard", lambda: unreg_case(browser, base))
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
