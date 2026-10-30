"""Shared start-up for the P1c (till speed / bigger screen / currency) real-browser scripts.

Same recipe retail_p0_till_e2e.py proved: boot the REAL Flask backend in-process
on a fresh temp AURA_APP_DATA, sign up through the real form, seed an active
licence AFTER sign-up (before it, _isJoinedDevice() hangs the app on
"Connecting to your shop..."), then hand back a Playwright page.

Import this module FIRST in a script, before anything else touches the backend:
the AURA_* environment has to be set before `app` is imported. Run the scripts
with the SYSTEM Python (Playwright lives there), e.g.
    py -3.14 products/retail/tests/retail_p1c_keyboard_e2e.py

Never binds 5000 / 5001 / 5551 (the owner's app and the lab Owner use them);
only the 59xx range below, with the same "is it really free" probe as p0.
"""
import io
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO / "products" / "retail" / "backend"
for _p in (str(REPO), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRATCH = Path(os.environ.get("AURA_E2E_SCRATCH", r"C:\Users\MSI\.claude\jobs\255fb8da\tmp\till\e2e_scratch"))
SCRATCH.mkdir(parents=True, exist_ok=True)
SHOTS = Path(os.environ.get("AURA_E2E_SHOTS", r"C:\Users\MSI\.claude\jobs\255fb8da\tmp\till"))
SHOTS.mkdir(parents=True, exist_ok=True)

DATA = Path(tempfile.mkdtemp(prefix="aura_p1c_", dir=str(SCRATCH)))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

import app as _app_module  # noqa: E402

flask_app = _app_module.init_app()

import logging  # noqa: E402

logging.getLogger("werkzeug").setLevel(logging.ERROR)

from werkzeug.serving import make_server  # noqa: E402

RESERVED = {5000, 5001, 5551}


def port_is_free(port):
    if port in RESERVED:
        return False
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
for candidate in range(5930, 5950):
    if not port_is_free(candidate):
        continue
    try:
        server = make_server("127.0.0.1", candidate, flask_app, threaded=True)
        PORT = candidate
        break
    except OSError:
        continue
if server is None:
    print("FATAL: no free port 5930-5949")
    sys.exit(2)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(1.2)
BASE = f"http://127.0.0.1:{PORT}"
RETAIL_DB = DATA / "database" / "subsystems" / "retail.db"
print(f"Backend up at {BASE}  AURA_APP_DATA={DATA}", flush=True)

RESULTS = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        RESULTS.append((name, False))
        print(f"FAIL: {name}\n      {e}", flush=True)
        return
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False))
        print(f"ERROR: {name}\n      {type(e).__name__}: {e}", flush=True)
        return
    RESULTS.append((name, True))
    print(f"PASS: {name}", flush=True)


def finish():
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed", flush=True)
    for n in failed:
        print("  FAILED:", n)
    sys.exit(1 if failed else 0)


def db_one(sql, args=()):
    last = None
    for _ in range(5):
        try:
            conn = sqlite3.connect(str(RETAIL_DB), timeout=5)
            try:
                return conn.execute(sql, args).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError as e:  # noqa: PERF203
            last = e
            time.sleep(0.2)
    raise last


def dismiss_intro_overlay(page):
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=3000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:  # noqa: BLE001
        pass


def onboard(page, company="P1c Till Co", email="owner@p1c.local", password="P1cTillTest12345!"):
    page.wait_for_selector("#su-btn", timeout=20000)
    page.fill("#su-name", "Aura Owner")
    page.fill("#su-company", company)
    page.fill("#su-email", email)
    page.fill("#su-pass", password)
    page.fill("#su-pass2", password)
    page.click("#su-btn")
    page.wait_for_selector("#sub-header-section", timeout=30000)
    page.wait_for_timeout(1200)


def api(page, method, path, body=None):
    fn = getattr(page.request, method.lower())
    resp = fn(BASE + path, data=body) if body is not None else fn(BASE + path)
    try:
        j = resp.json()
    except Exception:  # noqa: BLE001
        j = {}
    return resp.status, j


def mutations_from_env():
    """P1C_MUTATE=<key> serves a deliberately broken copy of subsystem-retail.js
    to the browser (the file on disk is never touched, so parallel crews are
    unaffected). Each key breaks ONE guard the e2e claims to protect; the run
    must then go RED. See MUTANTS."""
    key = os.environ.get("P1C_MUTATE")
    return MUTANTS[key] if key else None


MUTANTS = {
    "print_refocus": ("try { window.focus(); this._refocusScan(true); } catch (e) { /* focus is a courtesy */ }", ""),
    "scan_closes_receipt": ("if (openReceipt) { this._closeModalOverlay(openReceipt); this._refocusScan(true); }", ""),
    "enter_on_button": ("if (typing || this._isActivatorTarget(el)) return;", "if (typing) return;"),
    "go_to_pay": ("else this._goToPay();", "else this._chargeNow();"),
    "star_strip": ("el.value = v.slice(0, v.length - buf.length);", ""),
    # size test: each breaks ONE number the Large targets pin
    "large_pay_height": ("html.ui-large button.pos-pay-btn { min-block-size:52px;font-size:16px; }", "html.ui-large button.pos-pay-btn { min-block-size:44px;font-size:16px; }"),
    "large_charge_height": ("html.ui-large .pos-checkout-btn { min-block-size:60px;font-size:20px; }", "html.ui-large .pos-checkout-btn { min-block-size:44px;font-size:20px; }"),
    "large_card_name": ("html.ui-large .pos-card-name { font-size:16px; }", "html.ui-large .pos-card-name { font-size:14px; }"),
    "large_body": ("html.ui-large body { font-size: 16px; }", "html.ui-large body { font-size: 14px; }", "**/main.css*"),
    "currency_no_resend": ("r = await R()._post('/api/sub/retail/settings/credit', { base_currency: code, confirm_existing_sales: true });", "r = r;", "**/settings-hub.js*"),
    "currency_cancel_applies": ("if (!yes) { sel.value = S.currency.code; say(''); return; }", "", "**/settings-hub.js*"),
    "large_short_compaction": ("html.ui-large .pos-sum-row { line-height:1.2; }", ""),
    "large_720_relax": ("@media (max-height: 740px) {", "@media (max-height: 1px) {"),
    "large_default": ("return 'large';", "return 'normal';", "**/"),
    # till user-reset e2e: each breaks ONE guard (app-shell.js, served in memory only)
    "till_reset_logout_removed": ("this._resetTillForNewUser('logout');", "", "**/app-shell.js*"),
    "till_shell_reset_noop": ("window.RetailSystem._resetTillForNewUser(reason);", "", "**/app-shell.js*"),
    "till_same_user_clears": ("const sameUser = !!now && !!keyOf(prev) && keyOf(now) === keyOf(prev);", "const sameUser = false;", "**/app-shell.js*"),
    # UI-3b (retail_ui3b_e2e.py): each breaks ONE guard, served in memory only
    "ui3b_overlay_inset": ("inset-block-start: var(--top-banner-inset, 0px);", "inset-block-start: 0px;", "**/main.css*"),
    "ui3b_rail_64": (".sub-sidebar.sub-sidebar-rail { inline-size: 96px; }", ".sub-sidebar.sub-sidebar-rail { inline-size: 64px; }", "**/main.css*"),
    "ui3b_ai_top": ("html .ai-panel, html .sub-ai-panel { position: fixed; top: var(--top-banner-inset, 0px); }", "html .ai-panel, html .sub-ai-panel { top: 0px; }", "**/main.css*"),
    "ui3b_help_cap": ("max-block-size:calc(100vh - var(--top-banner-inset, 0px) - 32px)", "max-block-size:calc(100vh - 32px)", "**/help.js*"),
    "ui3b_tabs_never": ("tabs.length < 2", "tabs.length < 99", "**/app-shell.js*"),
    "xlarge_cart_floor": ("html.ui-xlarge .pos-cart-items { min-block-size:calc(2 * var(--pos-cart-row-min) + 8px); }", "html.ui-xlarge .pos-cart-items { min-block-size:calc(5 * var(--pos-cart-row-min) + 8px); }"),
    # till-roomy (retail_till_roomy_e2e.py): each breaks ONE guard, served in memory only
    "tr_badges_hidden": ("          .pos-pane-hdr .pos-pay-kbd, .pos-held-btn .pos-pay-kbd, .pos-cust-sub .pos-pay-kbd { display:none; }", "          .pos-pay-kbd { display:none; }"),
    "tr_strip_wraps": ("          block-size:2em;overflow:hidden; }", "          overflow:visible; }"),
    "tr_arrow_flip": ("        /* NO scaleX(-1) in RTL (it used to be here).", "        html[dir=\"rtl\"] .pos-cat-more-glyph { transform:scaleX(-1); }\n        /* NO scaleX(-1) in RTL (it used to be here)."),
    "tr_currency_gap": ("sym + '\\u00A0\\u00A0' : sym + ' ';", "sym + ' ' : sym + ' ';"),
    "tr_header_full": ("html[data-screen=\"pos\"] .sub-header { padding-block: 8px; }", "", "**/main.css*"),
    "tr_notes_removed": ("if (n >= 5 && n <= 8) { e.preventDefault(); this._quickCashNote(", "if (false) { e.preventDefault(); this._quickCashNote("),
    "tr_decimal_dropped": ("&& this._qtyKeyBuffer.indexOf('.') === -1) digit = '.';", "&& this._qtyKeyBuffer.indexOf('.') === -1) digit = null;"),
    "tr_x_in_search": ("el && el.id === 'pos-tendered' && !el.value && this._cart.length) {\n      this._armExactCash();", "el && (el.id === 'pos-tendered' || el.id === 'pos-search') && !el.value && this._cart.length) {\n      this._armExactCash();"),
    "tr_reprint_removed": ("if (n === 10) { e.preventDefault(); this._reprintLastReceipt(); return true; }", ""),
    "tr_returns_removed": ("if (n === 12) { e.preventDefault(); this._openReturnsFromTill(); return true; }", ""),
    "tr_linedisc_removed": ("if (n === 11) { e.preventDefault(); this._lineDiscountKey(); return true; }", ""),
    "tr_change_covered_only": ("if (this._paymentMethod === 'cash' && tendered > 0) {\n      // TILL-ROOMY", "if (this._paymentMethod === 'cash' && tendered > 0 && tendered >= amountDue) {\n      // TILL-ROOMY"),
    "tr_void_beside_hold": (".pos-pane-void-slot { order:-1;margin-inline-end:8px; }", ".pos-pane-void-slot { margin-inline-end:8px; }"),
    "tr_delete_adjacent": ("<span style=\"margin-inline-start:28px\">", "<span style=\"margin-inline-start:0px\">"),
    "tr_unit_leak": ("if (!isAr) return this._UNIT_ALIASES[raw.trim()] ? code : raw;", "if (!isAr) return raw;"),
    "tr_plural_removed": ("if ((code === 'pc' || code === 'pcs') && Number.isInteger(count) && count >= 3 && count <= 10) return t('Unit: pcs few');", ""),
    "tr_chip_iso": ("    return sameDay ? `${m[4]}:${m[5]}` : `${m[3]}/${m[2]} ${m[4]}:${m[5]}`;", "    return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`;", "**/cash-drawer.js*"),
    "tr_end_shift_on_row": ("<div class=\"cd-menu\" id=\"cd-menu\" role=\"menu\" hidden>", "<div class=\"cd-menu\" id=\"cd-menu\" role=\"menu\">", "**/cash-drawer.js*"),
}


def start_shop(p, viewport=None, currency="JOD"):
    """Launch Chromium, sign up, seed the licence, claim admin device, set the
    currency the way p0 does. Returns (browser, ctx, page)."""
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport=viewport or {"width": 1366, "height": 768})
    mut = mutations_from_env()
    if mut:
        find, repl = mut[0], mut[1]
        glob = mut[2] if len(mut) > 2 else "**/subsystem-retail.js*"

        def _serve_mutant(route):
            resp = route.fetch()
            body = resp.text()
            assert body.count(find) == 1, f"mutation anchor occurs {body.count(find)} times"
            route.fulfill(response=resp, body=body.replace(find, repl))

        ctx.route(glob, _serve_mutant)
        print(f"MUTANT ACTIVE: {os.environ.get('P1C_MUTATE')}", flush=True)
    page = ctx.new_page()
    page.goto(BASE)
    dismiss_intro_overlay(page)
    onboard(page)
    seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
    page.reload()
    dismiss_intro_overlay(page)
    page.wait_for_selector("#sub-header-section", timeout=20000)
    page.wait_for_timeout(800)
    try:
        banner = page.locator("#aura-admin-device-claim")
        banner.wait_for(state="visible", timeout=4000)
        page.get_by_role("button", name="Make this the admin device", exact=True).click()
        banner.wait_for(state="detached", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    if currency:
        api(page, "POST", "/api/sub/retail/settings/credit", {"base_currency": currency})
    return browser, ctx, page


def seed_catalogue(page):
    """A few products with stock, one customer, an open cash shift. Returns ids."""
    _, cat = api(page, "POST", "/api/sub/retail/categories", {"name": "General"})
    cat_id = ((cat or {}).get("data") or {}).get("id")
    ids = {}

    def mk(key, name, sku, price, barcode=None):
        body = {"name": name, "sku": sku, "sell_price": price, "cost_price": round(price / 2, 3),
                "category_id": cat_id, "unit": "pc"}
        if barcode:
            body["barcode"] = barcode
        _, r = api(page, "POST", "/api/sub/retail/products", body)
        pid = ((r or {}).get("data") or {}).get("id")
        assert pid, f"product {name} not created: {r}"
        api(page, "POST", f"/api/sub/retail/products/{pid}/stock-adjust", {"quantity": 100, "reason": "p1c seed"})
        ids[key] = pid

    mk("widget", "Widget", "P1C-W1", 10, "4901234567894")
    mk("gadget", "Gadget", "P1C-G1", 5, "4901234567801")
    mk("gizmo", "Gizmo", "P1C-Z1", 7.5, "4901234567818")
    mk("doohickey", "Doohickey", "P1C-D1", 3, "4901234567825")
    mk("thingamajig", "Thingamajig Deluxe", "P1C-T1", 12, "4901234567832")
    _, c = api(page, "POST", "/api/sub/retail/customers", {"name": "Samira Haddad", "phone": "+962791112233"})
    ids["customer"] = ((c or {}).get("data") or {}).get("id")
    api(page, "POST", "/api/sub/retail/customers", {"name": "Omar Nasser", "phone": "+962795556677"})
    api(page, "POST", "/api/sub/retail/numbering", {"readable_enabled": False})
    api(page, "POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 50})
    return ids


def go(page, section, wait_ms=900):
    page.evaluate("(s) => SubsystemApp._navigate(s)", section)
    page.wait_for_timeout(wait_ms)


def go_pos(page):
    go(page, "pos")
    page.wait_for_selector("#pos-product-grid .pos-card", timeout=10000)
    page.wait_for_timeout(500)
