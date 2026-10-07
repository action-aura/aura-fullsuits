"""P1b crew H (settings hub) -- live-browser verification for item 4 (Settings
hub) and the front half of item 6 (Numbering: branch code + readable numbers).

Boots the real Flask backend in-process on a fresh temp AURA_APP_DATA (the same
proven recipe retail_p1a_till_e2e.py uses): sign up through the real first-run
form while unlicensed, THEN seed an active licence, THEN reload. Drives a real
headless Chromium via Playwright against the ACTUAL running app.

Standalone script, not a pytest *_test.py: prints PASS/FAIL per check and exits
1 if anything failed. H_ONLY="tax|credit" runs just the checks whose name
contains one of those substrings (dev aid; unset = every check).

What it proves, each with a mutation proof recorded in the crew report:
  - every section is reachable by a real mouse click (rule 6b: inside the
    viewport, topmost at its centre, page.mouse.click -- never force=True);
  - the Unsaved bar exists only while something differs from what was saved,
    Discard puts the saved values back, Save persists across a reload;
  - a refused Save (bad credit number) keeps the bar up and changes nothing;
  - the receipt preview is character-for-character what the REAL print path
    puts in #ret-print-frame, EN and AR, 80 and 58 mm -- and it follows a
    typed-but-unsaved change that the print (rightly) does not yet show;
  - who sees what, BOTH directions: owner all, delegated manager only Tills +
    Team, cashier the lock panel;
  - the printer/drawer test lives in Hardware and is gone from the scanner
    screen (except for someone who cannot open Hardware);
  - the reorder-requests card is in Purchasing, not in Settings;
  - one tax rate for a whole category changes exactly that category;
  - the branch code + readable-numbers card sends exactly what changed.

Run with the SYSTEM Python (Playwright lives there):
    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_p1b_hub_e2e.py
"""
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# products/retail/tests/<this file> -> three levels up is the repo (worktree) root
REPO = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO / "products" / "retail" / "backend"
for _p in (str(REPO), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRATCH = Path(r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p1b\H2")
SCRATCH.mkdir(parents=True, exist_ok=True)

DATA = Path(tempfile.mkdtemp(prefix="aura_p1b_hub_", dir=str(SCRATCH)))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

import app as _app_module  # noqa: E402
flask_app = _app_module.init_app()

import logging  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from werkzeug.serving import make_server  # noqa: E402


def port_is_free(port):
    """A plain bind WITHOUT SO_REUSEADDR (werkzeug sets it, and on Windows that
    lets a second listener share an occupied port) plus a connect probe."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


server = None
PORT = None
# crew T's range for the P2S harmonisation run (6280-6299); the range is per
# run, not per file -- move it if another crew owns those ports. (Chromium
# refuses 6000 as an "unsafe port".)
for candidate in range(6280, 6300):
    if not port_is_free(candidate):
        continue
    try:
        server = make_server("127.0.0.1", candidate, flask_app, threaded=True)
        PORT = candidate
        break
    except OSError:
        continue
if server is None:
    print("FATAL: no free port 6280-6299")
    sys.exit(2)

threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(1.2)
BASE = f"http://127.0.0.1:{PORT}"
print(f"Backend up at {BASE}  AURA_APP_DATA={DATA}")

# ---------------------------------------------------------------------------
# Harness plumbing
# ---------------------------------------------------------------------------
RESULTS = []
PAGE_ERRORS = []
CURRENT_CHECK = [""]   # the name of the check now running (go() logs it beside a leave prompt)
_ONLY = [s for s in os.environ.get("H_ONLY", "").split("|") if s]
if _ONLY:
    print(f"FILTERED RUN (H_ONLY={_ONLY!r}) -- not a full verification")


def check(name, fn):
    if _ONLY and not any(s in name for s in _ONLY):
        return
    CURRENT_CHECK[0] = name
    try:
        fn()
    except AssertionError as e:
        RESULTS.append((name, False))
        print(f"FAIL: {name}\n      {e}")
        _cleanup_after_failure()
        return
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False))
        print(f"ERROR: {name}\n      {type(e).__name__}: {e}")
        _cleanup_after_failure()
        return
    RESULTS.append((name, True))
    print(f"PASS: {name}")


def _cleanup_after_failure():
    """A failed check must not leave a modal open for every check after it."""
    for pg in ("owner",):
        try:
            globals()[pg].evaluate("() => document.querySelectorAll('.ret-modal-overlay').forEach(el => el.remove())")
        except Exception:  # noqa: BLE001
            pass


def dismiss_intro_overlay(page):
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=3000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:  # noqa: BLE001
        pass


def api(page, method, path, body=None):
    fn = getattr(page.request, method.lower())
    resp = fn(BASE + path, data=body) if body is not None else fn(BASE + path)
    try:
        j = resp.json()
    except Exception:  # noqa: BLE001
        j = {}
    return resp.status, j


LEAVE_PROMPTS = []


def go(page, section, wait_ms=900):
    """Navigate like the app's own links do. The hub now asks 'Discard unsaved
    changes?' when a check leaves it dirty; _navigate then returns a Promise that
    only settles once somebody answers, so this call must NOT await it (it would
    hang the whole run). A check that ends with edits on screen is answered
    'Discard' here and (check name, section) is logged in LEAVE_PROMPTS, which the
    last-but-one check of the run asserts is EMPTY (H2 item 6: it used to be
    written and never read, so a check that left the hub dirty was answered
    quietly and nobody ever knew); the leave-guard
    checks below drive the prompt by hand and never come through this shortcut
    while they are asserting on it."""
    page.evaluate("(s) => { SubsystemApp._navigate(s); }", section)
    try:
        page.wait_for_selector("#ret-confirm-dialog", timeout=250)
    except Exception:  # noqa: BLE001 - no prompt: the normal case
        pass
    else:
        LEAVE_PROMPTS.append((CURRENT_CHECK[0], section))
        real_click(page, "#ret-confirm-ok", "Discard (a check ended with unsaved edits)")
    page.wait_for_timeout(wait_ms)


_CENTRE_JS = """(el) => {
  const r = el.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const inView = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= vw && r.bottom <= vh;
  const hit = document.elementFromPoint(cx, cy);
  return {cx, cy, inView, vw, vh, box: [r.left, r.top, r.right, r.bottom],
          hits: !!hit && (hit === el || el.contains(hit)),
          hitDesc: hit ? (() => { const parts = []; let n = hit; for (let i = 0; n && i < 4; i++, n = n.parentElement) parts.push(n.tagName + '#' + n.id + '.' + String(n.className).slice(0, 40)); const hr = hit.getBoundingClientRect(); return parts.join(' < ') + ' rect=' + [hr.left, hr.top, hr.right, hr.bottom].map(Math.round) + ' style=' + (hit.getAttribute('style') || '').slice(0, 80) + ' text=' + (hit.textContent || '').trim().slice(0, 40); })() : null};
}"""


def reachable(page, locator, what=""):
    """contract.md rule 6b: 'a person can click / see this' is three facts: the
    box is inside the viewport, the topmost thing at its centre IS the element,
    and (real_click) a real mouse click at that point is what gets sent."""
    locator.first.scroll_into_view_if_needed(timeout=3000)
    info = locator.first.evaluate(_CENTRE_JS)
    if not info["inView"]:
        # P1c: Chrome's scrollIntoViewIfNeeded treats a control that is clipped by
        # less than half a pixel as "already visible" and does nothing, which left a
        # card 0.28px past the bottom edge once Large's taller type shifted where an
        # earlier step had left the scroll. A person would simply scroll it into view;
        # so does this, with an explicit centre. The claim is unchanged: the box must
        # END UP fully inside the viewport (a control taller than the viewport, or
        # one that cannot be scrolled to, still fails on the assert below).
        locator.first.evaluate("el => el.scrollIntoView({block: 'center'})")
        info = locator.first.evaluate(_CENTRE_JS)
    assert info["inView"], f"{what}: box {info['box']} is outside the {info['vw']}x{info['vh']} viewport"
    assert info["hits"], f"{what}: elementFromPoint at its centre hits {info['hitDesc']}, not the element"
    return info


def real_click(page, selector, what=""):
    loc = page.locator(selector)
    info = reachable(page, loc, what or selector)
    page.mouse.click(info["cx"], info["cy"])
    return info


def activate_employee(user_id, password):
    conn = registry_conn()
    try:
        conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?",
                     (hash_password(password), user_id))
        conn.commit()
    finally:
        conn.close()


def grant_capability(user_id, code):
    conn = registry_conn()
    try:
        conn.execute("UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                     (user_accounts.ACCESS_FULL, user_id, code))
        conn.commit()
    finally:
        conn.close()


def set_lang(page, lang):
    page.evaluate("(l) => AuraI18n.setLang(l)", lang)
    page.wait_for_timeout(500)


def set_theme(page, theme):
    page.evaluate("(th) => ThemeEngine.apply(th)", theme)
    page.wait_for_timeout(200)


def hub_ready(page, owner_view=True, preview=True):
    page.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
    page.wait_for_function("() => !!document.getElementById('device-branch-select') && "
                           "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
    if owner_view:
        page.wait_for_function("() => !!document.getElementById('branding-card') && "
                               "!document.querySelector('#numbering-codes-body .sh-pane-loading')", timeout=15000)
        if preview:  # the paper is drawn only while Shop & receipt is the section on screen
            page.wait_for_function("() => { const f = document.getElementById('sh-preview-frame'); "
                                   "return f && f.contentDocument && f.contentDocument.body && "
                                   "f.contentDocument.body.innerText.length > 20; }", timeout=15000)
    page.wait_for_timeout(300)


def open_hub(page, section=None, owner_view=True):
    go(page, "admin-center", 400)
    hub_ready(page, owner_view)
    if section:
        real_click(page, f'#sh-root .sh-nav-item[data-sh="{section}"]', f"nav {section}")
        page.wait_for_timeout(500)


def visible_panes(page):
    return page.evaluate("() => [...document.querySelectorAll('[data-sh-pane]')].filter(p => p.offsetParent !== null || "
                         "getComputedStyle(p).display !== 'none').map(p => p.dataset.shPane)")


def savebar_up(page):
    return page.evaluate("() => { const b = document.getElementById('sh-savebar'); "
                         "return !!b && !b.hidden && getComputedStyle(b).display !== 'none'; }")


def set_input(page, selector, value):
    """Type like a person: focus, select all, type, blur (fires input + change)."""
    loc = page.locator(selector)
    reachable(page, loc, selector)
    if loc.evaluate("(e) => e.tagName") == "SELECT":
        loc.select_option(value)
    else:
        loc.click()
        loc.fill(str(value))
        loc.blur()
    page.wait_for_timeout(120)


from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    browser = p.chromium.launch()

    # =========================================================================
    # OWNER: sign up, licence, seed data
    # =========================================================================
    owner_ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    owner = owner_ctx.new_page()
    owner.on("pageerror", lambda exc: PAGE_ERRORS.append(("owner", str(exc) + " @ " + str(getattr(exc, "stack", ""))[:300])))
    owner.goto(BASE)
    dismiss_intro_overlay(owner)
    owner.wait_for_selector("#su-btn", timeout=20000)
    owner.fill("#su-name", "Aura Owner")
    owner.fill("#su-company", "P1b Hub Test Co")
    owner.fill("#su-email", "owner@p1bhub.local")
    owner.fill("#su-pass", "P1bHubTest12345!")
    owner.fill("#su-pass2", "P1bHubTest12345!")
    owner.click("#su-btn")
    owner.wait_for_selector("#sub-header-section", timeout=30000)
    owner.wait_for_timeout(1200)

    seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
    owner.reload()
    dismiss_intro_overlay(owner)
    owner.wait_for_selector("#sub-header-section", timeout=20000)
    owner.wait_for_timeout(800)

    # v42: this computer must be a REAL registered till, not a made-up terminal id.
    # A till's terminal id IS its registry device id, and the server refuses to hand
    # a till number (DEVICE_IDENTITY_CLASH) to a terminal the registry does not list
    # as active. So do what a real first-run shop does: the boot announce puts this
    # computer in the registry, then the owner claims it as the MAIN computer. The
    # owner above was made by the real signup route, so the founder's ADMIN_CREATED
    # row is there without planting anything.
    # What this can no longer catch: nothing was removed -- the numbering checks now
    # ALSO fail if a fresh shop cannot become its own main computer with these two
    # calls alone.
    _app_module._announce_device("boot")
    _st, _cl = api(owner, "POST", "/api/devices/me/claim-admin")
    assert _st == 200 and (_cl or {}).get("success") is True \
        and ((_cl or {}).get("device") or {}).get("is_admin_device") is True, (
        "the owner's computer could not become the main computer: %s %s" % (_st, _cl))

    api(owner, "POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})

    _, b = api(owner, "POST", "/api/sub/retail/categories", {"name": "Hub Drinks"})
    CAT_DRINKS = ((b or {}).get("data") or {}).get("id")
    _, b = api(owner, "POST", "/api/sub/retail/categories", {"name": "Hub Snacks"})
    CAT_SNACKS = ((b or {}).get("data") or {}).get("id")
    assert CAT_DRINKS and CAT_SNACKS, "category seeding failed"


    def create_product(name, sku, price, category_id):
        _, body = api(owner, "POST", "/api/sub/retail/products", {
            "name": name, "sku": sku, "sell_price": price, "cost_price": round(price / 2, 3),
            "category_id": category_id, "unit": "pc",
        })
        pid = ((body or {}).get("data") or {}).get("id")
        assert pid, f"product '{name}' was not created: {body}"
        return pid


    PID_D1 = create_product("Hub Cola", "H-D1", 1.5, CAT_DRINKS)
    PID_D2 = create_product("Hub Juice", "H-D2", 2.0, CAT_DRINKS)
    PID_S1 = create_product("Hub Chips", "H-S1", 0.75, CAT_SNACKS)

    # Employees: a cashier, and a delegated branch manager (CAP_EMPLOYEES).
    CASHIER_EMAIL, CASHIER_PW = "cashier@p1bhub.local", "CashierPW1b!"
    api(owner, "POST", "/api/admin/employees", {"email": CASHIER_EMAIL, "role": "cashier"})
    conn = registry_conn()
    try:
        CASHIER_ID = conn.execute("SELECT id FROM users WHERE email=?", (CASHIER_EMAIL,)).fetchone()["id"]
    finally:
        conn.close()
    activate_employee(CASHIER_ID, CASHIER_PW)

    MANAGER_EMAIL, MANAGER_PW = "manager@p1bhub.local", "ManagerPW1b!"
    api(owner, "POST", "/api/admin/employees", {"email": MANAGER_EMAIL, "role": "manager"})
    conn = registry_conn()
    try:
        MANAGER_ID = conn.execute("SELECT id FROM users WHERE email=?", (MANAGER_EMAIL,)).fetchone()["id"]
    finally:
        conn.close()
    grant_capability(MANAGER_ID, user_accounts.CAP_EMPLOYEES)
    activate_employee(MANAGER_ID, MANAGER_PW)


    def new_session(email, password):
        ctx = browser.new_context(viewport={"width": 1366, "height": 768})
        pg = ctx.new_page()
        pg.on("pageerror", lambda exc: PAGE_ERRORS.append((email, str(exc) + " @ " + str(getattr(exc, "stack", ""))[:300])))
        pg.goto(BASE)
        pg.wait_for_selector("body", timeout=15000)
        status, _ = api(pg, "POST", "/api/auth/login", {"email": email, "password": password})
        assert status == 200, f"login as {email} failed with status {status}"
        pg.goto(BASE)
        dismiss_intro_overlay(pg)
        pg.wait_for_selector("#sub-header-section", timeout=20000)
        pg.wait_for_timeout(800)
        return ctx, pg


    # A PLAIN manager: same role as the delegated one above but WITHOUT
    # CAP_EMPLOYEES -- someone whose printer/drawer server calls would 403 (F12).
    PLAIN_EMAIL, PLAIN_PW = "plainmgr@p1bhub.local", "PlainMgrPW1b!"
    api(owner, "POST", "/api/admin/employees", {"email": PLAIN_EMAIL, "role": "manager"})
    conn = registry_conn()
    try:
        PLAIN_ID = conn.execute("SELECT id FROM users WHERE email=?", (PLAIN_EMAIL,)).fetchone()["id"]
    finally:
        conn.close()
    activate_employee(PLAIN_ID, PLAIN_PW)

    cashier_ctx, cashier = new_session(CASHIER_EMAIL, CASHIER_PW)
    manager_ctx, manager = new_session(MANAGER_EMAIL, MANAGER_PW)
    plain_ctx, plainmgr = new_session(PLAIN_EMAIL, PLAIN_PW)
    print("SEEDING DONE.")

    # P1c added "display" (Screen size: a per-DEVICE preference, offered to everyone who can
    # open Settings -- it changes nothing in the shop's data).
    # EXTRAS (2026-10-07): the Extras set-up section sits after Tills.
    OWNER_SECTIONS = ["shop-receipt", "tax", "credit", "tills", "extras", "hardware", "display", "team", "data-backup", "plan-devices"]

    # =========================================================================
    # 1. Reaching the hub and every section (owner)
    # =========================================================================
    def owner_reaches_hub_from_the_gear_and_every_section():
        # The path a person takes: footer gear, then the Settings entry.
        real_click(owner, "#sub-settings-gear-btn", "settings gear")
        real_click(owner, '#sub-settings-menu .sub-identity-menu-item[data-section="admin-center"]', "settings menu entry")
        hub_ready(owner)
        markers = {
            "shop-receipt": "#brand-name",
            "tax": "#tax-mode",
            "credit": "#credit-mode",
            "tills": "#device-branch-card",
            "extras": None,
            "hardware": "#printer-card",
            "display": "#display-card",
            "team": None,
            "data-backup": None,
            "plan-devices": None,
        }
        seen = []
        for sid in OWNER_SECTIONS:
            real_click(owner, f'#sh-root .sh-nav-item[data-sh="{sid}"]', f"nav item {sid}")
            owner.wait_for_timeout(600)
            panes = visible_panes(owner)
            # COUNT THE CHECK: exactly the clicked section is showing, not
            # "some section rendered".
            assert panes == [sid], f"after clicking {sid} the visible panes are {panes}"
            cur = owner.evaluate("() => document.querySelector('#sh-root .sh-nav-item[aria-current=\"page\"]').dataset.sh")
            assert cur == sid, f"nav marks {cur!r} current after clicking {sid}"
            pane_loc = owner.locator(f'[data-sh-pane="{sid}"]')
            # a pane may be taller than the screen (it scrolls); what must hold is
            # that its top edge is on screen and it is the topmost thing there.
            top = pane_loc.evaluate("""(el) => { const r = el.getBoundingClientRect(); const hit = document.elementFromPoint(r.left + r.width / 2, Math.min(r.top + 40, window.innerHeight - 1));
              return {top: r.top, bottom: r.bottom, vh: window.innerHeight, hits: !!hit && el.contains(hit)}; }""")
            assert 0 <= top["top"] < top["vh"], f"pane {sid} top edge {top['top']} is off screen"
            assert top["hits"], f"pane {sid} is not what a person sees at its top: {top}"
            if markers[sid]:
                reachable(owner, owner.locator(markers[sid]), f"{sid} marker {markers[sid]}")
            txt = pane_loc.inner_text().strip()
            assert txt and "Loading" not in txt[:12], f"pane {sid} is empty or still loading: {txt[:60]!r}"
            seen.append(sid)
        assert seen == OWNER_SECTIONS
        # sections() is what the palette reads: same ids, all visible, labels already translated.
        secs = owner.evaluate("() => SettingsHub.sections()")
        assert [s["id"] for s in secs] == OWNER_SECTIONS, secs
        assert all(s["visible"] and s["label"] for s in secs), secs
        nav_labels = owner.eval_on_selector_all("#sh-root .sh-nav-item", "els => els.map(e => e.textContent.trim())")
        assert nav_labels == [s["label"] for s in secs], (nav_labels, secs)

    check("hub: owner reaches the hub from the gear and every one of the 9 sections by real click",
          owner_reaches_hub_from_the_gear_and_every_section)

    def open_api_lands_on_the_section():
        go(owner, "dashboard", 500)
        ok = owner.evaluate("() => SettingsHub.open('credit')")
        assert ok is True
        hub_ready(owner, preview=False)
        owner.wait_for_timeout(500)
        assert visible_panes(owner) == ["credit"], visible_panes(owner)
        # Mounted hub: open() just switches.
        assert owner.evaluate("() => SettingsHub.open('tills')") is True
        owner.wait_for_timeout(300)
        assert visible_panes(owner) == ["tills"], visible_panes(owner)
        # An id that does not exist / is not for this person is refused, not navigated.
        assert owner.evaluate("() => SettingsHub.open('nope')") is False

    check("hub: SettingsHub.open(id) lands on that section from elsewhere and when mounted; unknown id refused",
          open_api_lands_on_the_section)

    # =========================================================================
    # 2. Unsaved bar, Discard, Save (owner)
    # =========================================================================
    def savebar_only_when_changed_and_discard_restores():
        open_hub(owner, "credit")
        assert not savebar_up(owner), "the Unsaved bar is up before anything was changed"
        before = owner.input_value("#credit-loyalty")
        set_input(owner, "#credit-loyalty", "0.25")
        assert savebar_up(owner), "the Unsaved bar did not appear after a real change"
        bar_info = reachable(owner, owner.locator("#sh-save"), "Save button in the bar")
        # typing the saved value back means nothing differs: the bar goes away again.
        set_input(owner, "#credit-loyalty", before)
        assert not savebar_up(owner), "bar stayed up although the field is back to its saved value"
        set_input(owner, "#credit-loyalty", "0.25")
        set_input(owner, "#credit-mode", "unlimited")
        assert savebar_up(owner)
        real_click(owner, "#sh-discard", "Discard")
        owner.wait_for_timeout(400)
        assert owner.input_value("#credit-loyalty") == before, "Discard did not restore the loyalty value"
        assert owner.input_value("#credit-mode") == "none", "Discard did not restore the credit option"
        assert not savebar_up(owner), "bar stayed up after Discard"
        assert bar_info["inView"]

    check("hub: Unsaved bar appears only after a real change, goes when reverted, Discard restores",
          savebar_only_when_changed_and_discard_restores)

    def dirty_state_survives_switching_sections():
        open_hub(owner, "credit")
        set_input(owner, "#credit-loyalty", "0.5")
        real_click(owner, '#sh-root .sh-nav-item[data-sh="tax"]', "nav tax")
        owner.wait_for_timeout(300)
        assert savebar_up(owner), "the bar dropped when moving to another section with unsaved edits"
        real_click(owner, '#sh-root .sh-nav-item[data-sh="credit"]', "nav credit")
        assert owner.input_value("#credit-loyalty") == "0.5", "typed value lost on switching sections"
        real_click(owner, "#sh-discard", "Discard")

    check("hub: edits survive switching sections (one bar for the whole hub)", dirty_state_survives_switching_sections)

    def save_persists_across_reload():
        open_hub(owner, "tax")
        set_input(owner, "#tax-mode", "before_discount")
        set_input(owner, "#tax-default-rate", "16")
        real_click(owner, '#sh-root .sh-nav-item[data-sh="credit"]', "nav credit")
        owner.wait_for_timeout(300)
        set_input(owner, "#credit-mode", "limited")
        set_input(owner, "#credit-limit", "500")
        set_input(owner, "#credit-enforce", "block")
        set_input(owner, "#credit-loyalty", "0.05")
        assert savebar_up(owner)
        real_click(owner, "#sh-save", "Save")
        owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
        # server truth, not the screen
        _, tax = api(owner, "GET", "/api/sub/retail/settings/tax")
        _, cred = api(owner, "GET", "/api/sub/retail/settings/credit")
        d = tax["data"]
        assert d["tax_calculation_mode"] == "before_discount", d
        assert float(d["default_tax_rate"]) == 16.0, d
        c = cred["data"]
        assert c["default_credit_mode"] == "limited" and float(c["default_credit_limit"]) == 500.0, c
        assert c["enforce_credit_limit"] == "block" and float(c["loyalty_point_value"]) == 0.05, c
        # and after a full reload of the page the hub shows the same
        owner.reload()
        dismiss_intro_overlay(owner)
        owner.wait_for_selector("#sub-header-section", timeout=20000)
        open_hub(owner, "tax")
        assert owner.input_value("#tax-mode") == "before_discount"
        assert owner.input_value("#tax-default-rate") == "16"
        real_click(owner, '#sh-root .sh-nav-item[data-sh="credit"]', "nav credit")
        owner.wait_for_timeout(500)
        assert owner.input_value("#credit-mode") == "limited"
        assert owner.input_value("#credit-limit") == "500"
        assert owner.input_value("#credit-enforce") == "block"
        assert owner.input_value("#credit-loyalty") == "0.05"
        assert not savebar_up(owner), "bar is up on a freshly opened hub"

    check("hub: Save persists tax mode, default GST, credit mode/limit/enforce and loyalty value across a reload",
          save_persists_across_reload)

    def refused_save_keeps_bar_and_changes_nothing():
        open_hub(owner, "credit")
        _, before = api(owner, "GET", "/api/sub/retail/settings/credit")
        set_input(owner, "#credit-loyalty", "-3")
        real_click(owner, "#sh-save", "Save")
        owner.wait_for_timeout(700)
        assert savebar_up(owner), "a refused Save dropped the bar: the person would think it was saved"
        assert owner.input_value("#credit-loyalty") == "-3", "the typed value was thrown away"
        _, after = api(owner, "GET", "/api/sub/retail/settings/credit")
        assert before["data"] == after["data"], f"server changed on a refused save: {before['data']} -> {after['data']}"
        real_click(owner, "#sh-discard", "Discard")
        # credit limit mode 'limited' with a 0 limit is also refused. Put the mode there
        # ourselves: the limit box is hidden in the other modes, and this check used to lean
        # on an earlier check having left the shop in 'limited' (a filtered run showed it).
        set_input(owner, "#credit-mode", "limited")
        set_input(owner, "#credit-limit", "0")
        real_click(owner, "#sh-save", "Save")
        owner.wait_for_timeout(500)
        assert savebar_up(owner)
        # P1c: the refusal's toast is a fixed overlay near the bottom edge, and with the
        # taller Large controls the save bar can sit right under it for the few seconds
        # it stays up. Wait until Discard is actually the topmost thing at its centre
        # (the toast expires) instead of clicking through a transient overlay -- the
        # reachability rule real_click enforces afterwards is unchanged. WHAT THIS CAN NO
        # LONGER CATCH: a toast that never expires and permanently covers Discard (that
        # fails here by timing out after 15s, so it is still caught).
        owner.wait_for_function(
            "() => { const b = document.getElementById('sh-discard'); if (!b) return false; const r = b.getBoundingClientRect();"
            " const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!h && b.contains(h); }",
            timeout=15000)
        real_click(owner, "#sh-discard", "Discard")

    check("hub: a refused Save (negative loyalty, zero limit) keeps the bar up and the server unchanged",
          refused_save_keeps_bar_and_changes_nothing)

    def branding_and_business_day_and_device_branch_ride_the_same_bar():
        open_hub(owner, "shop-receipt")
        set_input(owner, "#brand-name", "Hub Test Shop")
        assert savebar_up(owner)
        real_click(owner, "#sh-save", "Save")
        owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
        _, br = api(owner, "GET", "/api/sub/retail/settings/branding")
        assert br["data"].get("branding_business_name") == "Hub Test Shop", br["data"]
        open_hub(owner, "tills")
        set_input(owner, "#business-day-hour", "4")
        assert savebar_up(owner)
        real_click(owner, "#sh-save", "Save")
        owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
        _, bd = api(owner, "GET", "/api/sub/retail/settings/business-day")
        assert int(bd["data"]["business_day_start_hour"]) == 4, bd["data"]
        # put it back so later checks see the default
        set_input(owner, "#business-day-hour", "0")
        real_click(owner, "#sh-save", "Save")
        owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
        # no per-card Save button is left on a moved card
        for sel in ("#branding-card", "#default-tax-card", "#device-branch-card", "#business-day-card"):
            n = owner.locator(f'{sel} button.ret-btn-primary').count()
            assert n == 0, f"{sel} still has {n} primary button(s) -- the per-card Save should be gone"

    check("hub: branding and business day save through the one bar; the per-card Save buttons are gone",
          branding_and_business_day_and_device_branch_ride_the_same_bar)

    # =========================================================================
    # 3. The receipt preview IS the printed receipt
    # =========================================================================
    def frame_text(page, frame_id):
        return page.evaluate("(id) => { const f = document.getElementById(id); "
                             "return f && f.contentDocument && f.contentDocument.body ? f.contentDocument.body.innerText : ''; }",
                             frame_id)

    def frame_html(page, frame_id):
        return page.evaluate("(id) => { const f = document.getElementById(id); "
                             "return f && f.contentDocument ? f.contentDocument.documentElement.outerHTML : ''; }",
                             frame_id)

    def real_print(page, lang, width_mm):
        page.evaluate("(w) => localStorage.setItem('aura_printer_cfg', JSON.stringify({paperWidth: w === 58 ? '58mm' : '80mm'}))",
                      width_mm)
        # The sample sale's tax follows the shop's tax mode (F14), so the print is
        # fed the mode the shop has saved -- the preview reads the same mode off
        # the Tax section's select, which holds the saved value in this check.
        _, tax = api(page, "GET", "/api/sub/retail/settings/tax")
        mode = tax["data"]["tax_calculation_mode"]
        page.evaluate("async ([l, m]) => { await RetailSystem._printReceipt(SettingsHub.sampleSale(l, m)); }", [lang, mode])
        page.wait_for_timeout(500)
        return frame_text(page, "ret-print-frame"), frame_html(page, "ret-print-frame")

    def preview_equals_print_en_ar_80_58():
        report = []
        for lang in ("en", "ar"):
            open_hub(owner, "shop-receipt")
            # make the SHOP's saved receipt language the one under test, saved via the bar
            set_input(owner, "#brand-receipt-lang", lang)
            if savebar_up(owner):
                real_click(owner, "#sh-save", "Save")
                owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
            for width in (80, 58):
                real_click(owner, f'.sh-seg button[data-sh-w="{width}"]', f"{width} mm toggle")
                real_click(owner, f'.sh-seg button[data-sh-l="{lang}"]', f"{lang} toggle")
                owner.wait_for_timeout(700)
                prev_txt = frame_text(owner, "sh-preview-frame")
                prev_html = frame_html(owner, "sh-preview-frame")
                assert "A-000124" in prev_txt, f"preview does not show the sample sale: {prev_txt[:120]!r}"
                pr_txt, pr_html = real_print(owner, lang, width)
                assert pr_txt.strip(), "the real print frame is empty -- the print path did not run"
                assert prev_txt.strip() == pr_txt.strip(), (
                    f"[{lang} {width}mm] preview text != printed text\n--- preview ---\n{prev_txt}\n--- print ---\n{pr_txt}")
                # The paper width is visible in the receipt document itself.
                assert f"{width}mm" in prev_html and f"{width}mm" in pr_html, f"[{lang} {width}mm] width not in receipt html"
                if lang == "ar":
                    # the receipt does not force dir=rtl; what makes it Arabic is its words
                    assert any("؀" <= ch <= "ۿ" for ch in prev_txt), "Arabic preview has no Arabic text"
                else:
                    assert not any("؀" <= ch <= "ۿ" for ch in prev_txt), "English preview contains Arabic text"
                report.append((lang, width, len(pr_txt)))
        assert len(report) == 4, report
        # different languages really differ (a preview that ignores the toggle would pass equality vacuously)
        open_hub(owner, "shop-receipt")
        real_click(owner, '.sh-seg button[data-sh-l="en"]', "en")
        owner.wait_for_timeout(600)
        en_t = frame_text(owner, "sh-preview-frame")
        real_click(owner, '.sh-seg button[data-sh-l="ar"]', "ar")
        owner.wait_for_timeout(600)
        ar_t = frame_text(owner, "sh-preview-frame")
        assert en_t != ar_t, "EN and AR previews are identical -- the language toggle does nothing"
        # The toggle must win over the form's own language field. In the loop above
        # the toggle always matched the shop's language, so a preview that only read
        # the FIELD would pass; here they DISAGREE on purpose (found by mutation:
        # dropping `branding.branding_receipt_language = S.pvLang` stayed green).
        pv = {}
        for field, toggle in (("en", "ar"), ("ar", "ar"), ("ar", "en"), ("en", "en")):
            set_input(owner, "#brand-receipt-lang", field)
            real_click(owner, f'.sh-seg button[data-sh-l="{toggle}"]', f"{toggle} toggle")
            owner.wait_for_timeout(600)
            pv[(field, toggle)] = frame_text(owner, "sh-preview-frame")
        assert pv[("en", "ar")] == pv[("ar", "ar")], (
            "toggle AR with the form on EN does not read like toggle AR with the form on AR: "
            "the preview follows the form field, not the toggle")
        assert pv[("ar", "en")] == pv[("en", "en")], (
            "toggle EN with the form on AR does not read like toggle EN with the form on EN")
        assert pv[("en", "ar")] != pv[("en", "en")], "toggle changed nothing while the form stayed on EN"
        real_click(owner, '.sh-seg button[data-sh-w="80"]', "80")
        owner.wait_for_timeout(500)
        t80 = frame_html(owner, "sh-preview-frame")
        real_click(owner, '.sh-seg button[data-sh-w="58"]', "58")
        owner.wait_for_timeout(500)
        t58 = frame_html(owner, "sh-preview-frame")
        assert t80 != t58, "80 mm and 58 mm previews are identical -- the width toggle does nothing"
        # leave the shop on 'auto'
        set_input(owner, "#brand-receipt-lang", "auto")
        real_click(owner, "#sh-save", "Save")
        owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)

    check("preview: text of the live preview == text the REAL print path renders (EN/AR x 80/58 mm)",
          preview_equals_print_en_ar_80_58)

    def preview_follows_typing_print_does_not():
        open_hub(owner, "shop-receipt")
        real_click(owner, '.sh-seg button[data-sh-l="en"]', "en")
        set_input(owner, "#brand-name", "Typed But Unsaved Cafe")
        owner.wait_for_timeout(700)
        prev = frame_text(owner, "sh-preview-frame")
        assert "Typed But Unsaved Cafe" in prev, f"preview ignored the typed name: {prev[:150]!r}"
        pr_txt, _ = real_print(owner, "en", 80)
        assert "Typed But Unsaved Cafe" not in pr_txt, "the real print already shows an UNSAVED name"
        real_click(owner, "#sh-discard", "Discard")
        owner.wait_for_timeout(700)
        prev2 = frame_text(owner, "sh-preview-frame")
        assert "Typed But Unsaved Cafe" not in prev2, "Discard did not take the typed name out of the preview"

    check("preview: follows an unsaved typed name (the print does not); Discard takes it back out",
          preview_follows_typing_print_does_not)

    # =========================================================================
    # 3b. Unsaved values survive a theme / language switch (NOT a leave guard)
    # =========================================================================
    def wait_new_hub(page, old_marker, preview=True):
        # the paper is drawn only while Shop & receipt is on screen
        page.wait_for_function(
            "(m) => { const r = document.getElementById('sh-root'); return !!r && !r[m]; }", arg=old_marker, timeout=15000)
        hub_ready(page, True, preview=preview)
        page.wait_for_timeout(600)

    def mark_root(page, marker):
        page.evaluate("(m) => { document.getElementById('sh-root')[m] = true; }", marker)

    def switch_theme(page):
        # always a REAL change: applying the theme already on screen re-renders nothing
        page.evaluate("() => ThemeEngine.apply(document.documentElement.getAttribute('data-theme') === 'night' ? 'sand' : 'night')")

    def unsaved_survive_theme_and_language_switch():
        prior_theme = owner.evaluate("() => document.documentElement.getAttribute('data-theme')")
        try:
            # --- theme switch, dirty in TWO different sections' worth of fields
            open_hub(owner, "shop-receipt")
            set_input(owner, "#brand-name", "Carry Me Cafe")
            owner.locator("#brand-show-cashier").set_checked(not owner.is_checked("#brand-show-cashier"))
            owner.dispatch_event("#brand-show-cashier", "change")
            cashier_before = owner.is_checked("#brand-show-cashier")
            assert savebar_up(owner)
            mark_root(owner, "__old1")
            switch_theme(owner)
            wait_new_hub(owner, "__old1")
            assert owner.input_value("#brand-name") == "Carry Me Cafe", "typed name lost across a theme switch"
            assert owner.is_checked("#brand-show-cashier") == cashier_before, "ticked box lost across a theme switch"
            assert savebar_up(owner), "the values came back but the bar did not: they would look saved"
            assert "shop-receipt" in visible_panes(owner), visible_panes(owner)
            # the preview follows the restored name too (it was rebuilt, not stale)
            assert "Carry Me Cafe" in frame_text(owner, "sh-preview-frame"), "preview did not pick up the restored name"
            # and the SERVER still holds the old name: nothing was saved by the switch
            _, cur = api(owner, "GET", "/api/sub/retail/settings/branding")
            assert "Carry Me Cafe" not in json.dumps(cur), "a theme switch SAVED the unsaved name"

            # --- Discard after the restore really discards (baseline is the server's)
            real_click(owner, "#sh-discard", "Discard")
            owner.wait_for_timeout(500)
            assert owner.input_value("#brand-name") != "Carry Me Cafe" and not savebar_up(owner)

            # --- the person comes back to the SAME section, not the first one
            open_hub(owner, "tax")
            set_input(owner, "#tax-default-rate", "7")
            assert savebar_up(owner)
            mark_root(owner, "__old1b")
            switch_theme(owner)
            wait_new_hub(owner, "__old1b", preview=False)
            assert owner.input_value("#tax-default-rate") == "7", "typed tax rate lost across a theme switch"
            assert visible_panes(owner) == ["tax"], f"came back to {visible_panes(owner)}, not the tax section"
            real_click(owner, "#sh-discard", "Discard")
            owner.wait_for_timeout(400)
            assert not savebar_up(owner)

            # --- a switch with NOTHING dirty brings nothing back
            mark_root(owner, "__old2")
            switch_theme(owner)
            wait_new_hub(owner, "__old2", preview=False)  # a stale carry would land on Tax, where no paper is drawn: fail on the assertion below, not on a wait
            assert owner.input_value("#brand-name") != "Carry Me Cafe" and not savebar_up(owner), "a clean hub came back dirty"

            # --- a discarded edit is not resurrected by a LATER switch
            set_input(owner, "#brand-name", "Second Edit")
            real_click(owner, "#sh-discard", "Discard")
            owner.wait_for_timeout(400)
            mark_root(owner, "__old3")
            switch_theme(owner)
            wait_new_hub(owner, "__old3", preview=False)  # a stale carry would land on Tax, where no paper is drawn: fail on the assertion below, not on a wait
            assert owner.input_value("#brand-name") != "Second Edit" and not savebar_up(owner), "a discarded edit came back"
            owner.evaluate("(n) => ThemeEngine.apply(n)", prior_theme or "light")
            owner.wait_for_timeout(800)

            # --- plain navigation away and back is NOT a switch: starts clean (honest limit)
            open_hub(owner, "shop-receipt")
            set_input(owner, "#brand-name", "Left Behind")
            assert savebar_up(owner)
            # go() answers the leave guard's question with 'Discard', which wipes the carry
            # itself; drop the guards (test seam) so it is the carry's OWN rule (env stamp,
            # observer) that is under test. The guarded leave is owned by the leave-guard checks.
            owner.evaluate("() => SubsystemApp._leaveGuards.clear()")
            go(owner, "dashboard", 600)
            open_hub(owner, "shop-receipt")
            assert owner.input_value("#brand-name") != "Left Behind" and not savebar_up(owner), (
                "navigating away and back kept the value: the carry must only answer a theme/language switch")

            # --- language switch through the real header button
            open_hub(owner, "shop-receipt")
            set_input(owner, "#brand-name", "Carry Across Language")
            mark_root(owner, "__old4")
            real_click(owner, "#aura-lang-toggle", "language toggle")
            wait_new_hub(owner, "__old4")
            assert owner.input_value("#brand-name") == "Carry Across Language", "typed name lost across a language switch"
            assert savebar_up(owner), "restored after a language switch but the bar is down"
            real_click(owner, "#sh-discard", "Discard")
            mark_root(owner, "__old5")
            real_click(owner, "#aura-lang-toggle", "language toggle back")
            wait_new_hub(owner, "__old5")
            assert owner.input_value("#brand-name") != "Carry Across Language" and not savebar_up(owner)
        finally:
            try:
                if owner.evaluate("() => AuraI18n.current") != "en":
                    owner.evaluate("() => AuraI18n.setLang('en')")
                    owner.wait_for_timeout(1200)
                owner.evaluate("(n) => ThemeEngine.apply(n)", prior_theme or "light")
                owner.wait_for_timeout(600)
            except Exception:  # noqa: BLE001
                pass

    check("hub: unsaved values survive a theme and a language switch; clean, discarded and left-behind ones do not come back",
          unsaved_survive_theme_and_language_switch)

    # =========================================================================
    # 4. Who sees what -- both directions
    # =========================================================================
    def manager_sees_only_tills_and_team():
        go(manager, "admin-center", 400)
        manager.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
        manager.wait_for_function("() => !!document.getElementById('device-branch-select') && "
                                  "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
        manager.wait_for_timeout(500)
        ids = manager.eval_on_selector_all("#sh-root .sh-nav-item", "els => els.map(e => e.dataset.sh)")
        # P1c: Display (Screen size) is per-device and needs no authority, so a delegated
        # manager sees it too. WHAT THIS CAN NO LONGER CATCH: "Display" being hidden from a
        # manager -- it is not owner-data. Every owner-only section is still refused below.
        # EXTRAS: a manager holds retail.stock.adjust, so the Extras set-up section shows too.
        assert ids == ["tills", "extras", "display", "team"], f"a delegated manager sees sections {ids}"
        secs = manager.evaluate("() => SettingsHub.sections().filter(s => s.visible).map(s => s.id)")
        assert secs == ["tills", "extras", "display", "team"], secs
        # owner-only sections refuse to open
        assert manager.evaluate("() => SettingsHub.open('credit')") is False
        for sel in ("#branding-card", "#default-tax-card", "#business-day-card", "#einvoicing-card",
                    "#wa-reports-card", "#shop-network-card", "#numbering-codes-card", "#credit-card",
                    "#tax-mode", "#printer-card", "#reorder-req-card"):
            assert manager.query_selector(sel) is None, f"a delegated manager must not have {sel}"
        assert manager.query_selector("#device-branch-card") is not None
        assert manager.query_selector("#doc-series-card") is not None
        assert manager.query_selector('button:has-text("Create Book")') is None
        reachable(manager, manager.locator("#device-branch-card"), "manager device-branch card")
        real_click(manager, '#sh-root .sh-nav-item[data-sh="team"]', "manager team nav")
        manager.wait_for_timeout(900)
        assert visible_panes(manager) == ["team"], visible_panes(manager)
        assert manager.locator('[data-sh-pane="team"]').inner_text().strip(), "the Team pane is empty for the manager"
        # the server agrees: the owner-only write the hub does not offer is 403 for this person
        st, body = api(manager, "POST", "/api/sub/retail/settings/credit", {"loyalty_point_value": "9"})
        assert st == 403, f"expected 403 for a manager posting credit settings, got {st}: {body}"

    check("gating: a delegated manager sees ONLY Tills, Display and Team (and the server still refuses the rest)",
          manager_sees_only_tills_and_team)

    def cashier_gets_lock_panel():
        go(cashier, "admin-center", 600)
        assert cashier.query_selector("#sh-root") is None, "a cashier got the hub"
        assert cashier.query_selector("#device-branch-card") is None
        heading = cashier.locator(".ret-title", has_text="Settings")
        assert heading.count() > 0, "the lock panel's own Settings heading did not render"
        assert cashier.evaluate("() => SettingsHub.sections().filter(s => s.visible).length") == 0
        assert cashier.evaluate("() => SettingsHub.open('tills')") is False

    check("gating: a cashier gets the lock panel and no section is open()-able", cashier_gets_lock_panel)

    # =========================================================================
    # 5. Hardware moved; reorder card in Purchasing
    # =========================================================================
    def printer_test_lives_in_hardware_not_scanner():
        open_hub(owner, "hardware")
        for sel in ("#printer-card", "#pr-hw-test-btn", "#pr-hw-kick-btn", "#pr-width"):
            reachable(owner, owner.locator(sel), sel)
        go(owner, "scanner", 700)
        assert owner.query_selector("#pr-hw-test-btn") is None, "the printer test is still on the Barcode Scanner screen"
        assert owner.query_selector("#printer-card") is None
        reachable(owner, owner.locator("#printer-moved-btn"), "pointer to Hardware")
        real_click(owner, "#printer-moved-btn", "Open printer settings")
        owner.wait_for_selector("#sh-root", timeout=10000)
        owner.wait_for_timeout(700)
        assert visible_panes(owner) == ["hardware"], visible_panes(owner)

    check("hardware: the printer/drawer test is in Hardware, gone from the scanner screen; the pointer opens it",
          printer_test_lives_in_hardware_not_scanner)

    def manager_keeps_printer_card_on_scanner():
        go(manager, "scanner", 700)
        assert manager.query_selector("#pr-hw-test-btn") is not None, \
            "a manager who cannot open Hardware lost the printer test altogether"
        assert manager.query_selector("#printer-moved-btn") is None, "manager was pointed at a section they cannot open"

    check("hardware: a manager who cannot open Hardware keeps the printer card on the scanner screen",
          manager_keeps_printer_card_on_scanner)

    def reorder_card_in_purchasing_not_settings():
        go(owner, "purchases", 900)
        assert owner.query_selector("#reorder-req-card") is not None, "reorder-requests card is not in Purchasing"
        reachable(owner, owner.locator("#reorder-req-card"), "reorder card in Purchasing")
        open_hub(owner, "tills")
        assert owner.query_selector("#reorder-req-card") is None, "reorder-requests card is still in Settings"

    check("purchasing: the reorder-requests card is in Purchasing and not in Settings", reorder_card_in_purchasing_not_settings)

    # =========================================================================
    # 6. One tax rate for a whole category
    # =========================================================================
    def category_tax_changes_exactly_that_category():
        def rates():
            _, body = api(owner, "GET", "/api/sub/retail/products")
            return {r["id"]: r.get("tax_rate") for r in body["data"]}
        before = rates()
        open_hub(owner, "tax")
        owner.select_option("#tax-cat-select", str(CAT_DRINKS))
        set_input(owner, "#tax-cat-rate", "12")
        # cancel first: nothing may change
        real_click(owner, "#tax-cat-apply", "Change tax rate")
        owner.wait_for_selector("#ret-confirm-dialog", timeout=5000)
        real_click(owner, "#ret-confirm-cancel", "confirm Cancel")
        owner.wait_for_timeout(500)
        assert rates() == before, "cancelling the confirmation still changed tax rates"
        # apply
        real_click(owner, "#tax-cat-apply", "Change tax rate")
        owner.wait_for_selector("#ret-confirm-dialog", timeout=5000)
        real_click(owner, "#ret-confirm-ok", "confirm OK")
        owner.wait_for_function("() => /Changed the tax rate|Changed \\d+ of/.test(document.getElementById('tax-cat-status').textContent)", timeout=10000)
        after = rates()
        assert float(after[PID_D1]) == 12.0 and float(after[PID_D2]) == 12.0, after
        assert after[PID_S1] == before[PID_S1], "a product in ANOTHER category changed"
        status = owner.inner_text("#tax-cat-status")
        assert "2" in status, status
        # out of range is refused before anything is sent
        set_input(owner, "#tax-cat-rate", "150")
        real_click(owner, "#tax-cat-apply", "Change tax rate")
        owner.wait_for_timeout(400)
        assert owner.query_selector("#ret-confirm-dialog") is None, "an out-of-range rate reached the confirmation"
        assert rates() == after

    check("tax: one rate for a whole category changes exactly that category; cancel and out-of-range change nothing",
          category_tax_changes_exactly_that_category)

    # =========================================================================
    # 7. Receipt numbers (ONE card): the per-computer switch + till number, the
    #    branch code, the example beside them, the books under Advanced.
    #    Everything here runs against the REAL /numbering route (crew B's), with
    #    this process pinned to ONE fixed till identity so the books the server
    #    makes at shift-open are the ones a person would get.
    # =========================================================================
    import contextlib  # noqa: E402
    import re  # noqa: E402
    import uuid  # noqa: E402
    from api import retail_api as _retail_api  # noqa: E402
    from database.schema import get_retail_conn as _retail_conn  # noqa: E402

    NUM = "/api/sub/retail/numbering"
    # This computer's REAL terminal id (its registry device id, set by the announce
    # above). It used to be a made-up constant patched over local_terminal_id(); v42
    # refuses a made-up id (DEVICE_IDENTITY_CLASH), and rightly.
    NUM_TERMINAL = _retail_api.local_terminal_id()
    assert NUM_TERMINAL, "the boot announce did not give this computer a device id"
    RIVAL_CO = "e2e-rival-shop"
    RIVAL_TERMINAL = "5e5e5e5e-2222-4222-8222-000000000002"
    LEGACY_SALE = re.compile(r"^SALE-\d+-[0-9a-f]{8}-[0-9a-f]{8}$")
    NEXT_SHIFT_NOTE = "The books are created when this computer opens its next shift."
    NEW_CODE_NOTE = ("A new code applies only to tills switched on afterwards. "
                     "Books that already exist keep their code.")
    NUM_STATE = {}

    @contextlib.contextmanager
    def pinned(available=None):
        """Sets ONE thing for the length of the block: the server's
        READABLE_NUMBERS_AVAILABLE kill switch (None leaves it alone, so the
        num_get/num_post helpers nested inside a check never undo what the check
        chose).

        v42 change: this used to ALSO patch local_terminal_id() to a made-up id.
        That fake is gone -- this computer is now a real registered main computer
        (announce + claim-admin at seed time) and its terminal id is real, so the
        server's own DEVICE_IDENTITY_CLASH guard is exercised instead of dodged.
        The shipped default of the switch is now True (readable numbers ship ON);
        the ON-path checks run with it True and the unavailable-state checks force
        it False. What that seam cannot catch: the value of the constant as shipped
        -- a check that forces it either way proves the code path behind it, not
        the default itself."""
        previous_avail = _retail_api.READABLE_NUMBERS_AVAILABLE
        if available is not None:
            _retail_api.READABLE_NUMBERS_AVAILABLE = bool(available)
        try:
            yield
        finally:
            _retail_api.READABLE_NUMBERS_AVAILABLE = previous_avail

    def num_get(**qs):
        path = NUM + ("?" + "&".join(f"{k}={v}" for k, v in qs.items()) if qs else "")
        with pinned():
            st, b = api(owner, "GET", path)
        assert st == 200 and (b or {}).get("status") == "success", (st, b)
        return b["data"]

    def num_post(body):
        with pinned():
            return api(owner, "POST", NUM, body)

    def num_branch():
        """(id, uid, name) of the branch the numbering card is about."""
        if not NUM_STATE:
            d = num_get()
            assert d.get("branch_uid"), f"the test shop's branch has no uid: {d}"
            conn = _retail_conn()
            try:
                row = conn.execute("SELECT id, name FROM branches WHERE uid=?", (d["branch_uid"],)).fetchone()
            finally:
                conn.close()
            NUM_STATE.update(id=row["id"], uid=d["branch_uid"], name=row["name"])
        return NUM_STATE

    def num_reset():
        """Back to a plain shop: this till off, no saved code, no rival books, the
        branch's real name."""
        br = num_branch()
        num_post({"readable_enabled": False})
        conn = _retail_conn()
        try:
            conn.execute("DELETE FROM doc_series WHERE company_id=?", (RIVAL_CO,))
            conn.execute("DELETE FROM retail_settings WHERE skey LIKE 'branch_code:%'")
            conn.execute("UPDATE branches SET name=? WHERE id=?", (br["name"], br["id"]))
            conn.commit()
        finally:
            conn.close()

    def rival_book(doc_type, code):
        """Another computer's book for this code. Idempotent: (doc_type, code) is
        UNIQUE, and the EN and AR halves of one check both plant it."""
        conn = _retail_conn()
        try:
            conn.execute("DELETE FROM doc_series WHERE company_id=? AND doc_type=? AND code=?",
                         (RIVAL_CO, doc_type, code))
            conn.execute(
                "INSERT INTO doc_series (id,company_id,doc_type,code,label,allocator_terminal_uid,status,row_version) "
                "VALUES (?,?,?,?,?,?,'active',1)",
                (str(uuid.uuid4()), RIVAL_CO, doc_type, code, code, RIVAL_TERMINAL))
            conn.commit()
        finally:
            conn.close()

    def reopen_shift():
        br = num_branch()
        with pinned():
            _, cur = api(owner, "GET", f"/api/sub/retail/cash-sessions/current?branch_id={br['id']}")
            row = (cur or {}).get("data")
            if row:
                exp = row.get("expected_cash") if isinstance(row, dict) else None
                st, b = api(owner, "POST", f"/api/sub/retail/cash-sessions/{row['id']}/close",
                            {"closing_float_counted": exp if exp is not None else 0})
                assert st == 200, f"could not close the open shift: {b}"
            st, b = api(owner, "POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 0, "branch_id": br["id"]})
            assert st == 200, f"could not open a shift: {b}"

    def num_sale_number():
        _, b = api(owner, "POST", "/api/sub/retail/products", {
            "name": "Num Widget " + uuid.uuid4().hex[:6], "sku": "NUM-" + uuid.uuid4().hex[:8],
            "sell_price": 2, "cost_price": 1, "category_id": CAT_SNACKS, "unit": "pc", "initial_stock": 50})
        pid = ((b or {}).get("data") or {}).get("id")
        assert pid, b
        with pinned():
            st, sale = api(owner, "POST", "/api/sub/retail/sales", {
                "items": [{"product_id": pid, "quantity": 1}], "amount_paid": 100, "payment_method": "cash",
                "idempotency_key": str(uuid.uuid4())})
        assert st == 200, (st, sale)
        return sale["data"]["sale_number"]

    def pinned_check(name, fn, available=True):
        """A check that runs with this process pinned to ONE till identity for its
        whole length: the browser's own requests reach the server on other threads,
        so pinning only around the test's api() calls would leave the screen talking
        to a different till than the test reads back (found the hard way).
        `available=True` runs it with readable numbers switched ON-able on the
        server; pass available=False for the real shipped state."""
        def run():
            with pinned(available=available):
                fn()
        check(name, run)

    def spy_numbering_posts(page):
        posts = []

        def handler(route, request):
            if request.method == "POST":
                posts.append(json.loads(request.post_data or "{}"))
            route.continue_()
        page.route("**/api/sub/retail/numbering*", handler)
        return posts

    def numbering_hub(page=None):
        pg = page or owner
        open_hub(pg, "tills")
        pg.wait_for_selector("#num-branch-code", timeout=8000)
        return pg

    def bar_gone(page):
        page.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)

    def num_error_text(page):
        return page.evaluate("() => { const e = document.getElementById('num-error'); return e && !e.hidden ? e.textContent.trim() : ''; }")

    def rects_overlap(page, sel_a, sel_b):
        return page.evaluate("""([a, b]) => {
          const ra = document.querySelector(a).getBoundingClientRect(), rb = document.querySelector(b).getBoundingClientRect();
          return !(ra.bottom <= rb.top || ra.top >= rb.bottom || ra.right <= rb.left || ra.left >= rb.right);
        }""", [sel_a, sel_b])

    def save_click(page):
        """A real click on Save, once the previous action's toast is gone: the toast
        is the app shell's own bare div (about 3 s) and sits over the Save bar while
        it lives, so clicking at once would test the toast, not the hub."""
        page.wait_for_function(
            "() => ![...document.body.children].some(e => e.tagName === 'DIV' && e.style.position === 'fixed' && /bottom/.test(e.getAttribute('style') || ''))",
            timeout=8000)
        real_click(page, "#sh-save", "Save")

    def numbering_one_card_with_books_under_advanced():
        num_reset()
        numbering_hub()
        # ONE card: the old separate "Numbering" card is not a top-level card any more.
        cards = owner.evaluate("() => [...document.querySelectorAll('[data-sh-pane=\"tills\"] > .sub-chart-card')].map(c => c.id)")
        assert "numbering-codes-card" in cards, cards
        assert "doc-series-card" not in cards, f"the numbering books are still a card of their own: {cards}"
        title = owner.inner_text("#numbering-codes-card .sh-card-title")
        assert title.strip() == "Receipt numbers", title
        det = owner.locator("#numbering-advanced")
        assert det.evaluate("(d) => d.open") is False, "Advanced is open by default"
        assert not owner.locator("#doc-series-card").is_visible(), "the books list is visible before Advanced is opened"
        real_click(owner, "#numbering-advanced > summary", "Advanced")
        owner.wait_for_function("() => document.getElementById('numbering-advanced').open", timeout=3000)
        assert owner.evaluate("() => !!document.querySelector('#numbering-advanced #doc-series-card')"), \
            "the numbering books list is not inside Advanced"
        # The card is taller than a 768px window by design (title, list or teaching
        # empty state, and the New Book form), so "the whole box fits" can never hold;
        # what a person needs is to see where it starts. It can no longer catch the
        # card's LOWER half falling off-screen -- the form-focus check in the empty
        # states block covers reaching the form.
        reachable(owner, owner.locator("#doc-series-card > h3"), "numbering books list under Advanced (its title)")
        reachable(owner, owner.locator("#doc-series-table thead th").first, "numbering books list under Advanced (its header row)")

    pinned_check("numbering: ONE 'Receipt numbers' card; the books list sits closed under Advanced and opens by a real click",
          numbering_one_card_with_books_under_advanced)

    def numbering_on_with_till_number_books_made_at_next_shift_open():
        num_reset()
        br = num_branch()
        posts = spy_numbering_posts(owner)
        try:
            numbering_hub()
            assert not owner.is_checked("#num-readable"), "the switch starts on for a shop that has not chosen it"
            assert not owner.locator("#num-till").is_visible(), "the till number shows before the switch is on"
            set_input(owner, "#num-branch-code", "abd")
            real_click(owner, "#num-readable", "Readable receipt numbers switch")
            owner.wait_for_selector("#num-till", state="visible", timeout=3000)
            set_input(owner, "#num-till", "7")
            ex = owner.inner_text("#num-preview")
            assert "ABD7-000001" in ex and "ABD7-R-000001" in ex, f"the example did not follow the code + till: {ex!r}"
            save_click(owner)
            bar_gone(owner)
            assert posts == [{"branch_uid": br["uid"], "branch_code": "ABD", "readable_enabled": True, "till_number": 7}], posts
            d = num_get()
            assert d["readable_enabled"] is True and d["till_number"] == 7 and d["branch_code"] == "ABD", d
            assert d["books_ready"] is False, "books exist before a shift was opened"
            note = owner.inner_text("#num-preview-note")
            assert NEXT_SHIFT_NOTE in note, f"the 'created at next shift' note is not on screen: {note!r}"
            # the books really are made at the NEXT shift open, and the sale carries the number
            reopen_shift()
            d = num_get()
            assert d["books_ready"] is True and d["preview_is_example"] is False, d
            assert d["preview"] == {"sale": "ABD7-000001", "return": "ABD7-R-000001"}, d["preview"]
            assert num_sale_number() == "ABD7-000001", "the first sale did not take the readable number"
            numbering_hub()
            live = owner.inner_text("#num-preview")
            assert "ABD7-000002" in live, f"the card does not show the server's real next number: {live!r}"
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            num_reset()

    pinned_check("numbering: off to on with a till number sends exactly that, and the books exist only after the next shift opens",
          numbering_on_with_till_number_books_made_at_next_shift_open)

    def numbering_taken_till_number_is_said_plainly():
        num_reset()
        posts = spy_numbering_posts(owner)
        try:
            numbering_hub()
            # Somebody else takes till 5 AFTER the page loaded, so the card cannot have
            # known: the refusal must come from the server (409) and be said plainly.
            rival_book("sale", "ABD5")
            set_input(owner, "#num-branch-code", "ABD")
            real_click(owner, "#num-readable", "Readable receipt numbers switch")
            owner.wait_for_selector("#num-till", state="visible", timeout=3000)
            set_input(owner, "#num-till", "5")
            save_click(owner)
            owner.wait_for_function("() => { const e = document.getElementById('num-error'); return e && !e.hidden && e.textContent.trim().length > 0; }", timeout=8000)
            assert len(posts) == 1 and posts[0].get("till_number") == 5, posts
            want = "Till number 5 is already used by another computer. Choose a different number."
            assert num_error_text(owner) == want, num_error_text(owner)
            assert savebar_up(owner), "a refused save dropped the bar"
            d = num_get()
            assert d["readable_enabled"] is False, "the refused save switched the till on anyway"
            # ... and in Arabic it is Arabic, never the server's English
            set_lang(owner, "ar")
            try:
                numbering_hub()
                rival_book("sale", "ABD5")
                set_input(owner, "#num-branch-code", "ABD")
                real_click(owner, "#num-readable", "Readable receipt numbers switch")
                owner.wait_for_selector("#num-till", state="visible", timeout=3000)
                set_input(owner, "#num-till", "5")
                save_click(owner)
                owner.wait_for_function("() => { const e = document.getElementById('num-error'); return e && !e.hidden && e.textContent.trim().length > 0; }", timeout=8000)
                ar = num_error_text(owner)
                assert re.search(r"[؀-ۿ]", ar), f"the refusal is not Arabic: {ar!r}"
                assert not re.search(r"[A-Za-z]{3,}", ar), f"English leaked into the Arabic refusal: {ar!r}"
                assert "5" in ar, ar
            finally:
                set_lang(owner, "en")
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            num_reset()

    pinned_check("numbering: a till number another computer holds gives a 409 that is said plainly, EN and AR, and changes nothing",
          numbering_taken_till_number_is_said_plainly)

    def numbering_off_makes_the_next_sale_legacy():
        num_reset()
        br = num_branch()
        st, b = num_post({"branch_code": "ABD", "readable_enabled": True, "till_number": 6})
        assert st == 200, b
        reopen_shift()
        assert num_sale_number() == "ABD6-000001", "setup: the till should be on before the test switches it off"
        posts = spy_numbering_posts(owner)
        try:
            numbering_hub()
            assert owner.is_checked("#num-readable"), "the switch does not show that this computer is on"
            assert owner.input_value("#num-till") == "6", "the till number is not shown for a computer that is on"
            real_click(owner, "#num-readable", "Readable receipt numbers switch")
            save_click(owner)
            bar_gone(owner)
            assert posts == [{"branch_uid": br["uid"], "readable_enabled": False}], posts
            n = num_sale_number()
            assert LEGACY_SALE.match(n), f"after switching off the next sale is not a legacy number: {n!r}"
            assert not owner.is_checked("#num-readable")
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            num_reset()

    pinned_check("numbering: switching off sends only that, and the next sale is a legacy number again",
          numbering_off_makes_the_next_sale_legacy)

    def numbering_code_change_shows_the_next_book_note():
        num_reset()
        br = num_branch()
        st, b = num_post({"branch_code": "ABD", "readable_enabled": True, "till_number": 8})
        assert st == 200, b
        reopen_shift()
        posts = spy_numbering_posts(owner)
        try:
            numbering_hub()
            set_input(owner, "#num-branch-code", "abe")
            save_click(owner)
            bar_gone(owner)
            assert posts == [{"branch_uid": br["uid"], "branch_code": "ABE"}], posts
            server = num_get()
            KEEPS = ("This computer keeps numbering with its current books (ABD8). "
                     "The new branch code applies only to tills switched on afterwards.")
            LIVE = "Each document on this computer prints the next number of these books."
            # The server says the code note (the computer is on, so: 'keeps its books')
            # and the example note (the books are live) -- the screen shows exactly those.
            assert server["branch_code_note"] == KEEPS, server["branch_code_note"]
            assert server["preview_note"] == LIVE, server["preview_note"]
            keeps = owner.inner_text("#num-code-server-note").strip()
            assert keeps == KEEPS, keeps
            live = owner.inner_text("#num-preview-note").strip()
            assert live == LIVE, live
            # the example is the books' real next number, NOT the new code
            assert "ABD8-000001" in owner.inner_text("#num-preview"), owner.inner_text("#num-preview")
            # and in Arabic the same two notes are Arabic
            set_lang(owner, "ar")
            try:
                numbering_hub()
                for sel in ("#num-code-server-note", "#num-preview-note"):
                    txt = owner.inner_text(sel).strip()
                    assert re.search(r"[؀-ۿ]", txt) and not re.search(r"[A-Za-z]{4,}", txt.replace("ABD", "")), \
                        f"{sel} is not Arabic: {txt!r}"
            finally:
                set_lang(owner, "en")
            # A computer that is OFF gets the other sentence: a new code applies to tills
            # switched on afterwards. (Both notes exist; each must show in its own state.)
            st, b = num_post({"readable_enabled": False})
            assert st == 200, b
            numbering_hub()
            off_note = owner.inner_text("#num-code-server-note").strip()
            assert off_note == NEW_CODE_NOTE, off_note
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            num_reset()

    pinned_check("numbering: changing the code of a computer that is on shows the 'next book' notes, EN and AR",
          numbering_code_change_shows_the_next_book_note)

    def numbering_arabic_only_branch_name_asks_for_a_code():
        num_reset()
        br = num_branch()
        posts = spy_numbering_posts(owner)
        try:
            st, b = api(owner, "PUT", f"/api/sub/retail/branches/{br['id']}", {"name": "فرع الشمال"})
            assert st == 200, b
            d = num_get()
            assert d["branch_code"] is None and d["branch_code_is_saved"] is False, d
            numbering_hub()
            assert owner.input_value("#num-branch-code") == "", "a code was guessed for a branch with an Arabic-only name"
            note = owner.inner_text("#num-code-note")
            assert "no code yet" in note, note
            assert "Type a branch code to see an example." in owner.inner_text("#num-preview")
            assert not savebar_up(owner), "the empty field counts as an unsaved change"
            set_input(owner, "#num-branch-code", "nor")
            assert "NOR1-000001" in owner.inner_text("#num-preview"), owner.inner_text("#num-preview")
            save_click(owner)
            bar_gone(owner)
            assert posts == [{"branch_uid": br["uid"], "branch_code": "NOR"}], posts
            assert num_get()["branch_code"] == "NOR"
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            num_reset()

    pinned_check("numbering: an Arabic-only branch name shows an empty code field with a prompt (no guess), then saves what is typed",
          numbering_arabic_only_branch_name_asks_for_a_code)

    def numbering_bad_code_is_refused_before_anything_is_sent():
        num_reset()
        posts = spy_numbering_posts(owner)
        try:
            numbering_hub()
            # a reserved code
            set_input(owner, "#num-branch-code", "sale")
            assert owner.input_value("#num-branch-code") == "SALE"
            live = num_error_text(owner)
            assert '"SALE" is already used at the start of other document numbers. Choose another code.' == live, live
            save_click(owner)
            owner.wait_for_timeout(500)
            assert posts == [], f"a reserved code reached the server: {posts}"
            assert savebar_up(owner)
            # digits and symbols never get typed in; what is left is too short
            set_input(owner, "#num-branch-code", "a1!")
            assert owner.input_value("#num-branch-code") == "A", owner.input_value("#num-branch-code")
            save_click(owner)
            owner.wait_for_timeout(500)
            assert posts == [], f"a non-letter code reached the server: {posts}"
            assert "2 to 4 letters" in num_error_text(owner), num_error_text(owner)
            # Digits that arrive WITHOUT a typing event (autofill, a script) are not
            # cleaned by the field, so Save itself must refuse them: nothing is sent.
            set_input(owner, "#num-branch-code", "abc")
            owner.evaluate("() => { document.getElementById('num-branch-code').value = 'A1B'; }")
            save_click(owner)
            owner.wait_for_timeout(500)
            assert posts == [], f"a code with a digit in it reached the server: {posts}"
            assert "2 to 4 letters" in num_error_text(owner), num_error_text(owner)
            # switching on without a till number
            set_input(owner, "#num-branch-code", "abd")
            real_click(owner, "#num-readable", "Readable receipt numbers switch")
            owner.wait_for_selector("#num-till", state="visible", timeout=3000)
            save_click(owner)
            owner.wait_for_timeout(500)
            assert posts == [], f"a switch-on with no till number reached the server: {posts}"
            assert num_error_text(owner) == "Choose this till's number, from 1 to 99.", num_error_text(owner)
            # the allow half: a good code and a good number DO go
            set_input(owner, "#num-till", "3")
            save_click(owner)
            bar_gone(owner)
            assert len(posts) == 1 and posts[0]["till_number"] == 3 and posts[0]["branch_code"] == "ABD", posts
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            num_reset()

    pinned_check("numbering: a reserved code, a non-letter and a missing till number are refused in plain words with NO request sent; a good one goes",
          numbering_bad_code_is_refused_before_anything_is_sent)

    def numbering_save_bar_never_covers_the_example():
        num_reset()
        st, b = num_post({"branch_code": "ABD", "readable_enabled": True, "till_number": 9})
        assert st == 200, b
        reopen_shift()
        try:
            for w, h in ((1366, 768), (1024, 768)):
                owner.set_viewport_size({"width": w, "height": h})
                owner.wait_for_timeout(300)
                numbering_hub()
                set_input(owner, "#num-branch-code", "abf")
                assert savebar_up(owner), "the bar did not come up"
                owner.wait_for_timeout(300)
                reachable(owner, owner.locator("#num-preview"), f"example at {w}x{h}")
                assert not rects_overlap(owner, "#num-example-card", "#sh-savebar"), \
                    f"the Save bar covers the example at {w}x{h}"
                # the Save button is itself reachable while the example is on screen
                reachable(owner, owner.locator("#sh-save"), f"Save at {w}x{h}")
                real_click(owner, "#sh-discard", "Discard")
                owner.wait_for_timeout(300)
        finally:
            owner.set_viewport_size({"width": 1366, "height": 768})
            num_reset()

    pinned_check("numbering: while the Save bar is up the example is on screen and not under it (1366 and 1024)",
          numbering_save_bar_never_covers_the_example)

    def numbering_save_bar_never_covers_the_advanced_summary():
        # H2 item 5b. The Save bar is sticky at the foot of the pane. The "Advanced" fold
        # sits at the foot of the numbering card, i.e. exactly where a sticky bar lands
        # when the pane is a little shorter than the card. Rule 6b, in EN and AR, at both
        # widths: the summary's box is inside the window, the topmost thing at its centre
        # IS the summary (not the bar), and a real mouse click there opens the fold.
        # The readable numbers cannot be switched on in this build (409 until the device
        # registry lands), so the fold cannot be put lower by state. Its place is moved by
        # window HEIGHT instead: the shorter the window, the nearer the fold is to the bar.
        num_reset()
        try:
            for lang in ("en", "ar"):
                set_lang(owner, lang)
                # 1024 wide is the size where the bar wraps to two rows (126px tall at EN,
                # taller than the pane's 96px scroll-padding). The fold sits ~960-1000px down
                # the pane, so the tall 1024 windows (a tablet upright) are the ones where
                # it is wholly on screen yet the padding still calls it clear of the bar.
                for w, h in ((1366, 768), (1024, 768), (1024, 1100), (1024, 1120), (1024, 1140), (1024, 1366)):
                    owner.set_viewport_size({"width": w, "height": h})
                    owner.wait_for_timeout(300)
                    numbering_hub()
                    set_input(owner, "#num-branch-code", "abf")
                    assert savebar_up(owner), f"the bar did not come up ({lang} {w}x{h})"
                    owner.wait_for_timeout(300)
                    what = f"Advanced summary ({lang} {w}x{h})"
                    to_top = "() => { const s = document.getElementById('sub-content'); if (s) s.scrollTop = 0; window.scrollTo(0, 0); }"
                    # Path 1, a person scrolling a little / a tool bringing it into view: the
                    # pane is at its top, the bar is up, and the page must scroll the summary
                    # CLEAR of the bar. reachable() asks the browser to scroll it into view only
                    # if it is not already "visible" -- and "visible" is judged with the pane's
                    # scroll-padding, so a padding shorter than the bar (126px tall at EN 1024,
                    # two rows) calls a half-hidden summary visible and never moves it.
                    owner.evaluate(to_top)
                    owner.wait_for_timeout(200)
                    reachable(owner, owner.locator("#numbering-advanced > summary"), what)
                    assert not rects_overlap(owner, "#numbering-advanced > summary", "#sh-savebar"), \
                        f"the Save bar covers the Advanced summary ({lang} {w}x{h})"
                    # Path 2, the keyboard: focus() lets the browser do the scrolling itself.
                    owner.evaluate(to_top)
                    owner.evaluate("() => document.querySelector('#numbering-advanced > summary').focus()")
                    owner.wait_for_timeout(400)
                    info = owner.locator("#numbering-advanced > summary").first.evaluate(_CENTRE_JS)
                    assert info["inView"], f"{what}: after focus() box {info['box']} is outside the {info['vw']}x{info['vh']} viewport"
                    assert info["hits"], f"{what}: after focus() the topmost thing at its centre is {info['hitDesc']}, not the summary"
                    if not owner.evaluate("() => document.getElementById('numbering-advanced').open"):
                        real_click(owner, "#numbering-advanced > summary", what)
                        owner.wait_for_function("() => document.getElementById('numbering-advanced').open", timeout=3000)
                    if (w, h) in ((1024, 768), (1024, 1120)):
                        owner.screenshot(path=str(SCRATCH / f"numbering_advanced_{lang}_{w}x{h}.png"))
                    real_click(owner, "#sh-discard", "Discard")
                    owner.wait_for_timeout(300)
        finally:
            owner.set_viewport_size({"width": 1366, "height": 768})
            set_lang(owner, "en")
            num_reset()

    pinned_check("numbering (H2): while the Save bar is up the Advanced summary is on screen, on top, and clickable (EN and AR, 1366 and 1024)",
          numbering_save_bar_never_covers_the_advanced_summary)

    def numbering_taken_till_number_is_hinted_before_save():
        num_reset()
        posts = spy_numbering_posts(owner)
        try:
            # Another computer already holds till 5 when the page loads, so the card
            # can (and should) say so at once, before anything is saved.
            st, body = num_post({"branch_code": "ABD"})
            assert st == 200, body
            rival_book("sale", "ABD5")
            numbering_hub()
            real_click(owner, "#num-readable", "Readable receipt numbers switch")
            owner.wait_for_selector("#num-till", state="visible", timeout=3000)
            note = owner.inner_text("#num-till-note")
            assert "Already used: 5" in note, f"the numbers in use are not listed: {note!r}"
            assert "Till number" not in note, f"a hint about a number nobody typed: {note!r}"
            set_input(owner, "#num-till", "5")
            note = owner.inner_text("#num-till-note")
            assert "Till number 5 is already used." in note, f"a taken number gets no hint: {note!r}"
            # the allow half: a free number gets no such hint
            # A number nobody holds. Earlier checks in the same run leave this shop's own
            # books at 6..9, so a hard-coded 6 is not free: read the list the card printed
            # and take the first number missing from it.
            used = [int(x) for x in re.search(r"Already used: ([\d, ]+)", note).group(1).replace(" ", "").strip(",").split(",") if x]
            free = next(n for n in range(10, 100) if n not in used)
            set_input(owner, "#num-till", str(free))
            note = owner.inner_text("#num-till-note")
            assert f"Till number {free} is already used." not in note and "Till number 5 is already used." not in note, note
            assert "Already used: 5" in note, note
            assert posts == [], f"typing sent something: {posts}"
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            num_reset()

    pinned_check("numbering: a till number another computer holds is hinted while typing, and a free one is not",
          numbering_taken_till_number_is_hinted_before_save)

    def numbering_fields_and_example_are_usable_at_a_narrow_main_column():
        # At 1024 the app sidebar + hub nav leave the main column ~470 px. Two
        # columns there leave the fields ~170 px. MEASURED on the live page: either
        # the example is stacked under the fields, or both columns stay usable.
        try:
            for w, h in ((1024, 768), (1366, 768)):
                owner.set_viewport_size({"width": w, "height": h})
                owner.wait_for_timeout(300)
                numbering_hub()
                m = owner.evaluate("""() => {
                  const f = document.querySelector('.sh-num-fields').getBoundingClientRect();
                  const e = document.getElementById('num-example-card').getBoundingClientRect();
                  return {fw: f.width, ew: e.width, fb: f.bottom, et: e.top};
                }""")
                stacked = m["et"] >= m["fb"] - 1
                assert stacked or (m["fw"] >= 240 and m["ew"] >= 200), f"crushed columns at {w}x{h}: {m}"
        finally:
            owner.set_viewport_size({"width": 1366, "height": 768})

    pinned_check("numbering: at a narrow main column the fields are not crushed (stacked, or both usable) at 1024 and 1366",
          numbering_fields_and_example_are_usable_at_a_narrow_main_column)

    def numbering_missing_backend_is_said_plainly():
        # Always exercised: answer the route with a 404 (an older backend) even
        # when the real route exists, so this branch is never skipped.
        owner.route("**/api/sub/retail/numbering*", lambda route, request: route.fulfill(
            status=404, content_type="application/json", body='{"status":"error","message":"not found"}'))
        try:
            open_hub(owner, "tills")
            assert owner.query_selector("#num-branch-code") is None
            assert "not available" in owner.inner_text("#numbering-codes-card")
            assert not savebar_up(owner)
        finally:
            owner.unroute("**/api/sub/retail/numbering*")

    check("numbering: with no /numbering route the card says so and offers no fields",
          numbering_missing_backend_is_said_plainly)

    # -------------------------------------------------------------------------
    # 7-U. Readable numbers are UNAVAILABLE until multi-computer support (the
    #      server says `available:false`). v42: the shipped default of the switch is
    #      now True, so these no longer run "against the shipped value" -- they FORCE
    #      it False (pinned_check(available=False)) to keep proving the page's
    #      unavailable state, which the server still enters when a computer has no
    #      till number from the main computer. What they can no longer catch: the
    #      shipped default itself; the ON-path checks above force it True, so the
    #      constant's real value is proved only by the backend numbering tests.
    # -------------------------------------------------------------------------
    # v42 wording: the reason is no longer "arrives with multi-computer support" -- it is
    # "the main computer has not given this computer a till number yet". The first is the
    # server's own sentence word for word (the page shows the server's reason when it sends
    # one); the second is the page's own sentence for a computer that WAS on and lost its number.
    UNAVAILABLE_REASON = ("Readable numbers are given out by the main computer, and this computer has "
                          "no till number from the main computer yet. Until it has one it uses the long "
                          "numbers, so two computers can never print the same receipt number.")
    WITHDRAWN_REASON = ("This computer was using readable numbers, but it has no till number from the "
                        "main computer now, so it uses the long numbers again.")

    def assert_arabic_only(txt, where):
        assert re.search(r"[؀-ۿ]", txt), f"{where}: not Arabic: {txt!r}"
        assert not re.search(r"[A-Za-z]{3,}", txt), f"{where}: English leaked into the Arabic text: {txt!r}"

    def numbering_unavailable_is_shown_and_cannot_be_turned_on():
        assert _retail_api.READABLE_NUMBERS_AVAILABLE is False, "this check must run with the switch forced to the unavailable state (pinned_check(available=False))"
        num_reset()
        br = num_branch()
        posts = spy_numbering_posts(owner)
        try:
            for lang in ("en", "ar"):
                set_lang(owner, lang)
                for w, h in ((1366, 768), (1024, 768)):
                    owner.set_viewport_size({"width": w, "height": h})
                    owner.wait_for_timeout(300)
                    numbering_hub()
                    where = f"[{lang} {w}x{h}]"
                    sw = owner.locator("#num-readable")
                    assert sw.is_disabled(), f"{where}: the switch can be used"
                    assert not owner.is_checked("#num-readable"), f"{where}: the switch shows ON"
                    assert sw.get_attribute("aria-describedby") == "num-unavailable", f"{where}: the switch is not tied to its reason"
                    reachable(owner, owner.locator("#num-unavailable"), f"{where} the reason")
                    # The switch row's SHAPE: a small box with its words beside it. The
                    # app's own `.ret-field input {width:100%}` once stretched the box over
                    # the whole row and squeezed the words to a sliver (seen only in a
                    # screenshot), and no reachability check could tell.
                    box = sw.bounding_box()
                    lab = owner.locator('label[for="num-readable"]').bounding_box()
                    assert box["width"] <= 48 and box["height"] <= 48, f"{where}: the switch box is {box['width']:.0f}x{box['height']:.0f}"
                    assert lab["width"] >= 120, f"{where}: the switch's words are squeezed to {lab['width']:.0f}px"
                    assert abs((box["y"] + box["height"] / 2) - (lab["y"] + lab["height"] / 2)) <= 24, \
                        f"{where}: the words are not on the switch's row ({box['y']:.0f} vs {lab['y']:.0f})"
                    txt = owner.inner_text("#num-unavailable").strip()
                    if lang == "en":
                        assert txt == UNAVAILABLE_REASON, f"{where}: {txt!r}"
                    else:
                        assert_arabic_only(txt, where)
                    # a real mouse click on the switch does nothing
                    real_click(owner, "#num-readable", f"{where} the switch")
                    owner.wait_for_timeout(200)
                    assert not owner.is_checked("#num-readable"), f"{where}: a click turned the switch on"
                    assert owner.locator("#num-till").is_disabled(), f"{where}: the till number can be typed"
                    assert not owner.locator("#num-till").is_visible(), f"{where}: the till number is on screen while off"
                    assert not savebar_up(owner), f"{where}: clicking a dead switch raised the Save bar"
                    owner.locator("#numbering-codes-card").screenshot(path=str(SCRATCH / f"numbering_unavailable_{lang}_{w}.png"))
            assert posts == [], f"the dead switch sent something: {posts}"
        finally:
            owner.set_viewport_size({"width": 1366, "height": 768})
            set_lang(owner, "en")
        try:
            # the rest of the card still works: the Advanced books list opens ...
            numbering_hub()
            real_click(owner, "#numbering-advanced > summary", "Advanced")
            owner.wait_for_function("() => document.getElementById('numbering-advanced').open", timeout=3000)
            # (the title and header row, not the whole tall card: see the same note above)
            reachable(owner, owner.locator("#doc-series-card > h3"), "books list under Advanced (unavailable state, its title)")
            reachable(owner, owner.locator("#doc-series-table thead th").first, "books list under Advanced (unavailable state, its header row)")
            # ... and the branch code still saves (that half is NOT withdrawn)
            set_input(owner, "#num-branch-code", "abg")
            assert savebar_up(owner), "a changed branch code did not raise the Save bar"
            save_click(owner)
            bar_gone(owner)
            assert posts == [{"branch_uid": br["uid"], "branch_code": "ABG"}], posts
            d = num_get()
            assert d["branch_code"] == "ABG" and d["readable_enabled"] is False and d["available"] is False, d
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            with pinned(available=True):
                num_reset()

    pinned_check("numbering unavailable: the switch is off and dead, the reason is on screen, the branch code and the books list still work (EN/AR, 1366/1024)",
                 numbering_unavailable_is_shown_and_cannot_be_turned_on, available=False)

    def numbering_409_code_is_said_in_words():
        num_reset()
        posts = []

        def handler(route, request):
            if request.method == "POST":
                posts.append(json.loads(request.post_data or "{}"))
                route.continue_()
                return
            # a page that BELIEVES the switch is available (older page, tampered
            # answer): the real server must still refuse, and the page must say why.
            resp = route.fetch()
            body = resp.json()
            body["data"]["available"] = True
            body["data"]["unavailable_reason"] = None
            route.fulfill(response=resp, body=json.dumps(body))
        owner.route("**/api/sub/retail/numbering*", handler)
        try:
            for lang in ("en", "ar"):
                set_lang(owner, lang)
                del posts[:]
                numbering_hub()
                set_input(owner, "#num-branch-code", "abd")
                real_click(owner, "#num-readable", "Readable receipt numbers switch (believed available)")
                owner.wait_for_selector("#num-till", state="visible", timeout=3000)
                set_input(owner, "#num-till", "7")
                save_click(owner)
                owner.wait_for_function("() => { const e = document.getElementById('num-error'); return e && !e.hidden && e.textContent.trim().length > 0; }", timeout=8000)
                assert len(posts) == 1 and posts[0].get("readable_enabled") is True, posts
                txt = num_error_text(owner)
                if lang == "en":
                    assert txt == UNAVAILABLE_REASON, txt
                else:
                    assert_arabic_only(txt, "409 refusal")
                assert savebar_up(owner), "a refused save dropped the bar"
                assert num_get()["readable_enabled"] is False, "the server switched it on anyway"
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            set_lang(owner, "en")
            with pinned(available=True):
                num_reset()

    pinned_check("numbering unavailable: the server's READABLE_NEEDS_DEVICE_REGISTRY 409 is said in plain words, EN and AR, and changes nothing",
                 numbering_409_code_is_said_in_words, available=False)

    def numbering_withdrawn_by_update_is_said():
        with pinned(available=True):
            num_reset()
            st, b = num_post({"branch_code": "ABD", "readable_enabled": True, "till_number": 6})
            assert st == 200, b
            reopen_shift()
        # ... and now the update that withdraws the feature arrives
        d = num_get()
        assert d["available"] is False and d["readable_switched_off_by_update"] is True, d
        try:
            numbering_hub()
            assert owner.locator("#num-readable").is_disabled()
            assert not owner.is_checked("#num-readable"), "the withdrawn switch still shows ON"
            reachable(owner, owner.locator("#num-unavailable"), "the withdrawn text")
            txt = owner.inner_text("#num-unavailable").strip()
            assert txt == WITHDRAWN_REASON, txt
            set_lang(owner, "ar")
            try:
                numbering_hub()
                assert_arabic_only(owner.inner_text("#num-unavailable").strip(), "withdrawn text")
            finally:
                set_lang(owner, "en")
        finally:
            with pinned(available=True):
                num_reset()

    pinned_check("numbering unavailable: a computer that WAS on is told an update switched readable numbers off (EN and AR)",
                 numbering_withdrawn_by_update_is_said, available=False)

    def numbering_stale_on_row_still_shows_off_while_unavailable():
        """The server says unavailable but still carries readable_enabled=true and a
        till number (an older row, or a reply from a mixed-version pair). The card
        must show the switch OFF and the number empty: showing ON would tell the owner
        readable numbers are running when they are not."""
        real = num_get()
        stale = dict(real, available=False, readable_enabled=True, till_number=6,
                     readable_switched_off_by_update=False)

        def handler(route, request):
            if request.method != "GET":
                return route.continue_()
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"status": "success", "data": stale}))
        owner.route("**/api/sub/retail/numbering*", handler)
        try:
            numbering_hub()
            assert owner.locator("#num-readable").is_disabled(), "the switch can be used"
            assert not owner.is_checked("#num-readable"), "a stale 'on' row made the switch show ON while unavailable"
            assert owner.input_value("#num-till") == "", "a stale till number was put in the field"
            assert not savebar_up(owner), "opening the card raised the Save bar"
        finally:
            owner.unroute("**/api/sub/retail/numbering*")

    pinned_check("numbering unavailable: a stale 'on' row is still shown OFF with no till number (the page never claims readable numbers are running)",
                 numbering_stale_on_row_still_shows_off_while_unavailable, available=False)

    def numbering_tampered_page_sends_nothing():
        num_reset()
        posts = spy_numbering_posts(owner)
        try:
            numbering_hub()
            # a person who edits the page to switch the dead switch back on
            owner.evaluate("() => { for (const id of ['num-readable', 'num-till']) document.getElementById(id).disabled = false; }")
            set_input(owner, "#num-branch-code", "abd")
            real_click(owner, "#num-readable", "the un-deadened switch")
            owner.wait_for_selector("#num-till", state="visible", timeout=3000)
            set_input(owner, "#num-till", "4")
            save_click(owner)
            owner.wait_for_function("() => { const e = document.getElementById('num-error'); return e && !e.hidden && e.textContent.trim().length > 0; }", timeout=8000)
            assert num_error_text(owner) == UNAVAILABLE_REASON, num_error_text(owner)
            assert posts == [], f"a switch-on for an unavailable feature was sent: {posts}"
            assert savebar_up(owner)
            assert num_get()["readable_enabled"] is False
            # The refused edit is still on screen with the Save bar up (asserted
            # above). Take it out the way a person would, so this check does not
            # hand a dirty hub to the next one -- the run-level "no check left the
            # hub dirty" guard (go() auto-discarding) caught exactly that here.
            real_click(owner, "#sh-discard", "Discard (leave the hub clean)")
            assert not savebar_up(owner), "Discard did not clear the refused edit"
        finally:
            owner.unroute("**/api/sub/retail/numbering*")
            with pinned(available=True):
                num_reset()

    pinned_check("numbering unavailable: a tampered page that turns the switch on is refused in the page and sends NO request",
                 numbering_tampered_page_sends_nothing, available=False)

    # =========================================================================
    # 7a. "Needs review": crew Q's sync-review card is mounted for the OWNER only
    # =========================================================================
    def needs_review_shows_the_sync_card_to_the_owner_only():
        go(owner, "exceptions", 1500)
        owner.wait_for_selector("#exq-sync-review", timeout=8000)
        first = owner.evaluate("() => { const h = document.getElementById('exq-sync-review'); "
                               "return h.parentElement.firstElementChild === h; }")
        assert first, "the sync card is not at the TOP of Needs review"
        owner.wait_for_function("() => document.getElementById('exq-sync-review').textContent.trim().length > 20", timeout=8000)
        assert owner.evaluate("() => !!document.getElementById('exq-stock-body')"), "the existing queues are gone"
        reachable(owner, owner.locator("#exq-sync-review"), "sync card on Needs review")
        # everyone else: no mount point, whether they reach the screen or not
        for name, pg in (("delegated manager", manager), ("plain manager", plainmgr), ("cashier", cashier)):
            go(pg, "exceptions", 1500)
            assert pg.locator("#exq-sync-review").count() == 0, f"{name} was given the owner-only sync card"
            assert pg.evaluate("() => !!document.querySelector('#sub-content') && document.querySelector('#sub-content').innerText.trim().length > 0"), \
                f"{name}: the screen is blank"

    check("needs review: the owner sees the sync card at the top; managers and cashiers get no mount point",
          needs_review_shows_the_sync_card_to_the_owner_only)

    # =========================================================================
    # 7b. Verifier round: F1 (crushed form at 1024)
    # =========================================================================
    def shop_form_not_crushed_at_1024_and_1366():
        # F1: at 1024 the window is wide but the app sidebar (240) + the hub nav (220)
        # leave the main column ~470 px; a viewport-only media query left the form
        # column ~100 px. What a person needs: the form column is a usable width, OR the
        # paper is stacked under the form. MEASURED on the live page, never read off CSS.
        problems = []
        try:
            for lang in ("en", "ar"):
                set_lang(owner, lang)
                for w, h in ((1024, 768), (1366, 768)):
                    owner.set_viewport_size({"width": w, "height": h})
                    owner.wait_for_timeout(300)
                    open_hub(owner, "shop-receipt")
                    m = owner.evaluate("""() => {
                      const f = document.getElementById('sh-shop-forms').getBoundingClientRect();
                      const split = document.querySelector('.sh-split');
                      const cols = getComputedStyle(split).gridTemplateColumns.trim().split(/\\s+/).length;
                      const pv = document.querySelector('.sh-preview').getBoundingClientRect();
                      const nm = document.getElementById('brand-name').getBoundingClientRect();
                      return {formW: f.width, formBottom: f.bottom, cols, pvTop: pv.top, pvW: pv.width, nameW: nm.width,
                              over: document.documentElement.scrollWidth - window.innerWidth};
                    }""")
                    stacked = m["cols"] == 1
                    tag = f"[{lang} {w}]"
                    if not (m["formW"] >= 360 or stacked):
                        problems.append(f"{tag} form column is {m['formW']:.0f}px and the paper is beside it: {m}")
                    if stacked and m["pvTop"] < m["formBottom"] - 1:
                        problems.append(f"{tag} stacked but the paper starts above the form's end: {m}")
                    if m["nameW"] < 180:  # two fields share a row at 1366: ~193 there, ~220 stacked; the crushed form was ~100
                        problems.append(f"{tag} the shop-name field is only {m['nameW']:.0f}px wide: {m}")
                    if m["over"] > 1:
                        problems.append(f"{tag} page overflows horizontally by {m['over']}px")
                    owner.screenshot(path=str(SCRATCH / f"f1_{lang}_{w}.png"))
        finally:
            owner.set_viewport_size({"width": 1366, "height": 768})
            set_lang(owner, "en")
        assert not problems, "\n      ".join(problems)

    check("look: F1 the Shop & receipt form is a usable width (>= 360px) or stacked, at 1024 and 1366, EN and AR",
          shop_form_not_crushed_at_1024_and_1366)

    # =========================================================================
    # 7c. Verifier round: F2 (the carry must not resurrect stale values)
    # =========================================================================
    OWNER_EMAIL, OWNER_PW = "owner@p1bhub.local", "P1bHubTest12345!"

    def f2_left_hub_then_theme_elsewhere_comes_back_clean():
        # Verifier scenario b: type, go AWAY, switch the theme while away, come back
        # inside the 15 s window. The abandoned name must NOT be there, and a Save
        # must not be able to store it (the bar is down, and the server is checked).
        prior = owner.evaluate("() => document.documentElement.getAttribute('data-theme')")
        try:
            open_hub(owner, "shop-receipt")
            set_input(owner, "#brand-name", "Abandoned Stale Name")
            assert savebar_up(owner)
            # With the leave guard live, go() answers 'Discard', which clears the carry by
            # itself and would hide the hub's own "I was taken off the page" drop. Guards
            # are dropped here (test seam) so THAT layer stays under test; the guarded
            # path is owned by the leave-guard checks.
            owner.evaluate("() => SubsystemApp._leaveGuards.clear()")
            go(owner, "dashboard", 600)
            switch_theme(owner)                  # a REAL theme change, hub not on screen
            owner.wait_for_timeout(600)
            open_hub(owner, "shop-receipt")
            assert owner.input_value("#brand-name") != "Abandoned Stale Name", (
                "the name typed before leaving the hub came back after a theme switch elsewhere")
            assert not savebar_up(owner), "the hub came back dirty after leaving it and switching theme elsewhere"
            # the server never received it either
            _, cur = api(owner, "GET", "/api/sub/retail/settings/branding")
            assert "Abandoned Stale Name" not in json.dumps(cur)
        finally:
            owner.evaluate("(n) => ThemeEngine.apply(n)", prior or "light")
            owner.wait_for_timeout(600)

    check("F2: type, leave the hub, switch theme elsewhere, come back -- clean, and Save cannot store the abandoned name",
          f2_left_hub_then_theme_elsewhere_comes_back_clean)

    def f2_old_carry_expires():
        # Age bound, with a FAKE CLOCK: a carry older than 15 s is not applied, even
        # though the hub stayed on screen and the theme really changed. Date.now is
        # overridden in the page (the test never sleeps 15 s).
        try:
            open_hub(owner, "shop-receipt")
            set_input(owner, "#brand-name", "Aged Out Name")
            assert savebar_up(owner)
            owner.evaluate("() => { window.__realNow = Date.now; Date.now = () => window.__realNow.call(Date) + 20000; }")
            mark_root(owner, "__agedOld")
            switch_theme(owner)
            wait_new_hub(owner, "__agedOld", preview=False)
            assert owner.input_value("#brand-name") != "Aged Out Name", "a 20-second-old carry was applied"
            assert not savebar_up(owner)
        finally:
            owner.evaluate("() => { if (window.__realNow) { Date.now = window.__realNow; delete window.__realNow; } }")
            owner.wait_for_timeout(300)
        # positive control on the same page: with the real clock a fresh carry DOES come back
        open_hub(owner, "shop-receipt")
        set_input(owner, "#brand-name", "Fresh Name")
        mark_root(owner, "__fresh")
        switch_theme(owner)
        wait_new_hub(owner, "__fresh")
        assert owner.input_value("#brand-name") == "Fresh Name", "the fresh carry did not come back: the age test above proves nothing"
        real_click(owner, "#sh-discard", "Discard")
        owner.wait_for_timeout(300)

    check("F2: a carry older than 15 s (fake clock) is not applied; a fresh one is", f2_old_carry_expires)

    def f2_next_person_does_not_get_the_last_persons_edit():
        # The owner leaves an unsaved choice, logs out through the header menu, the
        # theme is switched while the sign-in box is up, and the delegated manager
        # signs in. The manager's hub must be clean.
        st, b = api(owner, "POST", "/api/sub/retail/branches", {"name": "Hub Second Branch"})
        ctx2, pg2 = new_session(OWNER_EMAIL, OWNER_PW)
        try:
            open_hub(pg2, "tills")
            opts = pg2.eval_on_selector_all("#device-branch-select option", "els => els.map(e => e.value)")
            assert len(opts) >= 2, f"need two branches to make a real change, got {opts} (create branch: {st} {b})"
            cur = pg2.input_value("#device-branch-select")
            other = [o for o in opts if o != cur][0]
            set_input(pg2, "#device-branch-select", other)
            assert savebar_up(pg2), "choosing another branch did not raise the bar"
            # Since the leave guard went live, Log Out with unsaved edits ASKS first, and
            # 'Discard' clears the edit itself (proved by the leave-guard checks elsewhere in this file).
            # This check exists for the OTHER layer: even if nothing asked (a session that
            # ends by expiry, a crash, a guard that failed to register), the edit must not
            # reach the next person. So the guards are dropped on this page, by test seam,
            # to keep that second layer under test. It can no longer show what the
            # guarded logout does -- the 'signing out asks too' check owns that.
            pg2.evaluate("() => SubsystemApp._leaveGuards.clear()")
            real_click(pg2, "#sub-identity-chip-btn", "account chip")
            real_click(pg2, "#sub-identity-menu .sub-identity-menu-item.is-danger", "Log Out")
            pg2.wait_for_selector("#aura-relogin-modal", timeout=8000)
            switch_theme(pg2)                    # a real theme change while nobody is signed in
            pg2.wait_for_timeout(400)
            mark_root(pg2, "__prevPerson") if pg2.query_selector("#sh-root") else None
            pg2.fill("#rl-email", MANAGER_EMAIL)
            pg2.fill("#rl-pass", MANAGER_PW)
            real_click(pg2, "#rl-btn", "sign in")
            pg2.wait_for_selector("#aura-relogin-modal", state="detached", timeout=10000)
            pg2.wait_for_timeout(1500)
            # wherever the shell landed, get to the hub WITHOUT another theme switch
            if not pg2.query_selector("#sh-root #device-branch-select"):
                go(pg2, "admin-center", 600)
            pg2.wait_for_function("() => !!document.getElementById('device-branch-select') && "
                                  "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
            pg2.wait_for_timeout(400)
            who = pg2.evaluate("() => (SubsystemApp.currentUser || {}).email")
            assert who == MANAGER_EMAIL, f"the second sign-in is not the manager: {who!r}"
            assert pg2.input_value("#device-branch-select") == cur, (
                "the owner's unsaved branch choice appeared in the manager's hub")
            assert not savebar_up(pg2), "the manager's hub opened with the owner's unsaved bar"
        finally:
            ctx2.close()

    check("F2: the next signed-in person does not inherit the last person's unsaved choice",
          f2_next_person_does_not_get_the_last_persons_edit)

    # =========================================================================
    # 7d. Verifier round: F3 F6 F14 (tax + the bar's "where")
    # =========================================================================
    def spy_toasts(page):
        """Test-side spy: records every message SubsystemApp.showToast is asked to
        show (the on-screen toast lives ~3 s and is a bare div). Installed once."""
        page.evaluate("""() => { if (window.__toasts) return; window.__toasts = [];
          const orig = SubsystemApp.showToast.bind(SubsystemApp);
          SubsystemApp.showToast = (m, t) => { window.__toasts.push({m: String(m), t: t || 'info'}); return orig(m, t); }; }""")

    def toasts(page):
        return page.evaluate("() => (window.__toasts || []).slice()")

    def clear_toasts(page):
        page.evaluate("() => { window.__toasts = []; }")

    spy_toasts(owner)

    def server_tax():
        _, j = api(owner, "GET", "/api/sub/retail/settings/tax")
        return (j or {}).get("data") or {}

    def f3_half_saved_tax_group_then_discard():
        # Mode is changed AND the default rate is typed out of range. Save: the mode
        # goes through, the rate is refused. Discard must then leave the screen equal
        # to the SERVER (new mode, old rate) -- one merged group put the OLD mode back
        # on screen while the server held the new one.
        try:
            before = server_tax()
            open_hub(owner, "tax")
            old_mode = owner.input_value("#tax-mode")
            old_rate = owner.input_value("#tax-default-rate")
            new_mode = "after_discount" if old_mode == "before_discount" else "before_discount"
            set_input(owner, "#tax-mode", new_mode)
            set_input(owner, "#tax-default-rate", "150")
            assert savebar_up(owner)
            clear_toasts(owner)
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_timeout(1200)
            srv = server_tax()
            assert srv.get("tax_calculation_mode") == new_mode, f"the mode did not reach the server: {srv}"
            assert str(srv.get("default_tax_rate")) == str(before.get("default_tax_rate")), (
                f"the refused rate reached the server: before {before} after {srv}")
            assert savebar_up(owner), "the bar went away although the rate was refused"
            assert owner.input_value("#tax-default-rate") == "150", "the refused rate was thrown away on Save"
            assert owner.input_value("#tax-mode") == new_mode
            where = owner.inner_text("#sh-savebar-where")
            assert "Tax" in where, f"the bar does not say the unsaved change is in Tax: {where!r}"
            real_click(owner, "#sh-discard", "Discard")
            owner.wait_for_timeout(500)
            assert owner.input_value("#tax-mode") == srv.get("tax_calculation_mode"), (
                f"after Discard the screen shows mode {owner.input_value('#tax-mode')!r} but the server holds {srv.get('tax_calculation_mode')!r}")
            assert owner.input_value("#tax-default-rate") == old_rate, "Discard did not put the saved rate back"
            assert not savebar_up(owner), "the bar stayed up after Discard"
        finally:
            # leave the server as the later checks expect it (before_discount, rate 16)
            api(owner, "POST", "/api/sub/retail/settings/tax", {"tax_calculation_mode": "before_discount"})

    check("F3: mode saved + rate refused + Discard -- the screen equals the server, mode is not rolled back",
          f3_half_saved_tax_group_then_discard)

    def f6_bar_names_the_unsaved_section_as_a_link():
        open_hub(owner, "credit")
        saved_loyalty = owner.input_value("#credit-loyalty")
        set_input(owner, "#credit-loyalty", "0.75")
        real_click(owner, '#sh-root .sh-nav-item[data-sh="tax"]', "nav tax")
        set_input(owner, "#tax-default-rate", "17")
        links = owner.eval_on_selector_all("#sh-savebar-where button.sh-savebar-link[data-sh-goto]",
                                           "els => els.map(e => [e.dataset.shGoto, e.textContent.trim()])")
        assert [l[0] for l in links] == ["tax", "credit"], f"expected links to tax then credit, got {links}"
        assert "Unsaved in:" in owner.inner_text("#sh-savebar-where")
        # a person on Tax clicks the other name and lands on it
        real_click(owner, '#sh-savebar-where button[data-sh-goto="credit"]', "bar link Credit")
        owner.wait_for_timeout(400)
        assert visible_panes(owner) == ["credit"], visible_panes(owner)
        assert owner.input_value("#credit-loyalty") == "0.75", "following the link lost the typed value"
        # once one section is put right, its name leaves the bar
        set_input(owner, "#credit-loyalty", saved_loyalty)
        links = owner.eval_on_selector_all("#sh-savebar-where button.sh-savebar-link", "els => els.map(e => e.dataset.shGoto)")
        assert links == ["tax"], f"the bar still names a section that has nothing unsaved: {links}"
        real_click(owner, "#sh-discard", "Discard")
        owner.wait_for_timeout(300)
        assert not savebar_up(owner)
        assert owner.inner_text("#sh-savebar-where").strip() == ""

    check("F6: the bar names the unsaved sections as links that open them, and drops a name once it is clean",
          f6_bar_names_the_unsaved_section_as_a_link)

    def preview_text():
        return owner.evaluate("() => { const f = document.getElementById('sh-preview-frame'); "
                              "return f && f.contentDocument && f.contentDocument.body ? f.contentDocument.body.innerText : ''; }")

    def f14_sample_receipt_tax_follows_the_tax_mode():
        # Sample: 2 x 1.50 = 3.00 and 1 x 4.25 with 0.25 off -> subtotal 7.25, 16% tax.
        #   after_discount : tax 0.48 + 0.64 = 1.12, total 8.12
        #   before_discount: tax 0.48 + 0.68 = 1.16, total 8.16
        import re

        def has(txt, n):
            return re.search(r"(?<![\d.,])" + re.escape(n) + r"0?(?![\d])", txt) is not None
        # The mode is changed in the Tax pane and NOT saved: the sample follows the
        # selected mode, not the saved one.
        open_hub(owner, "tax")
        set_input(owner, "#tax-mode", "after_discount")
        set_input(owner, "#tax-mode", "after_discount")
        real_click(owner, '#sh-root .sh-nav-item[data-sh="shop-receipt"]', "nav shop")
        owner.wait_for_timeout(700)
        a = preview_text()
        assert has(a, "1.12") and has(a, "8.12"), f"after_discount sample should show tax 1.12 / total 8.12:\n{a}"
        assert not has(a, "1.16") and not has(a, "8.16"), f"after_discount sample shows before_discount figures:\n{a}"
        real_click(owner, '#sh-root .sh-nav-item[data-sh="tax"]', "nav tax")
        set_input(owner, "#tax-mode", "before_discount")
        real_click(owner, '#sh-root .sh-nav-item[data-sh="shop-receipt"]', "nav shop")
        owner.wait_for_timeout(700)
        b = preview_text()
        assert has(b, "1.16") and has(b, "8.16"), f"before_discount sample should show tax 1.16 / total 8.16:\n{b}"
        assert not has(b, "1.12") and not has(b, "8.12"), f"before_discount sample shows after_discount figures:\n{b}"
        assert has(a, "7.25") and has(b, "7.25"), "the subtotal must not move with the mode"

    check("F14: the sample receipt's tax and total follow the selected tax mode (both directions)",
          f14_sample_receipt_tax_follows_the_tax_mode)

    # ---- F4: a REFUSED logo must not end in "Branding saved." ------------------
    # A real 1x1 PNG the server accepts, and a 700 KB blob wearing a PNG data URL
    # that the REAL server refuses for size (no stub: the refusal is the server's).
    import base64
    TINY_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    BIG_PNG = TINY_PNG + b"\0" * (700 * 1024)

    def server_branding():
        _, j = api(owner, "GET", "/api/sub/retail/settings/branding")
        return (j or {}).get("data") or {}

    def logo_thumb():
        return owner.evaluate("() => { const p = document.getElementById('brand-logo-preview'); "
                              "return p ? {shown: getComputedStyle(p).display !== 'none' && !!p.getAttribute('src'), "
                              "src: (p.getAttribute('src') || '').slice(0, 40)} : null; }")

    def f4_refused_logo_is_not_reported_as_saved():
        before = server_branding()
        assert not before.get("has_logo"), f"the check assumes no logo yet: {before}"
        try:
            open_hub(owner, "shop-receipt")
            old_name = owner.input_value("#brand-name")
            new_name = (old_name + " F4").strip()   # the form trims; compare what the server will hold
            set_input(owner, "#brand-name", new_name)
            owner.set_input_files("#brand-logo-file", {"name": "big.png", "mimeType": "image/png", "buffer": BIG_PNG})
            owner.wait_for_timeout(300)
            assert savebar_up(owner)
            clear_toasts(owner)
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_timeout(1500)
            msgs = [x["m"] for x in toasts(owner)]
            assert not any("Branding saved" in m for m in msgs), f"a refused logo ended in 'Branding saved.': {msgs}"
            assert any(m.startswith("The details were saved, but the logo was not") for m in msgs), f"no honest two-halves toast: {msgs}"
            srv = server_branding()
            assert srv.get("branding_business_name") == new_name, f"the text half did not reach the server: {srv}"
            assert not srv.get("has_logo"), "the refused logo reached the server"
            assert savebar_up(owner), "the bar went away although the logo was refused"
            th = logo_thumb()
            assert th and not th["shown"], f"the thumbnail still shows the refused file: {th}"
            # Discard puts the picker back to empty and the bar goes
            real_click(owner, "#sh-discard", "Discard")
            owner.wait_for_timeout(400)
            assert not savebar_up(owner)
            assert owner.eval_on_selector("#brand-logo-file", "e => e.files.length") == 0
            # positive control: a logo the server takes ends in "Branding saved." and a clean bar
            owner.set_input_files("#brand-logo-file", {"name": "ok.png", "mimeType": "image/png", "buffer": TINY_PNG})
            owner.wait_for_timeout(300)
            clear_toasts(owner)
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_timeout(1500)
            msgs = [x["m"] for x in toasts(owner)]
            assert any("Branding saved" in m for m in msgs), f"an accepted logo did not say 'Branding saved.': {msgs}"
            assert server_branding().get("has_logo"), "the accepted logo did not reach the server"
            assert not savebar_up(owner), "the bar stayed up after a fully accepted Save"
            assert logo_thumb()["shown"], "the accepted logo is not on screen"
        finally:
            api(owner, "DELETE", "/api/sub/retail/settings/branding/logo")

    check("F4: a refused logo is said plainly (details saved, logo not), keeps the bar and the old thumbnail; an accepted one says saved",
          f4_refused_logo_is_not_reported_as_saved)

    # ---- F5: accepting a reorder request must refresh the PO list ---------------
    def f5_accepting_a_reorder_request_refreshes_the_po_list():
        # Real chain, no stub: a product at its reorder level + a real sale makes the
        # server file a pending request; the owner accepts it in Purchasing with real
        # clicks; the PO list right beside it must show the new order at once (it
        # used to keep saying "No purchase orders yet" under a success toast).
        # STOCK-BOOKS verify F3 (2026-10-05): a reorder PO is born unpaid, so the
        # product must name its supplier or accept is refused 409
        # SUPPLIER_REQUIRED_FOR_CREDIT_PO. This setup predates that rule; it now
        # gives the product a supplier. It no longer exercises a supplier-less
        # reorder accept -- that refusal is pinned in retail_stock_books_routes_test.
        _, sb = api(owner, "POST", "/api/sub/retail/suppliers", {"name": "Hub Reorder Supplier"})
        sup_id = ((sb or {}).get("data") or {}).get("id")
        assert sup_id, f"reorder supplier was not created: {sb}"
        _, b = api(owner, "POST", "/api/sub/retail/products", {
            "name": "Hub Reorder Widget", "sku": "H-RO1", "sell_price": 5, "cost_price": 2,
            "category_id": CAT_SNACKS, "unit": "pc", "reorder_level": 5,
            "reorder_method": "whatsapp", "initial_stock": 6, "supplier_id": sup_id,
        })
        pid = ((b or {}).get("data") or {}).get("id")
        assert pid, f"reorder product was not created: {b}"
        st, sale = api(owner, "POST", "/api/sub/retail/sales", {
            "items": [{"product_id": pid, "quantity": 2}], "amount_paid": 999, "payment_method": "cash",
            "idempotency_key": "hub-f5-sale-1",
        })
        assert st == 200, f"sale failed: {st} {sale}"
        _, rr = api(owner, "GET", "/api/sub/retail/reorder-requests")
        assert any(r.get("product_id") == pid for r in (rr or {}).get("data", [])), \
            f"the sale did not file a reorder request (the test's own setup): {rr}"
        _, pos = api(owner, "GET", "/api/sub/retail/purchase-orders")
        assert not (pos or {}).get("data"), f"the check assumes no purchase orders yet: {pos}"
        go(owner, "purchases", 1200)
        assert "No purchase orders yet" in owner.inner_text("#po-table"), owner.inner_text("#po-table")
        clear_toasts(owner)
        real_click(owner, "#reorder-req-table tbody tr button.ret-btn-primary", "Accept")
        owner.wait_for_selector("#ret-confirm-dialog", timeout=5000)
        real_click(owner, "#ret-confirm-ok", "confirm OK")
        owner.wait_for_timeout(1800)
        msgs = [x["m"] for x in toasts(owner)]
        assert any(m.startswith("Purchase order drafted") for m in msgs), f"accept did not succeed: {msgs}"
        po_text = owner.inner_text("#po-table")
        assert "No purchase orders yet" not in po_text, f"the PO list still says empty after the accept:\n{po_text}"
        assert owner.locator("#po-table tbody tr").count() == 1, po_text

    check("F5: accepting a reorder request shows the new purchase order in the list at once",
          f5_accepting_a_reorder_request_refreshes_the_po_list)

    # ---- F7: the printer fields ride the ONE bar (device-local group) -----------
    def printer_cfg(page=None):
        return (page or owner).evaluate("() => JSON.parse(localStorage.getItem('aura_printer_cfg') || '{}')")

    def f7_printer_fields_join_the_one_bar():
        open_hub(owner, "hardware")
        assert owner.query_selector("#pr-save-btn") is None, "the printer card kept a Save button of its own"
        assert not savebar_up(owner), "the bar is up before anything was changed"
        start = owner.input_value("#pr-width")
        other = "58mm" if start == "80mm" else "80mm"
        try:
            set_input(owner, "#pr-width", other)
            assert savebar_up(owner), "changing the paper width did not raise the bar"
            assert "Hardware" in owner.inner_text("#sh-savebar-where"), owner.inner_text("#sh-savebar-where")
            assert printer_cfg().get("paperWidth", "80mm") == start, "the width was saved before Save was pressed"
            # Discard puts it back and drops the bar
            real_click(owner, "#sh-discard", "Discard")
            owner.wait_for_timeout(300)
            assert owner.input_value("#pr-width") == start and not savebar_up(owner)
            # and Save persists it to this computer
            set_input(owner, "#pr-width", other)
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_timeout(700)
            assert printer_cfg().get("paperWidth") == other, printer_cfg()
            assert not savebar_up(owner), "the bar stayed up after the printer group was saved"
        finally:
            set_input(owner, "#pr-width", start)
            if savebar_up(owner):
                real_click(owner, "#sh-save", "Save")
                owner.wait_for_timeout(500)

    check("F7: the printer fields join the one Save bar (no Save of their own); Discard and Save both work",
          f7_printer_fields_join_the_one_bar)

    # ---- F8: a refused e-invoicing change keeps the choice and the bar ----------
    def f8_refused_einvoicing_change_keeps_choice_and_bar():
        open_hub(owner, "tax")
        v0 = owner.input_value("#einvoicing-regime")
        other = "none" if v0 == "jordan" else "jordan"
        refusal = "Regime refused by the test stub"

        def refuse(route):
            if route.request.method == "POST":
                route.fulfill(status=400, content_type="application/json",
                              body=json.dumps({"status": "error", "message": refusal}))
            else:
                route.continue_()
        owner.route("**/api/einvoicing/settings", refuse)
        try:
            set_input(owner, "#einvoicing-regime", other)
            assert savebar_up(owner)
            clear_toasts(owner)
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_timeout(1200)
            assert any(refusal in x["m"] for x in toasts(owner)), f"the refusal was not shown: {toasts(owner)}"
            assert owner.input_value("#einvoicing-regime") == other, "the refused choice snapped back on screen"
            assert savebar_up(owner), "the bar went away although the change was refused"
        finally:
            owner.unroute("**/api/einvoicing/settings")
        # Discard puts the SAVED value back
        real_click(owner, "#sh-discard", "Discard")
        owner.wait_for_timeout(400)
        assert owner.input_value("#einvoicing-regime") == v0 and not savebar_up(owner)

    check("F8: a refused e-invoicing change keeps the person's choice and the bar, and says why",
          f8_refused_einvoicing_change_keeps_choice_and_bar)

    # ---- F12: printer/drawer server buttons only for people whose calls succeed --
    def f12_server_buttons_follow_who_may_use_them():
        # allowed: the owner sees the buttons and no read-only note
        open_hub(owner, "hardware")
        for sel in ("#pr-device", "#pr-hw-test-btn", "#pr-hw-kick-btn"):
            reachable(owner, owner.locator(sel), sel)
        assert owner.query_selector("#pr-hw-readonly-note") is None
        # not allowed: a plain manager (no CAP_EMPLOYEES) sees the note and no buttons
        go(plainmgr, "scanner", 900)
        assert plainmgr.query_selector("#printer-card") is not None, "the plain manager has no printer card at all"
        assert plainmgr.query_selector("#pr-hw-test-btn") is None, "a plain manager is shown a button whose call would 403"
        assert plainmgr.query_selector("#pr-hw-kick-btn") is None
        assert plainmgr.query_selector("#pr-device") is None
        reachable(plainmgr, plainmgr.locator("#pr-hw-readonly-note"), "read-only note")
        # ...and their Save must KEEP the device / host / port / drawer the card hides
        plainmgr.evaluate("() => localStorage.setItem('aura_printer_cfg', JSON.stringify("
                          "{paperWidth: '80mm', printer: 'HUB-PRINTER', host: '10.9.9.9', port: 9101, autoKick: true}))")
        go(plainmgr, "scanner", 700)
        set_input(plainmgr, "#pr-width", "58mm")
        real_click(plainmgr, "#pr-save-btn", "Save")
        plainmgr.wait_for_timeout(500)
        cfg = printer_cfg(plainmgr)
        assert cfg.get("paperWidth") == "58mm", cfg
        assert (cfg.get("printer"), cfg.get("host"), cfg.get("port"), cfg.get("autoKick")) == ("HUB-PRINTER", "10.9.9.9", 9101, True), \
            f"a Save by someone who cannot see those fields erased them: {cfg}"

    check("F12: printer/drawer server buttons show for people whose calls succeed, a read-only note for the rest (and their Save keeps hidden values)",
          f12_server_buttons_follow_who_may_use_them)

    # ---- F13: the category-rate partial result names the failed product ---------
    def f13_partial_category_rate_names_the_failures():
        open_hub(owner, "tax")
        owner.select_option("#tax-cat-select", str(CAT_DRINKS))
        set_input(owner, "#tax-cat-rate", "7")

        def fail_cola(route):
            req = route.request
            if req.method == "PATCH" and PID_D1 in req.url:
                route.fulfill(status=400, content_type="application/json",
                              body=json.dumps({"status": "error", "message": "stubbed refusal"}))
            else:
                route.continue_()
        owner.route("**/api/sub/retail/products/*", fail_cola)
        try:
            clear_toasts(owner)
            real_click(owner, "#tax-cat-apply", "Change tax rate")
            owner.wait_for_selector("#ret-confirm-dialog", timeout=5000)
            real_click(owner, "#ret-confirm-ok", "confirm OK")
            owner.wait_for_function("() => /could not be changed/.test(document.getElementById('tax-cat-status').textContent)", timeout=10000)
            status = owner.inner_text("#tax-cat-status")
            assert "Hub Cola" in status, f"the status does not name the product that failed: {status}"
            assert "Hub Juice" not in status, f"the status names a product that WAS changed: {status}"
            assert "Changed 1 of 2" in status, status
            assert any(x["m"] == "Some products could not be changed." for x in toasts(owner)), toasts(owner)
        finally:
            owner.unroute("**/api/sub/retail/products/*")
            for pid in (PID_D1, PID_D2):
                api(owner, "PATCH", f"/api/sub/retail/products/{pid}", {"tax_rate": 16})

    check("F13: a partly failed category rate change names the product(s) that were not changed",
          f13_partial_category_rate_names_the_failures)

    # ---- M3: the bar stays in the viewport however far a long section is scrolled
    def m3_savebar_stays_in_view_while_scrolling():
        open_hub(owner, "shop-receipt")
        old = owner.input_value("#brand-name")
        set_input(owner, "#brand-name", (old + " M3").strip())
        assert savebar_up(owner)
        try:
            def bar_box():
                return owner.evaluate("() => { const r = document.getElementById('sh-savebar').getBoundingClientRect(); "
                                      "return {top: r.top, bottom: r.bottom, vh: window.innerHeight, h: r.height}; }")
            # the section is taller than the window (else this proves nothing)
            owner.evaluate("() => { const p = document.querySelector('[data-sh-pane=\"shop-receipt\"]'); p.firstElementChild.scrollIntoView({block: 'start'}); }")
            owner.wait_for_timeout(200)
            tall = owner.evaluate("() => { const p = document.querySelector('[data-sh-pane=\"shop-receipt\"]'); "
                                  "return p.lastElementChild.getBoundingClientRect().bottom > window.innerHeight; }")
            assert tall, "the Shop & receipt section fits in the window; the scroll check would prove nothing"
            b = bar_box()
            assert b["h"] > 0 and b["top"] >= 0 and b["bottom"] <= b["vh"] + 1, f"bar not in view at the top of a long section: {b}"
            reachable(owner, owner.locator("#sh-save"), "Save at the top")
            owner.evaluate("() => { const p = document.querySelector('[data-sh-pane=\"shop-receipt\"]'); p.lastElementChild.scrollIntoView({block: 'end'}); }")
            owner.wait_for_timeout(200)
            b = bar_box()
            assert b["top"] >= 0 and b["bottom"] <= b["vh"] + 1, f"bar not in view at the bottom of a long section: {b}"
            reachable(owner, owner.locator("#sh-save"), "Save at the bottom")
        finally:
            real_click(owner, "#sh-discard", "Discard")
            owner.wait_for_timeout(300)

    check("M3: the Save bar stays inside the window at the top and at the bottom of a long section",
          m3_savebar_stays_in_view_while_scrolling)

    # =========================================================================
    # 7c. Setup guide ("Get your shop ready"): index.html is another crew's file,
    #     so the test loads setup-guide.js the way the tag will, on every page.
    # =========================================================================
    SG_ROUTE = "**/api/sub/retail/setup-checklist*"
    SG_SERVER_STEPS = ["licence", "main_computer", "shop_name_logo", "default_gst", "products",
                       "cashier_invited", "readable_numbers", "first_backup", "jofotara"]

    def sg_load(page):
        page.evaluate("() => { const o = document.querySelector('.sg-overlay'); if (o) o.remove(); }")
        if not page.evaluate("() => !!window.AuraSetupGuide"):
            page.add_script_tag(url=BASE + "/static/setup-guide.js")
        sg_clear_ticks(page)

    def sg_clear_ticks(page):
        # H2 item 5a: ticks are keyed per company AND user now ('aura_setup_ticks:<co>:<user>'),
        # with the old global 'aura_setup_ticks' as a one-time fallback. Clear every
        # variant, or one check's ticks would leak into the next.
        page.evaluate("""() => { try { Object.keys(localStorage).filter(k => k.indexOf('aura_setup_ticks') === 0)
            .forEach(k => localStorage.removeItem(k)); } catch (e) {} }""")

    def sg_ticks(page):
        # the tick store the guide itself writes to (its own key, not a guess at it)
        return page.evaluate("""() => { try { return JSON.parse(localStorage.getItem(window.AuraSetupGuide.ticksKey()) || '{}'); }
            catch (e) { return {}; } }""")

    def sg_set_ticks(page, ticks):
        page.evaluate("(t) => localStorage.setItem(window.AuraSetupGuide.ticksKey(), JSON.stringify(t))", ticks)

    def sg_answer(page, done_by_id, default=False):
        rows = [{"id": i, "done": done_by_id.get(i, default)} for i in SG_SERVER_STEPS]
        page.route(SG_ROUTE, lambda route, request: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"status": "success", "data": {"steps": rows}})))

    SG_NUM_ROUTE = "**/api/sub/retail/numbering*"

    def sg_numbering(page, available):
        """What the numbering route says about readable numbers. The shipped server
        says available:false, which HIDES the 'readable numbers' step (it could
        never be finished); the checks that count every step stub it true."""
        page.route(SG_NUM_ROUTE, lambda route, request: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"status": "success", "data": {"available": available}})))

    def sg_home(page, wait_ms=1600):
        go(page, "dashboard", 300)
        page.wait_for_timeout(wait_ms)

    def sg_close_any(page):
        page.evaluate("() => { const o = document.querySelector('.sg-overlay'); if (o) o.remove(); }")

    def sg_owner_sees_the_card_with_a_ring_and_a_button():
        sg_load(owner)
        sg_home(owner)
        owner.wait_for_selector("#setup-guide-card", timeout=8000)
        info = reachable(owner, owner.locator("#setup-guide-card"), "setup card on the home screen")
        txt = owner.inner_text("#setup-guide-card")
        assert re.search(r"\d+ of \d+ steps done", txt), f"no progress sentence in the card: {txt!r}"
        assert owner.locator("#setup-guide-card .sg-ring svg").count() == 1, "no progress ring"
        assert owner.locator('#setup-guide-card [data-sg="open"]').inner_text().strip() == "See the steps"
        # in the page flow, never floating over the till
        fixed = owner.evaluate("() => { let n = document.getElementById('setup-guide-card'); "
                               "while (n) { if (getComputedStyle(n).position === 'fixed') return true; n = n.parentElement; } return false; }")
        assert not fixed, "the card sits in a fixed-position layer"
        go(owner, "pos", 1000)
        assert owner.locator("#setup-guide-card").count() == 0, "the card is on the till screen"
        assert owner.locator(".sg-overlay").count() == 0

    check("setup guide: the owner sees the card (progress ring, 'N of M', See the steps) on the home screen and not on the till",
          sg_owner_sees_the_card_with_a_ring_and_a_button)

    def sg_dialog_opens_lists_steps_goes_and_closes():
        sg_load(owner)
        # 'Open Products' is offered only while products is NOT done: say so, whatever the seed did
        sg_answer(owner, {}, default=False)
        sg_numbering(owner, True)
        try:
            sg_dialog_body()
        finally:
            owner.unroute(SG_ROUTE)
            owner.unroute(SG_NUM_ROUTE)
            sg_close_any(owner)

    def sg_dialog_body():
        sg_home(owner)
        owner.wait_for_selector("#setup-guide-card", timeout=8000)
        real_click(owner, '#setup-guide-card [data-sg="open"]', "See the steps")
        owner.wait_for_selector(".sg-dialog li.sg-step", timeout=8000)
        ids = owner.evaluate("() => [...document.querySelectorAll('.sg-dialog li.sg-step')].map(l => l.dataset.step)")
        for want in SG_SERVER_STEPS + ["printer_tested", "whatsapp_desktop"]:
            assert want in ids, f"step {want} is missing from the list {ids}"
        reachable(owner, owner.locator(".sg-dialog"), "setup dialog")
        owner.screenshot(path=str(SCRATCH / "sg_dialog_en.png"))
        # Escape closes it
        owner.keyboard.press("Escape")
        owner.wait_for_timeout(300)
        assert owner.locator(".sg-overlay").count() == 0, "Escape did not close the dialog"
        # the Close button closes it
        real_click(owner, '#setup-guide-card [data-sg="open"]', "See the steps again")
        owner.wait_for_selector("#sg-close", timeout=8000)
        real_click(owner, "#sg-close", "Close")
        owner.wait_for_timeout(300)
        assert owner.locator(".sg-overlay").count() == 0, "Close did not close the dialog"
        # a step's button takes the owner there (and shuts the dialog)
        real_click(owner, '#setup-guide-card [data-sg="open"]', "See the steps a third time")
        owner.wait_for_selector('.sg-dialog [data-sg-go="products"]', timeout=8000)
        real_click(owner, '.sg-dialog [data-sg-go="products"]', "Open Products")
        owner.wait_for_timeout(900)
        assert owner.locator(".sg-overlay").count() == 0, "the dialog stayed open after a step button"
        active = owner.evaluate("() => SubsystemApp.currentSection")
        assert active == "products", f"the step button went to {active!r}, not products"

    check("setup guide: the dialog lists every step, closes on Escape and on Close, and a step button goes to its screen",
          sg_dialog_opens_lists_steps_goes_and_closes)

    def sg_never_for_anyone_but_the_owner():
        for name, pg in (("delegated manager", manager), ("plain manager", plainmgr), ("cashier", cashier)):
            sg_load(pg)
            asked = []
            def spy(req, a=asked):
                if "setup-checklist" in req.url:
                    a.append(req.url)
            pg.on("request", spy)
            try:
                sg_home(pg, 1500)
                assert pg.locator("#setup-guide-card").count() == 0, f"{name} was shown the setup card"
                assert pg.locator(".sg-overlay").count() == 0
                opened = pg.evaluate("async () => await window.AuraSetupGuide.open()")
                assert opened is False, f"{name}: open() answered {opened!r}"
                assert pg.locator(".sg-overlay").count() == 0, f"{name} got the dialog"
                cleared = pg.evaluate("async () => { const d = document.createElement('div'); d.innerHTML = '<p>x</p>'; "
                                      "document.body.appendChild(d); await window.AuraSetupGuide.mount(d); "
                                      "const out = d.innerHTML; d.remove(); return out; }")
                assert cleared == "", f"{name}: mount() left {cleared!r} in the slot"
                assert not asked, f"{name} asked the server for the checklist: {asked}"
            finally:
                pg.remove_listener("request", spy)

    check("setup guide: a manager or cashier is never shown it, cannot open it, and never even asks the server",
          sg_never_for_anyone_but_the_owner)

    def sg_a_null_step_needs_i_did_this_and_100_percent_clears_the_card():
        sg_load(owner)
        # everything done, EXCEPT one step the server cannot tell (null) and one plainly not done (false)
        sg_set_ticks(owner, {"printer_tested": True, "whatsapp_desktop": True})
        answer = {i: True for i in SG_SERVER_STEPS}
        answer["cashier_invited"] = None
        answer["products"] = False
        sg_answer(owner, answer)
        sg_numbering(owner, True)
        try:
            sg_home(owner)
            owner.wait_for_selector("#setup-guide-card", timeout=8000)
            assert "8 of 10 steps done" in owner.inner_text("#setup-guide-card"), owner.inner_text("#setup-guide-card")
            real_click(owner, '#setup-guide-card [data-sg="open"]', "See the steps")
            owner.wait_for_selector(".sg-dialog li.sg-step", timeout=8000)
            row_null = 'li.sg-step[data-step="cashier_invited"]'
            row_false = 'li.sg-step[data-step="products"]'
            assert owner.locator(f"{row_null} [data-sg-tick]").count() == 1, "a step the server cannot tell has no 'I did this'"
            assert "is-done" not in (owner.get_attribute(row_null, "class") or ""), \
                "a null step was counted as done without the owner saying so"
            assert owner.locator(f"{row_false} [data-sg-tick]").count() == 0, "a step that is plainly NOT done offers 'I did this'"
            # the owner says the null step is done, then does the false one
            real_click(owner, f"{row_null} [data-sg-tick]", "I did this")
            owner.wait_for_function("() => document.querySelector('.sg-dialog li[data-step=\"cashier_invited\"]').classList.contains('is-done')", timeout=8000)
            assert sg_ticks(owner).get("cashier_invited") is True, f"the tick was not remembered: {sg_ticks(owner)}"
            # products becomes done on the server: the card and the list finish
            sg_close_any(owner)
            owner.unroute(SG_ROUTE)
            answer["products"] = True
            sg_answer(owner, answer)
            sg_home(owner)
            assert owner.locator("#setup-guide-card").count() == 0, "at 100% the card is still on the home screen"
            assert owner.inner_text("#setup-guide-mount").strip() == "", "at 100% the slot is not empty"
        finally:
            owner.unroute(SG_ROUTE)
            owner.unroute(SG_NUM_ROUTE)
            sg_clear_ticks(owner)

    check("setup guide: a step the server cannot tell stays open until 'I did this'; a plain 'no' offers no tick; 100% removes the card",
          sg_a_null_step_needs_i_did_this_and_100_percent_clears_the_card)

    def sg_readable_step_is_hidden_while_unavailable_and_back_when_it_is_not():
        sg_load(owner)
        sg_answer(owner, {}, default=False)

        def steps_and_total():
            sg_home(owner)
            owner.wait_for_selector("#setup-guide-card", timeout=8000)
            total = int(re.search(r"of (\d+) steps done", owner.inner_text("#setup-guide-card")).group(1))
            real_click(owner, '#setup-guide-card [data-sg="open"]', "See the steps")
            owner.wait_for_selector(".sg-dialog li.sg-step", timeout=8000)
            ids = owner.evaluate("() => [...document.querySelectorAll('.sg-dialog li.sg-step')].map(l => l.dataset.step)")
            sg_close_any(owner)
            return ids, total
        try:
            # the SHIPPED server: readable numbers unavailable -> the step is not asked of the owner
            with pinned(available=False):
                ids, total = steps_and_total()
            assert "readable_numbers" not in ids, f"an unattainable step is on the owner's list: {ids}"
            assert total == len(ids) - 1 == 9, (total, ids)  # jofotara is listed but optional: not counted
            # the allow half: available -> the step is back and counted
            sg_numbering(owner, True)
            ids, total = steps_and_total()
            owner.unroute(SG_NUM_ROUTE)
            assert "readable_numbers" in ids and total == len(ids) - 1 == 10, (total, ids)
            # the numbering route cannot be read -> fail OPEN (keep the step), never hide on a guess
            owner.route(SG_NUM_ROUTE, lambda route, request: route.fulfill(
                status=500, content_type="application/json", body='{"status":"error","message":"boom"}'))
            ids, total = steps_and_total()
            assert "readable_numbers" in ids and total == 10, (total, ids)
        finally:
            owner.unroute(SG_ROUTE)
            owner.unroute(SG_NUM_ROUTE)
            sg_close_any(owner)
            sg_clear_ticks(owner)

    check("setup guide: the 'readable numbers' step is hidden (and not counted) while the feature is unavailable, back when it is, kept if unreadable",
          sg_readable_step_is_hidden_while_unavailable_and_back_when_it_is_not)

    def sg_failed_route_shows_nothing_not_a_wrong_number():
        sg_load(owner)
        owner.route(SG_ROUTE, lambda route, request: route.fulfill(
            status=500, content_type="application/json", body='{"status":"error","message":"boom"}'))
        try:
            sg_home(owner)
            assert owner.locator("#setup-guide-card").count() == 0, "a failed checklist still drew a card"
            assert owner.inner_text("#sub-content").strip(), "the home screen went blank"
        finally:
            owner.unroute(SG_ROUTE)

    check("setup guide: if the checklist cannot be read the card stays away and the home screen still works",
          sg_failed_route_shows_nothing_not_a_wrong_number)

    def sg_only_a_real_receipt_test_ticks_the_printer_step():
        sg_load(owner)
        sg_home(owner, 700)

        def call_test(kick, body):
            owner.route("**/api/sub/retail/printer/test", lambda route, request: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)))
            try:
                owner.evaluate("async (k) => { await RetailSystem._testEscPosPrint(k); }", kick)
            finally:
                owner.unroute("**/api/sub/retail/printer/test")
            owner.wait_for_timeout(200)

        call_test(False, {"status": "success", "data": {"sent": False, "bytes": 12}})
        assert "printer_tested" not in sg_ticks(owner), "a file written with no printer ticked the step"
        call_test(True, {"status": "success", "data": {"sent": True}})
        assert "printer_tested" not in sg_ticks(owner), "a drawer kick ticked the step"
        call_test(False, {"status": "error", "message": "no"})
        assert "printer_tested" not in sg_ticks(owner), "a refused test ticked the step"
        call_test(False, {"status": "success", "data": {"sent": True}})
        assert sg_ticks(owner).get("printer_tested") is True, f"a real test receipt did not tick: {sg_ticks(owner)}"
        sg_clear_ticks(owner)

    check("setup guide: only a test receipt that really reached a printer ticks 'Send a test receipt' (not a file, a drawer kick or a refusal)",
          sg_only_a_real_receipt_test_ticks_the_printer_step)

    def sg_browser_test_print_ticks_only_if_the_dialog_really_opened():
        # H2 item 2. The browser "Test Print" used to tick the step the moment it
        # was clicked, whatever happened. Now it ticks only if print() returned
        # without throwing. Both directions: a print() that throws must NOT tick,
        # a print() that returns must. (The browser cannot tell Print from Cancel;
        # a dialog that is never answered blocks print(), so it never reaches the tick.)
        sg_load(owner)
        sg_home(owner, 700)
        sg_clear_ticks(owner)

        def run_test_print(print_body):
            owner.evaluate("""(body) => {
                let f = document.getElementById('ret-print-frame');
                if (!f) {
                    f = document.createElement('iframe');
                    f.id = 'ret-print-frame';
                    f.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
                    document.body.appendChild(f);
                }
                f.contentWindow.print = new Function(body);
            }""", print_body)
            owner.evaluate("async () => { await RetailSystem._testPrint(); }")
            owner.wait_for_timeout(200)

        run_test_print("throw new Error('no print dialog available')")
        assert "printer_tested" not in sg_ticks(owner), \
            "a print() that threw still ticked 'Send a test receipt'"
        run_test_print("/* dialog opened and closed */")
        assert sg_ticks(owner).get("printer_tested") is True, \
            f"a print() that returned did not tick: {sg_ticks(owner)}"
        sg_clear_ticks(owner)

    check("setup guide: the browser Test Print ticks 'Send a test receipt' only when print() really ran (a print() that throws does not tick)",
          sg_browser_test_print_ticks_only_if_the_dialog_really_opened)

    # =========================================================================
    # 7c-H2. Setup guide fix round (crew H2). Each check below guards one thing the
    #        verifier found wrong; the mutation that turns it red is in progress.md.
    # =========================================================================
    SG_BRAND_ROUTE = "**/api/sub/retail/settings/branding"

    def sg_branding(page, name):
        def handler(route, request):
            if request.method != "GET":
                route.continue_()
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"status": "success", "data": {"branding_business_name": name}}))
        page.route(SG_BRAND_ROUTE, handler)

    def sg_stub(page, answer, name="", readable=True):
        """Everything the guide asks the server, stubbed: the checklist, the numbering
        route and the saved shop name. Re-callable (drops the old stubs first)."""
        sg_unstub(page)
        sg_answer(page, answer)
        sg_numbering(page, readable)
        sg_branding(page, name)

    def sg_unstub(page):
        for pat in (SG_ROUTE, SG_NUM_ROUTE, SG_BRAND_ROUTE):
            page.unroute(pat)

    def sg_all_true(**over):
        ans = {i: True for i in SG_SERVER_STEPS}
        ans.update(over)
        return ans

    def sg_rows(page):
        """Open the dialog and read what a person would read: per step, is it done,
        does it offer 'I did this' / a go-button / Undo, and its visible text."""
        sg_close_any(page)
        page.evaluate("() => { window.AuraSetupGuide.open(); }")
        page.wait_for_selector(".sg-dialog li.sg-step", timeout=8000)
        return page.evaluate("""() => { const out = {};
            document.querySelectorAll('.sg-dialog li.sg-step').forEach(l => {
              out[l.dataset.step] = {done: l.classList.contains('is-done'),
                tick: !!l.querySelector('[data-sg-tick]'), go: !!l.querySelector('[data-sg-go]'),
                untick: !!l.querySelector('[data-sg-untick]'), waiting: !!l.querySelector('.sg-waiting'),
                text: l.innerText}; });
            const sub = document.querySelector('.sg-dialog .sg-sub');
            out._sub = {text: sub ? sub.innerText : ''};
            return out; }""")

    def sg_teardown(page):
        sg_unstub(page)
        sg_close_any(page)
        sg_clear_ticks(page)

    # ---- item 3a: the licence step can never be hand-ticked ------------------
    def sg_licence_is_a_fact_never_a_tick():
        sg_load(owner)
        sg_home(owner, 500)
        # a stale 'licence' tick from an older build must not count either
        sg_set_ticks(owner, {"licence": True, "printer_tested": True, "whatsapp_desktop": True})
        try:
            for label, val in (("null (the server cannot tell)", None), ("false", False)):
                sg_stub(owner, sg_all_true(licence=val))
                rows = sg_rows(owner)
                r = rows["licence"]
                assert not r["done"], f"licence answered {label} was counted done: {r}"
                assert not r["tick"], f"licence answered {label} offers 'I did this': {r}"
                assert r["go"], f"licence answered {label} has no way to go and fix it: {r}"
                assert "9 of 10 steps done" in rows["_sub"]["text"], (label, rows["_sub"])
            # the allow half: the server saying true closes it, with no button left
            sg_stub(owner, sg_all_true())
            rows = sg_rows(owner)
            assert rows["licence"]["done"] and not rows["licence"]["go"], rows["licence"]
            assert "Everything is set up." in rows["_sub"]["text"], rows["_sub"]
            # ...and the ban is on the licence only: another step the server cannot tell still takes a tick
            sg_stub(owner, sg_all_true(cashier_invited=None))
            sg_clear_ticks(owner)
            rows = sg_rows(owner)
            assert rows["cashier_invited"]["tick"], "the licence rule swallowed the tick on an ordinary unknown step"
        finally:
            sg_teardown(owner)

    check("setup guide (H2): the licence step is never hand-ticked, not for null, not for false, not from a stale tick; true still closes it",
          sg_licence_is_a_fact_never_a_tick)

    # ---- item 3b: e-invoicing is optional, uncounted, and says why -----------
    def sg_jofotara_is_optional_and_waiting():
        sg_load(owner)
        sg_set_ticks(owner, {"printer_tested": True, "whatsapp_desktop": True})
        try:
            # everything required done, e-invoicing not: the card is GONE (it must not hold 100% back)
            sg_stub(owner, sg_all_true(jofotara=False))
            sg_home(owner)
            assert owner.locator("#setup-guide-card").count() == 0, "an open optional step keeps the card on the home screen"
            rows = sg_rows(owner)
            j = rows["jofotara"]
            assert not j["done"] and j["waiting"], f"e-invoicing does not show it is waiting: {j}"
            assert "Optional" in j["text"], j["text"]
            assert "tax authority" in j["text"], j["text"]
            assert not j["tick"], f"e-invoicing can be hand-ticked: {j}"
            assert "Everything is set up." in rows["_sub"]["text"], rows["_sub"]
            sg_close_any(owner)
            # allow half: one REQUIRED step open -> the card is back, counts 10 not 11, and 'Next' is that step
            sg_stub(owner, sg_all_true(jofotara=False, products=False))
            sg_home(owner)
            owner.wait_for_selector("#setup-guide-card", timeout=8000)
            card = owner.inner_text("#setup-guide-card")
            assert "9 of 10 steps done" in card, card
            assert "Next: Add your products" in card, card
            assert "e-invoicing" not in card.lower(), f"e-invoicing was offered as the next step: {card!r}"
        finally:
            sg_teardown(owner)

    check("setup guide (H2): e-invoicing is optional - not counted, never 'Next', shows it is waiting on the tax authority, cannot hold the card up",
          sg_jofotara_is_optional_and_waiting)

    # ---- item 3c: a shop name alone finishes the shop step -------------------
    def sg_shop_name_alone_is_enough():
        sg_load(owner)
        try:
            sg_stub(owner, sg_all_true(shop_name_logo=False, products=False), name="Corner Shop")
            rows = sg_rows(owner)
            assert rows["shop_name_logo"]["done"], f"a saved shop name with no logo left the step open: {rows['shop_name_logo']}"
            # the deny half: no name (blank) and the server saying no -> still open, with its button
            sg_stub(owner, sg_all_true(shop_name_logo=False, products=False), name="   ")
            rows = sg_rows(owner)
            r = rows["shop_name_logo"]
            assert not r["done"] and r["go"], f"an empty shop name closed the step: {r}"
            # and the server's own yes still closes it whatever the name says
            sg_stub(owner, sg_all_true(products=False), name="")
            assert sg_rows(owner)["shop_name_logo"]["done"]
        finally:
            sg_teardown(owner)

    check("setup guide (H2): a shop name on its own completes 'Add your shop name' (logo optional); a blank name does not",
          sg_shop_name_alone_is_enough)

    # ---- item 3d: the WhatsApp step opens the real WhatsApp setup ------------
    def sg_whatsapp_step_opens_the_real_whatsapp_page():
        sg_load(owner)
        sg_stub(owner, sg_all_true(products=False))
        try:
            rows = sg_rows(owner)
            w = rows["whatsapp_desktop"]
            assert w["go"] and w["tick"] and not w["done"], w
            assert "Open WhatsApp" in w["text"], w["text"]
            real_click(owner, '.sg-dialog [data-sg-go="whatsapp_desktop"]', "Open WhatsApp")
            owner.wait_for_url("**/static/whatsapp.html*", timeout=10000)
            assert owner.url.split("?")[0].endswith("/static/whatsapp.html"), owner.url
        finally:
            sg_teardown(owner)
            # get the app back for the checks after this one
            owner.goto(BASE)
            dismiss_intro_overlay(owner)
            owner.wait_for_selector("#sub-header-section", timeout=20000)
            owner.wait_for_timeout(600)

    check("setup guide (H2): 'Set up WhatsApp on this computer' opens the real WhatsApp page",
          sg_whatsapp_step_opens_the_real_whatsapp_page)

    # ---- item 5a: ticks are kept per company AND user -------------------------
    def sg_ticks_are_per_user_with_one_legacy_read():
        sg_load(owner)
        sg_stub(owner, sg_all_true(cashier_invited=None, products=False))
        try:
            key = owner.evaluate("() => AuraSetupGuide.ticksKey()")
            uid = owner.evaluate("() => String(SubsystemApp.currentUser && SubsystemApp.currentUser.id)")
            assert key.startswith("aura_setup_ticks:") and key.endswith(":" + uid) and uid not in ("", "undefined", "null", "anon"), (key, uid)
            rows = sg_rows(owner)
            assert rows["cashier_invited"]["tick"]
            real_click(owner, 'li.sg-step[data-step="cashier_invited"] [data-sg-tick]', "I did this")
            owner.wait_for_function("() => document.querySelector('.sg-dialog li[data-step=\"cashier_invited\"]').classList.contains('is-done')", timeout=8000)
            assert sg_ticks(owner).get("cashier_invited") is True
            assert owner.evaluate("() => localStorage.getItem('aura_setup_ticks')") is None, "the tick was written to the old global key"
            # another person on the same computer does not inherit it
            owner.evaluate("() => { window.__h2user = SubsystemApp.currentUser; "
                           "SubsystemApp.currentUser = Object.assign({}, window.__h2user, {id: 'someone-else'}); }")
            try:
                assert owner.evaluate("() => AuraSetupGuide.isDone('cashier_invited')") is False, "a second user inherited the first user's tick"
                rows = sg_rows(owner)
                assert rows["cashier_invited"]["tick"] and not rows["cashier_invited"]["done"], rows["cashier_invited"]
            finally:
                owner.evaluate("() => { SubsystemApp.currentUser = window.__h2user; }")
            assert owner.evaluate("() => AuraSetupGuide.isDone('cashier_invited')") is True, "the first user lost their own tick"
            # the old global key is read ONCE, so ticks made before this change are not lost...
            sg_clear_ticks(owner)
            owner.evaluate("() => localStorage.setItem('aura_setup_ticks', JSON.stringify({cashier_invited: true}))")
            assert owner.evaluate("() => AuraSetupGuide.isDone('cashier_invited')") is True, "an old tick was lost"
            owner.evaluate("async () => { await AuraSetupGuide.tick('printer_tested', true); }")
            scoped = sg_ticks(owner)
            assert scoped.get("cashier_invited") is True and scoped.get("printer_tested") is True, scoped
            # ...and never again once the person has their own key
            owner.evaluate("() => localStorage.setItem('aura_setup_ticks', JSON.stringify({products: true}))")
            assert owner.evaluate("() => AuraSetupGuide.isDone('products')") is False, "the old global key is still being read"
        finally:
            sg_teardown(owner)
            owner.evaluate("() => { try { localStorage.removeItem('aura_setup_ticks'); } catch (e) {} }")

    check("setup guide (H2): ticks are stored per user (a second user does not inherit them); the old global key is read once, then never",
          sg_ticks_are_per_user_with_one_legacy_read)

    # ---- item 1: the guide is Arabic on an Arabic page ------------------------
    LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")

    def latin_words(text):
        text = re.sub(r"\b[A-Z]+-\d+-\d+\b", "", text)          # the sample receipt number AMM-1-00001 stays as written
        return sorted(set(LATIN_WORD.findall(text)))

    def arabic_unmerged(page):
        """A fresh page: nothing a previous check merged into the live dictionary
        survives a reload, so what is on screen is what locales/ar.json alone says.
        The real shipped catalogue is the only Arabic there is: nothing is injected."""
        page.reload()
        dismiss_intro_overlay(page)
        page.wait_for_selector("#sub-header-section", timeout=20000)
        page.wait_for_timeout(700)
        page.evaluate("(l) => AuraI18n.setLang(l)", "ar")
        page.wait_for_timeout(500)

    def back_to_english(page):
        set_lang(page, "en")

    def sg_guide_is_arabic_without_any_dictionary_merge():
        arabic_unmerged(owner)
        sg_load(owner)
        sg_set_ticks(owner, {"printer_tested": True})
        sg_stub(owner, sg_all_true(products=False, cashier_invited=None, licence=None, jofotara=False))
        try:
            sg_home(owner, 1800)
            owner.wait_for_selector("#setup-guide-card", timeout=8000)
            assert owner.evaluate("() => document.documentElement.dir") == "rtl"
            card = owner.inner_text("#setup-guide-card")
            bad = latin_words(card)
            assert not bad, f"English words on the Arabic card: {bad} in {card!r}"
            assert re.search(r"[؀-ۿ]", card), card
            assert owner.locator('#setup-guide-card .sg-sub bdi[dir="ltr"]').count() == 2, "the two numbers are not wrapped for bidi"
            owner.screenshot(path=str(SCRATCH / "sg_card_ar.png"))
            rows = sg_rows(owner)
            dialog = owner.inner_text(".sg-dialog")
            bad = latin_words(dialog)
            assert not bad, f"English words in the Arabic setup dialog: {bad}"
            assert owner.locator('.sg-dialog .sg-sub bdi[dir="ltr"]').count() == 2
            assert len(rows) - 1 == 11, rows.keys()
            owner.screenshot(path=str(SCRATCH / "sg_dialog_ar.png"))
        finally:
            sg_teardown(owner)
            back_to_english(owner)

    check("setup guide (H2): in Arabic, with NO extra dictionary merged, the card and the dialog have no English words and wrap their numbers",
          sg_guide_is_arabic_without_any_dictionary_merge)

    # ---- item 4: no English literal beside Arabic empty states ---------------
    ITEM4_SCREENS = {
        "customers": ["Loyalty Points", "Total Spent"],
        "returns": ["Return #", "Original Sale", "Refund Method"],
        "scanner": ["Scanner Status", "Test Scanner", "Configuration", "Scanner Enabled", "Input Mode",
                    "Barcode Timeout (ms)", "Min Length", "Prefix (stripped)", "Suffix (stripped)", "Success Sound",
                    "Save Settings", "Reset",
                    "Click below, then scan any barcode — the captured value appears instantly.",
                    "Note:"],
    }

    def item4_screen_text(page, section):
        go(page, section, 1200)
        return page.evaluate("() => (document.getElementById('sub-content') || document.body).textContent").lower()

    def customers_returns_scanner_have_no_english_literals_in_arabic():
        arabic_unmerged(owner)
        problems = []
        try:
            for section, literals in ITEM4_SCREENS.items():
                text = item4_screen_text(owner, section)
                for lit in literals:
                    if lit.lower() in text:
                        problems.append(f"[{section}] still shows English {lit!r}")
                owner.screenshot(path=str(SCRATCH / f"item4_{section}_ar.png"))
        finally:
            back_to_english(owner)
        assert not problems, "\n      ".join(problems)

    check("Arabic (H2): the Customers, Returns and Barcode Scanner screens carry no English literal, with NO extra dictionary merged",
          customers_returns_scanner_have_no_english_literals_in_arabic)

    def item4_english_unchanged():
        # the allow half: in English the same screens still say what they always said
        for section, literals in ITEM4_SCREENS.items():
            text = item4_screen_text(owner, section)
            missing = [lit for lit in literals if lit.lower() not in text]
            assert not missing, f"[{section}] English lost {missing}"

    check("English (H2): wrapping the literals in t() left the English Customers, Returns and Scanner screens unchanged",
          item4_english_unchanged)


    # =========================================================================
    # 8. Looking at it: EN/AR x sand/night x every section, no overflow
    # =========================================================================
    # =========================================================================
    # 7c. Leaving the hub with unsaved changes asks first (owner-approved
    #     'Discard unsaved changes?'; crew P's SubsystemApp.registerLeaveGuard)
    # =========================================================================
    LG_DIALOG = "#ret-confirm-dialog"

    def lg_guard_count(page):
        return page.evaluate("() => SubsystemApp._leaveGuards.size")

    def lg_dialog_open(page):
        return page.locator(LG_DIALOG).count() > 0

    def lg_dirty_hub():
        open_hub(owner, "credit")
        base = owner.input_value("#credit-loyalty")
        set_input(owner, "#credit-loyalty", "0.25" if base != "0.25" else "0.5")
        assert savebar_up(owner)
        return base

    def lg_leave_click(page):
        real_click(page, '.sub-nav-item[data-section="dashboard"]', "Home row")
        page.wait_for_selector(LG_DIALOG, timeout=5000)

    def leave_guard_stay_keeps_everything():
        go(owner, "dashboard", 400)
        idle = lg_guard_count(owner)
        base = lg_dirty_hub()
        typed = owner.input_value("#credit-loyalty")
        assert lg_guard_count(owner) == idle + 1, "the hub did not register a leave guard"
        # moving between the hub's OWN sections is not leaving: no question
        real_click(owner, '#sh-root .sh-nav-item[data-sh="tax"]', "nav tax")
        assert owner.evaluate("() => SettingsHub.open('credit')") is not False
        owner.wait_for_timeout(300)
        assert not lg_dialog_open(owner), "moving between the hub's own sections asked 'Discard unsaved changes?'"
        lg_leave_click(owner)
        text = owner.inner_text(LG_DIALOG)
        assert "Discard unsaved changes?" in text, text
        reachable(owner, owner.locator("#ret-confirm-cancel"), "Keep editing")
        real_click(owner, "#ret-confirm-cancel", "Keep editing")
        owner.wait_for_function(f"() => !document.querySelector('{LG_DIALOG}')", timeout=3000)
        assert owner.evaluate("() => SubsystemApp.currentSection") == "admin-center", "'Keep editing' still left the hub"
        assert savebar_up(owner), "'Keep editing' dropped the Save bar"
        assert owner.input_value("#credit-loyalty") == typed, "'Keep editing' lost what was typed"
        # and the guard is still armed: leaving again asks again
        lg_leave_click(owner)
        real_click(owner, "#ret-confirm-cancel", "Keep editing")
        owner.wait_for_function(f"() => !document.querySelector('{LG_DIALOG}')", timeout=3000)
        real_click(owner, "#sh-discard", "Discard")
        assert owner.input_value("#credit-loyalty") == base

    check("leave guard: leaving with unsaved edits asks; 'Keep editing' stays with the values and the Save bar intact; the hub's own sections never ask",
          leave_guard_stay_keeps_everything)

    def leave_guard_go_discards_and_leaves():
        go(owner, "dashboard", 400)
        base = lg_dirty_hub()
        lg_leave_click(owner)
        assert "Discard" in owner.inner_text("#ret-confirm-ok")
        real_click(owner, "#ret-confirm-ok", "Discard")
        owner.wait_for_function("() => SubsystemApp.currentSection === 'dashboard'", timeout=5000)
        assert not lg_dialog_open(owner)
        # what was typed is gone for good: the hub comes back clean and equal to the server
        open_hub(owner, "credit")
        assert owner.input_value("#credit-loyalty") == base, "the abandoned value came back"
        assert not savebar_up(owner), "the hub came back with an Unsaved bar"
        st, body = api(owner, "GET", "/api/sub/retail/settings/credit")
        assert st == 200, body

    check("leave guard: 'Discard' leaves, and the abandoned edit does not come back", leave_guard_go_discards_and_leaves)

    def leave_guard_clean_hub_leaves_freely_and_no_stale_guard():
        go(owner, "dashboard", 400)
        idle = lg_guard_count(owner)
        open_hub(owner, "credit")
        assert not savebar_up(owner)
        assert lg_guard_count(owner) == idle + 1
        real_click(owner, '.sub-nav-item[data-section="dashboard"]', "Home row")
        owner.wait_for_function("() => SubsystemApp.currentSection === 'dashboard'", timeout=5000)
        assert not lg_dialog_open(owner), "a clean hub asked 'Discard unsaved changes?'"
        owner.wait_for_timeout(300)
        assert lg_guard_count(owner) == idle, f"the hub left its guard behind ({lg_guard_count(owner)} vs {idle})"
        # dirty, discard-and-leave, then travel on: a stale guard would ask on other screens too
        lg_dirty_hub()
        lg_leave_click(owner)
        real_click(owner, "#ret-confirm-ok", "Discard")
        owner.wait_for_function("() => SubsystemApp.currentSection === 'dashboard'", timeout=5000)
        go(owner, "products", 500)
        go(owner, "dashboard", 500)
        assert not lg_dialog_open(owner), "a screen other than the hub asked 'Discard unsaved changes?'"
        assert owner.evaluate("() => SubsystemApp.currentSection") == "dashboard"
        assert lg_guard_count(owner) == idle

    check("leave guard: a clean hub leaves without a question, and no guard is left behind on other screens",
          leave_guard_clean_hub_leaves_freely_and_no_stale_guard)

    def leave_guard_asks_on_logout_both_answers():
        go(owner, "dashboard", 400)
        lg_dirty_hub()
        reqs = []
        owner.route("**/api/auth/logout*", lambda route, request: (reqs.append(request.url), route.abort()))
        # A seam on the step AFTER the guard, so answering 'Discard' does not really
        # sign the shared test session out: reaching it proves the guard let logout through.
        owner.evaluate("() => { window.__afterGuard = 0; SubsystemApp.__origUnsynced = SubsystemApp._confirmSignOutWhileUnsynced; "
                       "SubsystemApp._confirmSignOutWhileUnsynced = async () => { window.__afterGuard++; return false; }; }")
        try:
            def press_logout():
                real_click(owner, "#sub-identity-chip-btn", "identity chip")
                real_click(owner, "#sub-identity-menu .is-danger", "Log Out")
                owner.wait_for_selector(LG_DIALOG, timeout=5000)
            press_logout()
            assert "Discard unsaved changes?" in owner.inner_text(LG_DIALOG)
            real_click(owner, "#ret-confirm-cancel", "Keep editing")
            owner.wait_for_function(f"() => !document.querySelector('{LG_DIALOG}')", timeout=3000)
            owner.wait_for_timeout(300)
            assert owner.evaluate("() => window.__afterGuard") == 0, "'Keep editing' still carried on with the logout"
            assert reqs == [], f"a logout request was sent although the person said stay: {reqs}"
            assert savebar_up(owner) and owner.evaluate("() => SubsystemApp.currentSection") == "admin-center"
            press_logout()
            real_click(owner, "#ret-confirm-ok", "Discard")
            owner.wait_for_function("() => window.__afterGuard === 1", timeout=5000)
            assert not savebar_up(owner), "'Discard' let the sign-out go on but left the unsaved bar up"
            assert reqs == [], "the unsynced-check seam should have stopped the sign-out before any request"
        finally:
            owner.evaluate("() => { if (SubsystemApp.__origUnsynced) SubsystemApp._confirmSignOutWhileUnsynced = SubsystemApp.__origUnsynced; delete window.__afterGuard; }")
            owner.unroute("**/api/auth/logout*")
            go(owner, "dashboard", 400)

    check("leave guard: signing out asks too -- 'Keep editing' does not sign out, 'Discard' lets it through",
          leave_guard_asks_on_logout_both_answers)

    # =========================================================================
    # 7d. Teaching empty states: an empty list says what to do AND offers it;
    #     a list with data shows no empty state at all (both directions).
    # =========================================================================
    def stub_rows(page, pattern, rows):
        def handler(route, request):
            if request.method != "GET":
                return route.continue_()
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"status": "success", "data": rows}))
        page.route(pattern, handler)

    CUSTOMERS_URL = "**/api/sub/retail/customers*"
    RETURNS_URL = "**/api/sub/retail/returns*"
    SERIES_URL = "**/api/sub/retail/doc-series*"

    def empty_cta(page, table, label):
        """The empty state's action button, found and asserted by its words."""
        sel = f"{table} .ret-empty-actions button"
        page.wait_for_selector(sel, timeout=8000)
        assert page.locator(sel).count() == 1, "expected exactly one action on the empty state"
        assert label in page.inner_text(sel), page.inner_text(sel)
        return sel

    def empty_customers_offer_add_and_data_hides_it():
        stub_rows(owner, CUSTOMERS_URL, [])
        try:
            go(owner, "customers", 900)
            owner.wait_for_selector("#cust-table .ret-empty", timeout=8000)
            sel = empty_cta(owner, "#cust-table", "Add Customer")
            real_click(owner, sel, "Add Customer (empty state)")
            owner.wait_for_selector("#ret-cust-modal", timeout=5000)
            owner.keyboard.press("Escape")
            owner.wait_for_selector("#ret-cust-modal", state="detached", timeout=5000)
            set_lang(owner, "ar")
            go(owner, "dashboard", 300)
            go(owner, "customers", 900)
            owner.wait_for_selector("#cust-table .ret-empty", timeout=8000)
            assert "إضافة عميل" in owner.inner_text("#cust-table .ret-empty-actions button"), \
                owner.inner_text("#cust-table .ret-empty-actions button")
            set_lang(owner, "en")
        finally:
            owner.unroute(CUSTOMERS_URL)
            set_lang(owner, "en")
        stub_rows(owner, CUSTOMERS_URL, [{"id": "c1", "name": "Probe Customer", "phone": "0790000000",
                                          "email": "", "loyalty_points": 0, "total_spent": 0, "order_count": 0}])
        try:
            go(owner, "dashboard", 300)
            go(owner, "customers", 900)
            owner.wait_for_selector("#cust-table tbody tr", timeout=8000)
            assert "Probe Customer" in owner.inner_text("#cust-table"), owner.inner_text("#cust-table")
            assert owner.locator("#cust-table .ret-empty").count() == 0, "an empty state showed beside real customers"
        finally:
            owner.unroute(CUSTOMERS_URL)

    check("empty states: Customers with none offers 'Add Customer' (EN and AR) and it opens the form; with customers no empty state shows",
          empty_customers_offer_add_and_data_hides_it)

    def empty_returns_offer_process_and_data_hides_it():
        stub_rows(owner, RETURNS_URL, [])
        try:
            go(owner, "returns", 900)
            owner.wait_for_selector("#ret-table .ret-empty", timeout=8000)
            sel = empty_cta(owner, "#ret-table", "Process Return")
            real_click(owner, sel, "Process Return (empty state)")
            owner.wait_for_selector("#ret-return-modal", timeout=5000)
            owner.keyboard.press("Escape")
            owner.wait_for_selector("#ret-return-modal", state="detached", timeout=5000)
            set_lang(owner, "ar")
            go(owner, "dashboard", 300)
            go(owner, "returns", 900)
            owner.wait_for_selector("#ret-table .ret-empty", timeout=8000)
            assert "تسجيل مرتجع" in owner.inner_text("#ret-table .ret-empty-actions button"), \
                owner.inner_text("#ret-table .ret-empty-actions button")
        finally:
            owner.unroute(RETURNS_URL)
            set_lang(owner, "en")
        stub_rows(owner, RETURNS_URL, [{"id": "r1", "return_number": "R-1", "sale_number": "S-1",
                                        "customer_name": "Probe Buyer", "refund_method": "cash",
                                        "refund_amount": 5, "created_at": "2026-09-29T10:00:00"}])
        try:
            go(owner, "dashboard", 300)
            go(owner, "returns", 900)
            owner.wait_for_selector("#ret-table tbody tr", timeout=8000)
            assert "Probe Buyer" in owner.inner_text("#ret-table"), owner.inner_text("#ret-table")
            assert owner.locator("#ret-table .ret-empty").count() == 0, "an empty state showed beside real returns"
        finally:
            owner.unroute(RETURNS_URL)

    check("empty states: Returns with none offers 'Process Return' (EN and AR) and it opens the form; with returns no empty state shows",
          empty_returns_offer_process_and_data_hides_it)

    def empty_books_owner_gets_create_book_manager_does_not():
        stub_rows(owner, SERIES_URL, [])
        try:
            open_hub(owner, "tills")
            owner.wait_for_selector("#doc-series-table .ret-empty", state="attached", timeout=8000)
            assert owner.evaluate("() => document.getElementById('numbering-advanced').open") is False
            real_click(owner, "#numbering-advanced > summary", "Advanced")
            sel = empty_cta(owner, "#doc-series-table", "Create Book")
            real_click(owner, sel, "Create Book (empty state)")
            owner.wait_for_function("() => document.activeElement && document.activeElement.id === 'doc-series-new-code'",
                                    timeout=3000)
            reachable(owner, owner.locator("#doc-series-new-code"), "the New Book code field after the button")
            # the button must open a CLOSED Advanced by itself, not rely on it being open already
            real_click(owner, "#numbering-advanced > summary", "close Advanced")
            owner.wait_for_function("() => !document.getElementById('numbering-advanced').open", timeout=3000)
            owner.evaluate("() => RetailSystem._focusNewDocSeries()")
            assert owner.evaluate("() => document.getElementById('numbering-advanced').open") is True, \
                "the Create Book button left Advanced closed, so the form it points at stayed hidden"
            assert owner.evaluate("() => document.activeElement && document.activeElement.id") == "doc-series-new-code"
            # a manager may claim existing books but may not create one (403): told what a book is, offered nothing
            stub_rows(manager, SERIES_URL, [])
            go(manager, "admin-center", 400)
            manager.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
            manager.wait_for_selector("#doc-series-table .ret-empty", state="attached", timeout=8000)
            assert manager.locator("#doc-series-table .ret-empty-actions").count() == 0, \
                "a manager was offered a Create Book button the server would refuse"
            assert "No numbering books yet" in manager.inner_text("#doc-series-table .ret-empty-title")
        finally:
            owner.unroute(SERIES_URL)
            try:
                manager.unroute(SERIES_URL)
            except Exception:  # noqa: BLE001
                pass
        stub_rows(owner, SERIES_URL, [{"id": "b1", "doc_type": "sale", "code": "ZQ", "label": "Probe book",
                                       "branch_uid": "", "allocator_terminal_uid": "", "is_mine": False,
                                       "next_no": None, "status": "active"}])
        try:
            open_hub(owner, "tills")
            owner.wait_for_selector("#doc-series-table tbody tr td", state="attached", timeout=8000)
            # textContent, not innerText: the list sits inside the closed Advanced fold,
            # and innerText leaves out what is not rendered.
            owner.wait_for_function("() => document.getElementById('doc-series-table').textContent.includes('Probe book')",
                                    timeout=8000)
            assert owner.locator("#doc-series-table .ret-empty").count() == 0, "an empty state showed beside real books"
        finally:
            owner.unroute(SERIES_URL)

    check("empty states: no numbering books -- the owner gets a 'Create Book' that opens Advanced and focuses the form, a manager gets none; with books no empty state",
          empty_books_owner_gets_create_book_manager_does_not)

    def look_at_every_section_in_both_languages_and_themes():
        problems = []
        for lang in ("en", "ar"):
            set_lang(owner, lang)
            for theme in ("sand", "night"):
                set_theme(owner, theme)
                open_hub(owner)
                for sid in OWNER_SECTIONS:
                    real_click(owner, f'#sh-root .sh-nav-item[data-sh="{sid}"]', f"nav {sid}")
                    owner.wait_for_timeout(700)
                    over = owner.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
                    if over > 1:
                        problems.append(f"[{lang}/{theme}/{sid}] page overflows horizontally by {over}px")
                    rtl = owner.evaluate("() => document.documentElement.dir")
                    if (lang == "ar") != (rtl == "rtl"):
                        problems.append(f"[{lang}/{theme}/{sid}] document dir is {rtl!r}")
                    owner.screenshot(path=str(SCRATCH / f"hub_{lang}_{theme}_{sid}.png"))
        set_lang(owner, "en")
        set_theme(owner, "sand")
        assert not problems, "\n      ".join(problems)

    check("look: every section in EN/AR x sand/night at 1366x768 has no horizontal overflow (screenshots saved)",
          look_at_every_section_in_both_languages_and_themes)

    def no_leave_prompt_was_ever_auto_discarded():
        # H2 item 6: go() answers "Discard" to the leave guard so one dirty check cannot
        # wedge the rest of the run. That answer used to be logged and never read, so a
        # check that quietly left the hub dirty was invisible. Every check that wants to
        # leave unsaved edits behind must discard them ITSELF; this fails on any that did not.
        assert not LEAVE_PROMPTS, "checks that left the hub dirty (check, section): " + repr(LEAVE_PROMPTS)

    check("no check left the hub with unsaved edits (go() never had to auto-discard)",
          no_leave_prompt_was_ever_auto_discarded)

    def no_page_errors():
        mine = [e for e in PAGE_ERRORS if "hub" in e[1].lower() or "SettingsHub" in e[1]]
        assert not PAGE_ERRORS, f"uncaught page errors: {PAGE_ERRORS[:5]}"
        assert not mine

    check("no uncaught page errors in any session", no_page_errors)

    browser.close()

failed = [n for n, ok in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
if failed:
    print("FAILED:")
    for n in failed:
        print("  -", n)
    sys.exit(1)
