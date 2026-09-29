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
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(r"C:\Users\MSI\Desktop\aura-fullsuits\.claude\worktrees\retail-hardware-viewports")
BACKEND_DIR = REPO / "products" / "retail" / "backend"
for _p in (str(REPO), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRATCH = Path(r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p1b\H")
SCRATCH.mkdir(parents=True, exist_ok=True)
# New Arabic for this crew's strings; merged into the page dictionary because
# locales/*.json are edited only at merge time (see the report).
I18N_H = Path(r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p1b\i18n-H.json")

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
# crew H's own range (contract.md), minus 6000: Chromium refuses it as an
# "unsafe port" (X11) with net::ERR_UNSAFE_PORT.
for candidate in range(6001, 6020):
    if not port_is_free(candidate):
        continue
    try:
        server = make_server("127.0.0.1", candidate, flask_app, threaded=True)
        PORT = candidate
        break
    except OSError:
        continue
if server is None:
    print("FATAL: no free port 6000-6019")
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
_ONLY = [s for s in os.environ.get("H_ONLY", "").split("|") if s]
if _ONLY:
    print(f"FILTERED RUN (H_ONLY={_ONLY!r}) -- not a full verification")


def check(name, fn):
    if _ONLY and not any(s in name for s in _ONLY):
        return
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


def go(page, section, wait_ms=900):
    page.evaluate("(s) => SubsystemApp._navigate(s)", section)
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


I18N_ADD = json.loads(I18N_H.read_text(encoding="utf-8")) if I18N_H.exists() else {}


def merge_i18n(page):
    """The new Arabic is not in locales/ar.json until merge; add it to the live
    dictionary so the Arabic screens are judged with their real words."""
    page.evaluate("(add) => { Object.assign(AuraI18n.dicts.ar, add); }", I18N_ADD)


def set_lang(page, lang):
    page.evaluate("(l) => AuraI18n.setLang(l)", lang)
    page.wait_for_timeout(500)
    merge_i18n(page)


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
    merge_i18n(owner)

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
        merge_i18n(pg)
        return ctx, pg


    cashier_ctx, cashier = new_session(CASHIER_EMAIL, CASHIER_PW)
    manager_ctx, manager = new_session(MANAGER_EMAIL, MANAGER_PW)
    print("SEEDING DONE.")

    OWNER_SECTIONS = ["shop-receipt", "tax", "credit", "tills", "hardware", "team", "data-backup", "plan-devices"]

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
            "hardware": "#printer-card",
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

    check("hub: owner reaches the hub from the gear and every one of the 8 sections by real click",
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
        merge_i18n(owner)
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
        page.evaluate("async (l) => { await RetailSystem._printReceipt(SettingsHub.sampleSale(l)); }", lang)
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
        assert ids == ["tills", "team"], f"a delegated manager sees sections {ids}"
        secs = manager.evaluate("() => SettingsHub.sections().filter(s => s.visible).map(s => s.id)")
        assert secs == ["tills", "team"], secs
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

    check("gating: a delegated manager sees ONLY Tills and Team (and the server still refuses the rest)",
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
    # 7. Numbering: branch code + readable numbers
    # =========================================================================
    def numbering_card():
        # crew B's /numbering may not be merged into this tree yet. If the real
        # route answers, test against it; otherwise test against the DOCUMENTED
        # shape (numbering-shapes.md) with a network stub, and say so.
        st, real = api(owner, "GET", "/api/sub/retail/numbering")
        stubbed = not (st == 200 and (real or {}).get("status") == "success")
        # The real flag's default depends on the shop's history (brand-new shop =
        # ON, shop with sales = OFF, retail_api.py `_numbering_readable_enabled`),
        # so the starting value is READ, never assumed: the test flips it to the
        # OPPOSITE and back, which is a real change whichever way it began.
        start_enabled = bool((real or {}).get("data", {}).get("readable_enabled")) if not stubbed else False
        state = {"branch_code": "ABD", "readable_enabled": start_enabled, "posts": []}

        def body():
            code = state["branch_code"]
            return {"status": "success", "data": {
                "branch_uid": "stub-branch", "branch_code": code, "default_branch_code": "ABD",
                "branch_code_is_saved": True, "readable_enabled": state["readable_enabled"],
                # same shape as the real doc_series.preview(): <branch code><till no>-NNNNNN
                "preview": {"sale": f"{code}1-000001", "return": f"{code}1-R-000001"}}}

        # The route is ALWAYS intercepted so the POST bodies are counted, real
        # backend or not: "sent exactly what changed" is a claim about what left
        # the browser, and only a spy on the wire can see that. With the real
        # backend the request is passed straight through (continue_).
        def handler(route, request):
            if request.method == "POST":
                payload = json.loads(request.post_data or "{}")
                state["posts"].append(payload)
                if stubbed:
                    if "branch_code" in payload:
                        state["branch_code"] = payload["branch_code"].upper()
                    if "readable_enabled" in payload:
                        state["readable_enabled"] = bool(payload["readable_enabled"])
            if stubbed:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body()))
            else:
                route.continue_()
        owner.route("**/api/sub/retail/numbering*", handler)
        try:
            open_hub(owner, "tills")
            print(f"      numbering backend: {'STUB of the documented shape (real route absent)' if stubbed else 'REAL route'}")
            reachable(owner, owner.locator("#num-branch-code"), "branch code field")
            reachable(owner, owner.locator("#num-readable"), "readable numbers checkbox")
            assert not savebar_up(owner)
            set_input(owner, "#num-branch-code", "abd1")
            assert owner.input_value("#num-branch-code") == "ABD1", "branch code was not upper-cased as typed"
            prev = owner.inner_text("#num-preview")
            # the real preview is <code><till no>-000001: code ABD1 + till 1
            assert "ABD11-000001" in prev and "ABD11-R-000001" in prev, f"live example did not follow the code: {prev!r}"
            # bad code: refused client side, bar stays
            set_input(owner, "#num-branch-code", "A")
            assert "2 to 4" in owner.inner_text("#num-preview")
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_timeout(500)
            assert savebar_up(owner), "a too-short code was accepted"
            assert not state["posts"], f"a bad code reached the server: {state['posts']}"
            set_input(owner, "#num-branch-code", "abd1")
            assert owner.is_checked("#num-readable") == start_enabled, "checkbox does not show the stored flag"
            (owner.uncheck if start_enabled else owner.check)("#num-readable")
            owner.dispatch_event("#num-readable", "change")
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
            assert state["posts"] == [{"branch_code": "ABD1", "readable_enabled": not start_enabled}], state["posts"]
            if not stubbed:
                _, now = api(owner, "GET", "/api/sub/retail/numbering")
                assert now["data"]["branch_code"] == "ABD1" and now["data"]["readable_enabled"] is (not start_enabled), now
                # what the card drew before Save is what the server now says it will print
                assert now["data"]["preview"] == {"sale": "ABD11-000001", "return": "ABD11-R-000001"}, now
            # only the changed field is sent next time
            state["posts"].clear()
            (owner.check if start_enabled else owner.uncheck)("#num-readable")
            owner.dispatch_event("#num-readable", "change")
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
            assert state["posts"] == [{"readable_enabled": start_enabled}], state["posts"]
            # ...and the other way round: change ONLY the code, and the flag must
            # not ride along (mutation `numbering_readable_always_sent` stayed green
            # until this step existed).
            state["posts"].clear()
            set_input(owner, "#num-branch-code", "abd2")
            real_click(owner, "#sh-save", "Save")
            owner.wait_for_function("() => { const b = document.getElementById('sh-savebar'); return b && b.hidden; }", timeout=8000)
            assert state["posts"] == [{"branch_code": "ABD2"}], state["posts"]
        finally:
            owner.unroute("**/api/sub/retail/numbering*")

    check("numbering: branch code (2-4, upper-cased, live example) and readable-numbers opt-in send exactly what changed",
          numbering_card)

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

    # =========================================================================
    # 8. Looking at it: EN/AR x sand/night x every section, no overflow
    # =========================================================================
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
