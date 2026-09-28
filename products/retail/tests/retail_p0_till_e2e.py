"""Phase 0 crew C3a (till-1) -- live-browser verification for items #1, #3,
#5, #6, #16 (see .claude/artifacts/aura-review/p0/items.md and contracts.md).

Boots the real Flask backend in-process on a fresh temp AURA_APP_DATA (the
proven pattern from scripts/ui-sweep/retail_ui_sweep.py and
.claude/artifacts/aura-review/evidence/capture.py), completes first-run
onboarding through the real signup form, seeds an active licence AFTER
signup (seeding before it makes _isJoinedDevice() true and the app hangs on
"Connecting to your shop..."), then drives a real headless Chromium via
Playwright against the ACTUAL running app -- never against a DOM stub.

This is a standalone script, not a pytest *_test.py file (contracts.md's
file-ownership table names it that way on purpose): it prints PASS/FAIL per
check and exits 1 if anything failed, the same convention item #0 asks the
UI-sweep harness to follow.

Read-only w.r.t. the repo: writes only to its own scratch AURA_APP_DATA
under this crew's job-tmp directory.

Run with the SYSTEM Python (Playwright lives there), from anywhere:
    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_p0_till_e2e.py
"""
import io
import os
import re
import sqlite3
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

# Round 2 (contracts.md RESUME NOTE): a fresh AURA_APP_DATA under the
# round-2 "till" crew's own scratch dir, never the round-1 C3a one -- other
# round-2 crews run AT THE SAME TIME in this same worktree.
SCRATCH = Path(r"C:\Users\MSI\.claude\jobs\c5051313\tmp\p0\r2\till")
SCRATCH.mkdir(parents=True, exist_ok=True)

DATA = Path(tempfile.mkdtemp(prefix="aura_p0_r2_till_", dir=str(SCRATCH)))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

# Seed AFTER signup, not before -- see module docstring and capture.py's own
# identical comment on this exact trap.
from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Crew review fix (2026-09-27): item #8's money-identity check used to read
# a hardcoded `0.0005` inline -- the exact "second literal" ENGINEERING.md's
# money-gate rule forbids, because a hardcoded threshold can drift from the
# real one silently. Importing the SAME `_money_epsilon` retail_api.py's own
# gates call (see that module's docstring) means this test's tolerance is
# BY CONSTRUCTION the real per-currency half-minor-unit, never a second
# number someone has to remember to keep in sync.
from api.retail_api import _money_epsilon  # noqa: E402

import app as _app_module  # noqa: E402
flask_app = _app_module.init_app()

import logging  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from werkzeug.serving import make_server  # noqa: E402

server = None
PORT = None
for candidate in range(5900, 5910):  # round-3 "till" crew's own port range (computed task)
    try:
        server = make_server("127.0.0.1", candidate, flask_app, threaded=True)
        PORT = candidate
        break
    except OSError:
        continue
if server is None:
    print("FATAL: no free port 5900-5909")
    sys.exit(2)

thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
time.sleep(1.2)
BASE = f"http://127.0.0.1:{PORT}"
print(f"Backend up at {BASE}  AURA_APP_DATA={DATA}")

RETAIL_DB = DATA / "database" / "subsystems" / "retail.db"

# ---------------------------------------------------------------------------
# Harness plumbing
# ---------------------------------------------------------------------------
RESULTS = []  # [(name, passed)]


# Crew C3c addition: an OPT-IN, backward-compatible filter for fast mutation
# proofs. Every scenario's SETUP (seeding products/customers/etc.) runs as
# plain top-level code outside check() and is UNCHANGED by this -- only the
# scenario itself is skipped, never counted as pass or fail, so a filtered
# run's RESULTS/exit code says nothing about the skipped checks. Default
# (P0_ONLY unset) runs every scenario exactly as before this addition; no
# existing crew's behaviour changes.
_P0_ONLY = os.environ.get("P0_ONLY")


def check(name, fn):
    if _P0_ONLY and _P0_ONLY not in name:
        print(f"SKIP (P0_ONLY={_P0_ONLY!r}): {name}")
        return
    try:
        fn()
    except AssertionError as e:
        RESULTS.append((name, False))
        print(f"FAIL: {name}\n      {e}")
        return
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False))
        print(f"ERROR: {name}\n      {type(e).__name__}: {e}")
        return
    RESULTS.append((name, True))
    print(f"PASS: {name}")


def count_sales():
    """Reads sales.COUNT(*) straight from retail.db -- the item's own
    instruction ("count SALES CREATED, not screens shown"), not a UI proxy
    for it. A short retry absorbs the rare case of reading mid-write against
    the live werkzeug thread."""
    last_err = None
    for _ in range(5):
        try:
            conn = sqlite3.connect(str(RETAIL_DB), timeout=5)
            try:
                return conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.OperationalError as e:  # noqa: PERF203
            last_err = e
            time.sleep(0.2)
    raise last_err


def dismiss_intro_overlay(page):
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=3000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:  # noqa: BLE001
        pass


def onboard(page):
    page.wait_for_selector("#su-btn", timeout=20000)
    page.fill("#su-name", "Aura Owner")
    page.fill("#su-company", "P0 Till Test Co")
    page.fill("#su-email", "owner@p0till.local")
    page.fill("#su-pass", "P0TillTest12345!")
    page.fill("#su-pass2", "P0TillTest12345!")
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


def go(page, section, wait_ms=900):
    page.evaluate("(s) => SubsystemApp._navigate(s)", section)
    page.wait_for_timeout(wait_ms)


def go_pos(page):
    go(page, "pos")
    page.wait_for_selector("#pos-product-grid", timeout=10000)
    page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# Playwright
# ---------------------------------------------------------------------------
from playwright.sync_api import sync_playwright  # noqa: E402

XSS_NAME = 'Laptop 15" <b>Deal</b>'

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    page.goto(BASE)
    dismiss_intro_overlay(page)
    onboard(page)

    seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
    page.reload()
    dismiss_intro_overlay(page)
    page.wait_for_selector("#sub-header-section", timeout=20000)
    page.wait_for_timeout(800)

    # The signup computer's red admin-device claim bar (item #20, C-crew not
    # this one) blocks nothing this script needs, but clear it anyway so it
    # never intercepts a click meant for something underneath it.
    try:
        banner = page.locator("#aura-admin-device-claim")
        banner.wait_for(state="visible", timeout=4000)
        page.get_by_role("button", name="Make this the admin device", exact=True).click()
        banner.wait_for(state="detached", timeout=8000)
    except Exception:  # noqa: BLE001
        pass

    # =========================================================================
    # SEEDING via the real HTTP API (same session cookies as the page)
    # =========================================================================
    api(page, "POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})
    # Single source of truth for this file's currency: every money-gate
    # tolerance below reads `MONEY_EPS`, derived from THIS literal via the
    # real `_money_epsilon`, rather than a second hand-typed "JOD" or a
    # hand-typed "0.0005" living somewhere else in this file able to drift
    # from what the shop is actually configured to.
    CURRENCY = "JOD"
    MONEY_EPS = _money_epsilon(CURRENCY)
    _, cat_body = api(page, "POST", "/api/sub/retail/categories", {"name": "General"})
    cat_id = ((cat_body or {}).get("data") or {}).get("id")

    def create_product(name, sku, price):
        _, body = api(page, "POST", "/api/sub/retail/products", {
            "name": name, "sku": sku, "sell_price": price, "cost_price": round(price / 2, 3),
            "category_id": cat_id, "unit": "pc",
        })
        pid = ((body or {}).get("data") or {}).get("id")
        assert pid, f"product '{name}' was not created: {body}"
        api(page, "POST", f"/api/sub/retail/products/{pid}/stock-adjust",
            {"quantity": 100, "reason": "p0 till e2e seed"})
        return pid

    PID_1 = create_product("Widget", "P0-W1", 10)
    PID_2 = create_product("Gadget", "P0-G1", 5)
    PID_3 = create_product("Gizmo", "P0-Z1", 7.5)
    PID_4 = create_product("Doohickey", "P0-D1", 3)
    PID_XSS = create_product(XSS_NAME, "P0-X1", 20)

    _, cust_body = api(page, "POST", "/api/sub/retail/customers",
                        {"name": "Test Customer X", "phone": "+962700000000"})
    CUST_ID = ((cust_body or {}).get("data") or {}).get("id")
    assert CUST_ID, f"customer was not created: {cust_body}"
    api(page, "PATCH", f"/api/sub/retail/customers/{CUST_ID}", {"credit_mode": "unlimited"})

    # Round 2, till R2 (items #7(c)/#8 "totals invisible without scroll" +
    # "Charge clipped with customers"): two MORE customers, seeded here so
    # #pos-customer carries a realistic list (grows toward its own 190px
    # max-inline-size) for every scenario that runs after this, including
    # item7_layout_matrix below -- the round-2 T1 crew measured its
    # regression with an empty customer list and never saw the header wrap
    # a long name forces.
    api(page, "POST", "/api/sub/retail/customers", {"name": "Ahmad Al-Khatib"})
    api(page, "POST", "/api/sub/retail/customers",
        {"name": "Mrs. Samira Abdel-Rahman Al-Husseini"})

    # Round 2, till R2: a DEDICATED product + active promotion, with a
    # realistic (long) promotion name, seeded here -- before item7_layout_
    # matrix runs -- purely for that loop's own extra reachability check
    # below. A dedicated product (never touched by PID_1-4's existing
    # scenarios elsewhere in this file) rather than attaching a promotion to
    # one of PID_1-4 directly, because several OTHER scenarios already use
    # PID_1-4 and assert exact totals against them -- a shared, always-on
    # promotion would silently change those numbers too. The long name
    # doubles as the #7(b) promo-tag ellipsis fixture (subsystem-retail.js's
    # own .pos-promo-tag comment).
    PID_R2PROMO = create_product("Home & Kitchen Ceramic Knife Set", "P0-R2PROMO", 12)
    _, r2_promo_body = api(page, "POST", "/api/sub/retail/promotions", {
        "name": "Weekend Clearance — 20% off Home & Kitchen", "product_id": PID_R2PROMO, "discount_pct": 20,
    })
    assert r2_promo_body.get("status") == "success", f"R2 layout-matrix promotion was not created: {r2_promo_body}"

    api(page, "POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 50})

    go_pos(page)
    print("SEEDING DONE.")

    # =========================================================================
    # ITEM #1 -- discount/tender/customer/method reset after checkout
    # =========================================================================
    def item1_reset_sale_controls():
        go_pos(page)
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)  # unit price 10
        page.wait_for_timeout(400)
        page.fill("#pos-disc", "10")
        page.select_option("#pos-customer", CUST_ID)
        page.wait_for_timeout(300)
        page.click('.pos-pay-btn[data-method="credit"]')
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
        page.wait_for_timeout(300)

        disc_val = page.eval_on_selector("#pos-disc", "el => el.value")
        assert disc_val in ("0", ""), f"discount % was not reset to 0, got {disc_val!r}"

        tendered_val = page.eval_on_selector("#pos-tendered", "el => el.value")
        assert tendered_val == "", f"Cash Tendered was not cleared, got {tendered_val!r}"

        change_display = page.eval_on_selector("#pos-change-row", "el => getComputedStyle(el).display")
        assert change_display == "none", f"Change Due row is still shown (display={change_display!r})"

        cust_val = page.eval_on_selector("#pos-customer", "el => el.value")
        assert cust_val == "", f"selected customer was not cleared, got {cust_val!r}"

        active_methods = page.eval_on_selector_all(".pos-pay-btn.active", "els => els.map(e => e.dataset.method)")
        assert active_methods == ["cash"], f"payment method was not switched back to cash, got {active_methods}"

        # Allow-half, in the SAME assertion the item text asks for: a single
        # new item's total is exactly its own unit price -- no 10% discount,
        # no stale customer/credit tender bleeding into the next sale.
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)  # unit price 5
        page.wait_for_timeout(400)
        total = page.evaluate("() => RetailSystem._currentTotals.total")
        assert abs(total - 5) < 1e-6, f"next sale's total was {total}, expected exactly 5 (unit price, no leftover discount)"
        page.evaluate("() => RetailSystem._clearCart()")  # tidy up for the next scenario
        page.wait_for_timeout(200)

    check("item #1: discount/tender/customer/method all reset after checkout, next sale totals correctly", item1_reset_sale_controls)

    # =========================================================================
    # ITEM #3 -- Enter behind a dialog never charges; Enter on an idle cart does
    # =========================================================================
    def item3_enter_behind_dialog():
        go_pos(page)
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_3)
        page.wait_for_timeout(400)
        before = count_sales()

        page.click("#pos-held-btn")
        page.wait_for_selector("#ret-held-dialog", timeout=8000)
        page.wait_for_timeout(300)  # let _wireModalA11y's dialogEl.focus() settle
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        after_behind_dialog = count_sales()
        assert after_behind_dialog == before, (
            f"Enter behind the Held Sales dialog created a sale (deny-half failed): "
            f"{before} -> {after_behind_dialog}")

        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-held-dialog", state="detached", timeout=5000)
        # Defocus deliberately -- see this scenario's own note on why: focus
        # returns to the #pos-held-btn TRIGGER after Escape (_wireModalA11y's
        # own contract), and Enter on a focused <button> is ALSO the
        # browser's native "activate" key, not just this file's shortcut.
        # _onPOSShortcut's own preventDefault() already stops that, but
        # blurring first removes the ambiguity from this proof entirely.
        page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
        page.wait_for_timeout(200)

        page.keyboard.press("Enter")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        after_allowed = count_sales()
        assert after_allowed == before + 1, (
            f"Enter on an idle cart with items did not create exactly 1 sale (allow-half failed): "
            f"{before} -> {after_allowed}")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

    check("item #3: Enter behind an open dialog creates 0 sales; Enter on an idle cart creates 1", item3_enter_behind_dialog)

    # =========================================================================
    # ITEM #5 -- a product name with a quote and a <b> tag survives editing
    # =========================================================================
    def item5_product_name_quote_and_tag():
        page.evaluate("(id) => RetailSystem._openEditProduct(id)", PID_XSS)
        page.wait_for_selector("#ret-prod-modal", timeout=8000)
        page.wait_for_timeout(300)

        name_val = page.eval_on_selector("#pm-name", "el => el.value")
        assert name_val == XSS_NAME, (
            f"the edit field truncated/altered the name at its quote mark. "
            f"Expected {XSS_NAME!r}, got {name_val!r}")

        page.click("#pm-save-btn")
        page.wait_for_selector("#ret-prod-modal", state="detached", timeout=8000)
        page.wait_for_timeout(300)

        # Round-trip: reopen and confirm Save did not alter the stored name.
        page.evaluate("(id) => RetailSystem._openEditProduct(id)", PID_XSS)
        page.wait_for_selector("#ret-prod-modal", timeout=8000)
        page.wait_for_timeout(300)
        name_val_after_save = page.eval_on_selector("#pm-name", "el => el.value")
        assert name_val_after_save == XSS_NAME, (
            f"Save altered the stored name. Expected {XSS_NAME!r}, got {name_val_after_save!r}")
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-prod-modal", state="detached", timeout=5000)

        # Sale-detail cell: the literal text, no <b> element actually created.
        go_pos(page)
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_XSS)
        page.wait_for_timeout(400)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        sale_id = page.evaluate("() => RetailSystem._lastSaleData.id")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        go(page, "sales")
        page.wait_for_selector("#sh-table", timeout=10000)
        page.evaluate("(id) => RetailSystem._viewSale(id)", sale_id)
        page.wait_for_selector("#ret-sale-dialog", timeout=8000)
        page.wait_for_timeout(300)
        cell_text = page.eval_on_selector("#ret-sale-dialog table.ret-table tbody tr td", "el => el.textContent")
        has_b_element = page.eval_on_selector("#ret-sale-dialog table.ret-table tbody tr td", "el => !!el.querySelector('b')")
        assert cell_text == XSS_NAME, f"sale-detail cell text was {cell_text!r}, expected the literal {XSS_NAME!r}"
        assert not has_b_element, "sale-detail cell rendered a real <b> element -- the name's markup executed as HTML"
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-sale-dialog", state="detached", timeout=5000)

    check("item #5: a product name with a quote and a <b> tag survives editing/Save and renders literally in sale detail", item5_product_name_quote_and_tag)

    # =========================================================================
    # ROUND 2, T4 -- XSS residues the security review found: product modal
    # variant_label, PO items table product_name, return picker
    # product_name, sale detail notes -- all through this._esc(). item #5
    # above already proves the PRODUCT NAME half of this pattern; this proves
    # the four surfaces the review named separately, plus PO notes (found in
    # the same sweep, same pattern, not separately named by the review but
    # fixed alongside it -- see subsystem-retail.js's own comment on that
    # line). XSS_PAYLOAD carries BOTH a double-quote (breaks an unescaped
    # HTML attribute) and a real <img onerror> tag (executes if the string
    # ever reaches the DOM unescaped) in one string, so one fixture proves
    # both failure shapes item #5's own REFS describe ("cut at the quote"
    # and "inject attributes/script").
    # =========================================================================
    XSS_PAYLOAD = '<img src=x onerror="window.__t4XssFired=true">Evil&"Corp'

    def item_t4_xss_residues():
        page.evaluate("() => { window.__t4XssFired = false; }")

        def assert_not_fired(where):
            fired = page.evaluate("() => window.__t4XssFired")
            assert not fired, f"{where}: the onerror payload EXECUTED -- window.__t4XssFired was set"

        def assert_no_img(scope_selector, where):
            has_img = page.eval_on_selector(scope_selector, "el => !!el.querySelector('img[src=\"x\"]')")
            assert not has_img, f"{where}: a real <img src=x> element was created -- the payload was not escaped"

        # (a) Product modal variant_label. create_product accepts
        # variant_label directly (retail_api.py ~2036) -- no edit round trip
        # needed to set it, only to prove the FIELD renders it safely.
        _, vp_body = api(page, "POST", "/api/sub/retail/products", {
            "name": "T4 Variant Host", "sku": "T4-VARIANT", "sell_price": 9,
            "cost_price": 4, "category_id": None, "unit": "pc",
            "variant_label": XSS_PAYLOAD,
        })
        vp_pid = ((vp_body or {}).get("data") or {}).get("id")
        assert vp_pid, f"T4 variant-label product was not created: {vp_body}"

        # _openEditProduct reads from RetailSystem._products (the client's
        # own loaded batch, see that method's own `.find(...)` guard) -- a
        # product created straight through the API is not in it until a
        # screen that loads products (POS, here) mounts again.
        go_pos(page)
        page.evaluate("(id) => RetailSystem._openEditProduct(id)", vp_pid)
        page.wait_for_selector("#ret-prod-modal", timeout=8000)
        page.wait_for_timeout(300)
        variant_val = page.eval_on_selector("#pm-variant-label", "el => el.value")
        assert variant_val == XSS_PAYLOAD, (
            f"(a) product modal variant_label did not round-trip exactly: got {variant_val!r}, "
            f"expected {XSS_PAYLOAD!r}")
        assert_no_img("#ret-prod-modal", "(a) product modal variant_label")
        assert_not_fired("(a) product modal variant_label")
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-prod-modal", state="detached", timeout=5000)

        # (b)/(c) PO items table + return picker product_name, and the PO
        # notes bonus fix -- all three read the SAME product's catalogue
        # name, so one hostile product covers (b) and, via a completed
        # sale of it, (c) too.
        _, np_body = api(page, "POST", "/api/sub/retail/products", {
            "name": XSS_PAYLOAD, "sku": "T4-NAME", "sell_price": 12,
            "cost_price": 6, "category_id": None, "unit": "pc",
        })
        np_pid = ((np_body or {}).get("data") or {}).get("id")
        assert np_pid, f"T4 hostile-name product was not created: {np_body}"
        api(page, "POST", f"/api/sub/retail/products/{np_pid}/stock-adjust",
            {"quantity": 100, "reason": "T4 xss seed"})

        # Bonus (found alongside T4, same file/pattern): supplier_name was
        # the one field on this PO row left unescaped while product_name
        # right next to it already went through _esc() -- reusing this
        # scenario's own PO rather than minting a second one.
        _, sup_body = api(page, "POST", "/api/sub/retail/suppliers", {"name": XSS_PAYLOAD})
        sup_id = ((sup_body or {}).get("data") or {}).get("id")
        assert sup_id, f"T4 supplier was not created: {sup_body}"
        _, po_body = api(page, "POST", "/api/sub/retail/purchase-orders", {
            "supplier_id": sup_id, "notes": XSS_PAYLOAD,
            "items": [{"product_id": np_pid, "quantity": 5, "unit_cost": 6}],
        })
        assert po_body.get("status") == "success", f"T4 PO was not created: {po_body}"
        po_id = ((po_body or {}).get("data") or {}).get("id")
        assert po_id, f"T4 PO id missing: {po_body}"

        page.evaluate("(id) => RetailSystem._viewPO(id)", po_id)
        page.wait_for_selector("#ret-po-view-dialog", timeout=8000)
        page.wait_for_timeout(300)
        po_row_text = page.eval_on_selector(
            "#ret-po-view-dialog table.ret-table tbody tr td", "el => el.textContent")
        assert po_row_text == XSS_PAYLOAD, (
            f"(b) PO items table product_name did not round-trip: got {po_row_text!r}, "
            f"expected {XSS_PAYLOAD!r}")
        po_notes_text = page.eval_on_selector(
            "#ret-po-view-dialog p", "el => el.textContent")
        assert XSS_PAYLOAD in po_notes_text, (
            f"PO notes did not round-trip: got {po_notes_text!r}, expected to contain {XSS_PAYLOAD!r}")
        po_dialog_text = page.eval_on_selector("#ret-po-view-dialog", "el => el.textContent")
        assert XSS_PAYLOAD in po_dialog_text, (
            f"PO view supplier_name did not round-trip: dialog text does not contain {XSS_PAYLOAD!r}")
        assert_no_img("#ret-po-view-dialog", "(b)/PO-notes/supplier PO view")
        assert_not_fired("(b)/PO-notes PO view")
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-po-view-dialog", state="detached", timeout=5000)

        # Round 2, till R2 (T4 bonus finding, "guard vacuous"): the check
        # above only ever opened _viewPO's MODAL -- it never rendered the
        # Purchase Orders LIST screen at all, so the supplier_name escaping
        # this same PO's LIST ROW does (subsystem-retail.js's own
        # `this._esc(po.supplier_name||'-')`, already correct in source) had
        # no guard proving it. This is that guard: navigate to the actual
        # list, find THIS po's row by its onclick target (robust regardless
        # of sort order or short-doc-number formatting), and prove the
        # hostile string round-trips with no injected <img> and no fired
        # handler on the LIST, not just the detail view.
        page.evaluate("() => { window.__t4XssFired = false; }")
        go(page, "purchases")
        page.wait_for_selector("#po-table", timeout=10000)
        page.wait_for_timeout(400)
        po_list_row_html = page.eval_on_selector(
            f'#po-table tbody tr:has(button[onclick*="_viewPO({po_id})"])', "el => el.innerHTML")
        # html.unescape (not a hand-rolled replace chain): the escaped
        # payload carries &lt;/&gt;/&amp;/&quot; all at once, and a partial
        # replace chain here would just be a second, weaker copy of the same
        # escaping logic this check exists to verify.
        import html as _html
        assert XSS_PAYLOAD in _html.unescape(po_list_row_html), (
            f"(bonus) PO LIST row supplier_name did not round-trip. Row HTML: {po_list_row_html!r}")
        assert_no_img("#po-table", "(bonus) PO LIST row supplier_name")
        assert_not_fired("(bonus) PO LIST row supplier_name")
        go_pos(page)

        # (c) return picker: sell the hostile product, complete the sale
        # (which also seeds (d)'s notes below via the SAME sale), then look
        # it up for a return and check the items table's product_name cell.
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", np_pid)
        page.wait_for_timeout(300)
        page.select_option("#pos-customer", CUST_ID)
        page.click('.pos-pay-btn[data-method="credit"]')
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        t4_sale_number = page.evaluate("() => RetailSystem._lastSaleData.sale_number")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        page.evaluate("() => RetailSystem._openCreateReturn()")
        page.wait_for_selector("#ret-return-dialog", timeout=8000)
        page.fill("#ret-sale-search", t4_sale_number)
        page.click('button:has-text("Lookup")')
        page.wait_for_timeout(500)
        return_row_text = page.eval_on_selector(
            "#ret-sale-items table tbody tr td:nth-child(2)", "el => el.textContent")
        assert return_row_text == XSS_PAYLOAD, (
            f"(c) return picker product_name did not round-trip: got {return_row_text!r}, "
            f"expected {XSS_PAYLOAD!r}")
        assert_no_img("#ret-sale-items", "(c) return picker")
        assert_not_fired("(c) return picker")
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-return-dialog", state="detached", timeout=5000)

        # (d) sale detail notes -- a SEPARATE sale carrying notes directly
        # (create_sale accepts `notes`, retail_api.py ~7088), since the
        # cart-driven checkout above has no UI field for it.
        _, notes_sale_body = api(page, "POST", "/api/sub/retail/sales", {
            "items": [{"product_id": PID_1, "quantity": 1, "unit_price": 10, "line_total": 10}],
            "payment_method": "cash", "amount_paid": 10, "notes": XSS_PAYLOAD,
        })
        assert notes_sale_body.get("status") == "success", f"T4 notes sale was not created: {notes_sale_body}"
        notes_sale_id = ((notes_sale_body or {}).get("data") or {}).get("id")
        assert notes_sale_id, f"T4 notes sale id missing: {notes_sale_body}"

        go(page, "sales")
        page.wait_for_selector("#sh-table", timeout=10000)
        page.evaluate("(id) => RetailSystem._viewSale(id)", notes_sale_id)
        page.wait_for_selector("#ret-sale-dialog", timeout=8000)
        page.wait_for_timeout(300)
        notes_text = page.eval_on_selector(
            "#ret-sale-dialog p:has-text('Notes')", "el => el.textContent")
        assert XSS_PAYLOAD in notes_text, (
            f"(d) sale detail notes did not round-trip: got {notes_text!r}, expected to contain "
            f"{XSS_PAYLOAD!r}")
        assert_no_img("#ret-sale-dialog", "(d) sale detail notes")
        assert_not_fired("(d) sale detail notes")
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-sale-dialog", state="detached", timeout=5000)

    check("T4: XSS residues (product modal variant_label, PO items table product_name, PO notes, return "
          "picker product_name, sale detail notes) all round-trip the exact hostile string with no injected "
          "<img> element and no onerror execution", item_t4_xss_residues)

    # =========================================================================
    # ITEM #6 -- receipts show real product names, never a bare id; nowrap
    # =========================================================================
    UUID_PATTERN = re.compile(r"#[0-9a-f]{8}-[0-9a-f]{4}-")

    def item6_receipt_shows_names():
        go_pos(page)
        for pid in (PID_1, PID_2, PID_3, PID_4):
            page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
            page.wait_for_timeout(150)
        page.wait_for_timeout(300)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.wait_for_timeout(300)

        receipt_html = page.eval_on_selector("#ret-receipt-dialog", "el => el.innerHTML")
        assert not UUID_PATTERN.search(receipt_html), (
            f"the on-screen receipt shows a bare product id (uuid pattern found). HTML: {receipt_html[:2000]}")
        for name in ("Widget", "Gadget", "Gizmo", "Doohickey"):
            assert name in receipt_html, f"the on-screen receipt is missing the line for '{name}'"

        # Amount spans must not wrap.
        nowrap_ok = page.eval_on_selector_all(
            "#ret-receipt-dialog div[style*='justify-content:space-between'] span:last-child",
            "els => els.every(e => getComputedStyle(e).whiteSpace === 'nowrap')")
        assert nowrap_ok, "not every receipt amount span is white-space:nowrap"

        page.click('#ret-receipt-dialog button:has-text("Print")')
        page.wait_for_timeout(500)
        print_text = page.evaluate(
            "() => { const f = document.getElementById('ret-print-frame'); "
            "return f && f.contentDocument ? f.contentDocument.body.innerText : ''; }")
        assert print_text, "the print frame has no content -- _printReceipt did not run"
        assert not UUID_PATTERN.search(print_text), f"the printed receipt shows a bare product id. Text: {print_text[:1000]}"
        for name in ("Widget", "Gadget", "Gizmo", "Doohickey"):
            assert name in print_text, f"the printed receipt is missing the line for '{name}'"

        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

    check("item #6: the receipt (screen + print) shows all 4 real product names, never a bare id; amounts nowrap", item6_receipt_shows_names)

    # -------------------------------------------------------------------------
    # ITEM #6b -- the CLIENT fallback chain (cartNames, then t('Item')), tested
    # in isolation from the server. C2's backend half of this item (retail_api.py
    # line ~6718, `'name': product['name']`) landed in this same worktree while
    # this crew was working, so a sale rung through the real checkout flow now
    # always carries a server name and can never exercise link 2 or link 3 of
    # I-9's own fallback chain ("server name, then the cart's own names captured
    # before clearing, then t('Item'), never the id"). Calling _showReceipt()
    # directly with a synthetic saleData -- never through checkout, no sale ever
    # written -- is what actually drives those two links, independent of
    # whatever the server happens to send on any given day.
    # =========================================================================
    def item6b_client_fallback_chain_in_isolation():
        result = page.evaluate("""
            () => {
                const saleData = {
                    id: 999999, sale_number: 'SALE-999999-aaaaaaaa-bbbbbbbb',
                    subtotal: 15, tax_amount: 0, discount_amount: 0, total: 15,
                    amount_paid: 15, change: 0,
                    // Line A: no server name, but cartNames HAS this product_id --
                    // must render the CART name, link 2.
                    // Line B: no server name AND cartNames has nothing for it --
                    // must render t('Item'), link 3 -- and NEVER '#'+product_id.
                    lines: [
                        { product_id: 'aaaa-no-server-name', quantity: 1, line_total: 10 },
                        { product_id: 'bbbb-nothing-known', quantity: 1, line_total: 5 },
                    ],
                };
                const cartNames = { 'aaaa-no-server-name': 'Cart-Captured Name' };
                RetailSystem._showReceipt(saleData, cartNames);
                const html = document.getElementById('ret-receipt-dialog').innerHTML;
                RetailSystem._closeModalOverlay(document.getElementById('ret-receipt-dialog'));
                return html;
            }
        """)
        assert 'Cart-Captured Name' in result, (
            f"link 2 (the cart's own captured name) did not render when the server sent no name. HTML: {result[:1500]}")
        assert 'Item' in result, (
            f"link 3 (t('Item')) did not render for a line with no server name and no cart name. HTML: {result[:1500]}")
        assert not UUID_PATTERN.search(result), (
            f"a line fell all the way back to the bare id -- the chain must NEVER do that. HTML: {result[:1500]}")

    check("item #6b: client fallback chain (cart names, then t('Item')) renders correctly when the server sends no name", item6b_client_fallback_chain_in_isolation)

    # =========================================================================
    # ITEM #16 -- short document numbers; return lookup by the short number
    # =========================================================================
    def item16_short_doc_numbers():
        go_pos(page)
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)
        page.wait_for_timeout(400)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.wait_for_timeout(300)

        bdi_text = page.eval_on_selector("#ret-receipt-dialog p bdi", "el => el.textContent")
        assert re.match(r"^SALE-\d{6}$", bdi_text), (
            f"the receipt's prominent number is {bdi_text!r}, expected /^SALE-\\d{{6}}$/ (short form only)")
        full_number = page.evaluate("() => RetailSystem._lastSaleData.sale_number")
        short_number = re.match(r"^(SALE-\d{6})", full_number).group(1)
        assert bdi_text == short_number, f"prominent number {bdi_text!r} does not match the real sale's own short form {short_number!r}"

        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        # Sales History row shows the same short form.
        go(page, "sales")
        page.wait_for_selector("#sh-table", timeout=10000)
        page.wait_for_timeout(300)
        row_text = page.eval_on_selector("#sh-table tbody tr td button.ret-rowbtn bdi", "el => el.textContent")
        assert re.match(r"^SALE-\d{6}$", row_text), f"Sales History's own number cell is {row_text!r}, expected the short form"

        # Return lookup BY THE SHORT NUMBER still finds the sale (the live
        # consequence of shortening the display everywhere -- a cashier now
        # reads and types this shorter string).
        go(page, "returns")
        page.wait_for_timeout(500)
        page.click('button:has-text("Process Return")')
        page.wait_for_selector("#ret-return-dialog", timeout=8000)
        page.fill("#ret-sale-search", short_number)
        page.click('#ret-return-dialog button:has-text("Lookup")')
        page.wait_for_timeout(600)
        found_sale_id = page.evaluate("() => RetailSystem._returnSaleId")
        assert found_sale_id is not None, f"looking up '{short_number}' (the short number) did not find the sale"
        row_count = page.locator(".ret-item-cb").count()
        assert row_count > 0, "the return dialog shows no line items after a successful lookup"
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-return-dialog", state="detached", timeout=5000)

    check("item #16: receipt/Sales-History show SALE-NNNNNN only; a return lookup by that short number finds the sale", item16_short_doc_numbers)

    # =========================================================================
    # PHASE 0 CREW C3b (till-2) -- items #7, #8, #14(d) appended below. C3a's
    # own items (#1/#3/#5/#6/#16, above) are untouched; this crew only adds
    # scenarios after them, per contracts.md's "append" instruction for this
    # file.
    # =========================================================================
    def set_theme(p, theme):
        p.evaluate("(th) => ThemeEngine.apply(th)", theme)
        p.wait_for_timeout(150)

    def set_lang(p, lang):
        p.evaluate("(l) => AuraI18n.setLang(l)", lang)
        p.wait_for_timeout(400)

    # Item #7's own text: "Small fixtures hide the bug" -- C3a's 5 products
    # (4 plain + 1 XSS-name) never overflow .pos-product-grid and never need a
    # 2-line name to prove #7a. >= 16 products, including one deliberately
    # long name, is what the item's own test list asks for.
    LONG_NAME = "Wireless Bluetooth Over-Ear Noise Cancelling Headphones"
    EXTRA_PIDS = [create_product(f"Extra Item {i}", f"P0-E{i}", 2 + i) for i in range(1, 13)]
    PID_LONG = create_product(LONG_NAME, "P0-LN1", 45)
    print(f"Catalogue now has {5 + len(EXTRA_PIDS) + 1} products (>= 16 required for item #7).")

    # =========================================================================
    # ITEM #7 -- till layout: names readable, cart lines visible, Charge in
    # its own footer, fields that fit the tender. Run across the item's own
    # required matrix: 1366x768 and 1440x900, EN and AR, sand and Day, with
    # the >= 16 products seeded above. Each combo re-proves all four checks
    # the item lists, so a regression that only shows up in Arabic or only at
    # one viewport cannot hide behind the other 7 combos passing.
    # =========================================================================
    # Round 3 (item #7, 1280x720/1280x800 regression, medium): 1280-wide is
    # a common laptop/POS panel too, narrower than either size below, and a
    # heavy sale hid the money there in BOTH languages (probe3
    # more_viewports_heavy: #pos-total/tax/discount 0% visible at 1280x720
    # in EN and AR; row_overflow_1280x720: all 4 cart rows overflowing
    # .pos-cart by 32px, pushing the remove buttons outside it). Added to
    # THIS matrix, not a separate one-off script, so every check item #7
    # already runs (icon placeholder, name-clip, cart-row visibility,
    # tender reachability, F1/F2) re-proves itself at this size too, not
    # only the heavier fixture step (e) below.
    VIEWPORTS = [(1366, 768), (1440, 900), (1280, 720), (1280, 800)]
    LANGS = ["en", "ar"]
    THEMES = ["sand", "light"]

    def item7_layout_matrix():
        for (w, h) in VIEWPORTS:
            for lang in LANGS:
                for theme in THEMES:
                    combo = f"{w}x{h} lang={lang} theme={theme}"
                    page.set_viewport_size({"width": w, "height": h})
                    set_lang(page, lang)
                    set_theme(page, theme)
                    go_pos(page)
                    page.wait_for_timeout(250)

                    # (a) no category-icon placeholder anywhere in the grid --
                    # removed entirely, not just hidden (monograms are Phase 1a).
                    icon_count = page.locator(".pos-card-icon").count()
                    assert icon_count == 0, (
                        f"[{combo}] .pos-card-icon placeholder still renders ({icon_count} found)")

                    # (a) the long name is not clipped: its rendered box is
                    # close to a true two-line box, not squeezed toward one
                    # line the way the probe measured (21px of an expected
                    # 34px, i.e. ~62%). 85% is a safety margin above that.
                    name_el = page.locator(f'.pos-card-name[title="{LONG_NAME}"]').first
                    name_el.wait_for(state="visible", timeout=8000)
                    handle = name_el.element_handle()
                    metrics = page.evaluate(
                        "(el) => ({height: el.getBoundingClientRect().height, "
                        "lineHeight: parseFloat(getComputedStyle(el).lineHeight)})", handle)
                    two_line = metrics["lineHeight"] * 2
                    assert metrics["height"] >= two_line * 0.85, (
                        f"[{combo}] long product name box is {metrics['height']:.1f}px tall, "
                        f"expected close to a true 2-line box ({two_line:.1f}px) -- looks clipped")

                    # (b) cart: 4 distinct lines, >= 3 fully inside the cart
                    # pane, and the pane header carries the live line count.
                    page.evaluate("() => RetailSystem._clearCart()")
                    for pid in (PID_1, PID_2, PID_3, PID_4):
                        page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
                    page.wait_for_timeout(350)

                    title_text = page.eval_on_selector("#pos-pane-title", "el => el.textContent")
                    assert "4" in title_text, (
                        f"[{combo}] pane title is {title_text!r}, expected it to carry the line count (4)")

                    cart_box = page.eval_on_selector(
                        "#pos-cart",
                        "el => { const r = el.getBoundingClientRect(); return {top:r.top,bottom:r.bottom}; }")
                    row_boxes = page.eval_on_selector_all(
                        "#pos-cart .pos-cart-row",
                        "els => els.map(el => { const r = el.getBoundingClientRect(); "
                        "return {top:r.top,bottom:r.bottom}; })")
                    fully_visible = sum(
                        1 for r in row_boxes
                        if r["top"] >= cart_box["top"] - 0.5 and r["bottom"] <= cart_box["bottom"] + 0.5)
                    assert fully_visible >= 3, (
                        f"[{combo}] only {fully_visible} of {len(row_boxes)} cart rows are fully inside "
                        f"the cart box (top={cart_box['top']:.1f}, bottom={cart_box['bottom']:.1f}); "
                        f"rows={row_boxes}")

                    # (c) every tender button is actually hittable at its own
                    # centre, WITH NO SCROLL of any kind -- Charge, in its own
                    # non-scrolling footer, must never paint over any of them.
                    #
                    # Review fix (2026-09-27, high): this used to call
                    # `scrollIntoView` on each button BEFORE the hit test,
                    # which is exactly item #7(c)'s own review finding --
                    # scrolling a button into view before checking whether it
                    # is reachable makes the check pass whether or not a
                    # cashier could actually reach it WITHOUT scrolling, which
                    # is the literal claim item #7 makes ("elementFromPoint at
                    # the centre of every tender lands on that tender", no
                    # scrolling mentioned or implied). Measured against the
                    # pre-fix tree with this exact scrollIntoView still in
                    # place: 0 of 6 tenders hittable at 1366x768 with a
                    # promoted line in the cart (c3_measure_results.json
                    # '#7(c) tenders unreachable without scrolling') -- a
                    # regression this test could not see because it scrolled
                    # the defect away before ever measuring anything.
                    #
                    # ROUND 2, T1's OWN GAP, NOW CLOSED (round 2, till R2): T1
                    # moved the tender grid/Cash Tendered/Change Due into
                    # .pos-checkout-footer (a flex-shrink:0 sibling that never
                    # scrolls, like Charge) and made all 6 tenders below
                    # reachable with no scroll -- but starved .pos-summary down
                    # to an 18-105px strip in the process (measured at
                    # 1366x768/1440x900 with a promoted line, a discount and
                    # Change Due showing), pushing #pos-total/Tax/Promotions/
                    # Discount off-screen instead. Round 1's C3c crew had
                    # already measured and reverted this exact trade once; T1
                    # re-applied it and this loop's own tender-only checks
                    # never caught the regression, because they check the
                    # tenders and nothing above them. The fix actually
                    # shipped is a height RE-BUDGET, not a re-scroll: Void
                    # moved into .pos-pane-hdr (reclaiming its own bar),
                    # Discount % folded onto the Subtotal row, Change Due
                    # folded onto the Cash Tendered row, and the short-till
                    # compaction breakpoint widened from 820px to 920px so
                    # 1440x900 gets it too (see subsystem-retail.js's own
                    # comments on each of those). The heavier fixture-and-
                    # reachability check just below (promoted line + manual
                    # discount + Cash Tendered showing Change Due + a
                    # customer list) is what actually proves #pos-total and
                    # its neighbours survived that re-budget, rather than
                    # trusting the tender-only loop above to imply it.
                    for method in ("cash", "card", "mobile", "transfer", "credit", "voucher"):
                        sel = f'.pos-pay-btn[data-method="{method}"]'
                        box = page.eval_on_selector(
                            sel,
                            "el => { const r = el.getBoundingClientRect(); "
                            "return {x:r.x+r.width/2,y:r.y+r.height/2, "
                            "inViewport: (r.top>=0 && r.bottom<=window.innerHeight)}; }")
                        assert box["inViewport"], (
                            f"[{combo}] the '{method}' tender is outside the viewport with NO scroll "
                            f"performed -- it is not reachable without scrolling")
                        hit_method = page.evaluate(
                            "(pt) => { const el = document.elementFromPoint(pt.x, pt.y); "
                            "const btn = el && el.closest ? el.closest('.pos-pay-btn') : null; "
                            "return btn ? btn.dataset.method : null; }", box)
                        assert hit_method == method, (
                            f"[{combo}] elementFromPoint at the centre of the '{method}' tender resolved "
                            f"to '{hit_method}', not itself -- something is painted over it")

                    # (d) Cash Tendered row toggles by tender, via F1 (cash) /
                    # F2 (card) -- the accelerator badges printed on the grid.
                    page.click('.pos-pay-btn[data-method="cash"]')
                    page.wait_for_timeout(100)
                    row_display_cash = page.eval_on_selector(
                        "#pos-tendered-row", "el => getComputedStyle(el).display")
                    assert row_display_cash != "none", (
                        f"[{combo}] Cash Tendered row is hidden while the tender IS cash")

                    page.keyboard.press("F2")
                    page.wait_for_timeout(150)
                    row_display_card = page.eval_on_selector(
                        "#pos-tendered-row", "el => getComputedStyle(el).display")
                    assert row_display_card == "none", f"[{combo}] F2 (Card) did not hide the Cash Tendered row"

                    page.keyboard.press("F1")
                    page.wait_for_timeout(150)
                    row_display_back = page.eval_on_selector(
                        "#pos-tendered-row", "el => getComputedStyle(el).display")
                    assert row_display_back != "none", f"[{combo}] F1 (Cash) did not show the row again"

                    # (e) Round 2, till R2 (items #7(c)/#8 "totals invisible
                    # without scroll" + "Charge clipped with customers" +
                    # #7(b) "promoted line steals cart rows"): the heavier
                    # fixture the review actually measured this regression
                    # under -- a promoted line, a manual discount, Cash
                    # Tendered showing Change Due, and a customer picked from
                    # the 3-name list seeded above -- none of which (a)-(d)
                    # above exercise together. theme == 'sand' only: colour
                    # never changes layout, and running this twice per
                    # viewport/lang for two themes would only double runtime
                    # for no new signal, so it runs once per (width, height,
                    # lang) the SAME way the item's own fix text asks for
                    # ("at 1366x768 and 1440x900 in EN and AR").
                    if theme == "sand":
                        page.evaluate("() => RetailSystem._clearCart()")
                        for pid in (PID_1, PID_2, PID_R2PROMO, PID_4):
                            page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
                        page.select_option("#pos-customer", CUST_ID)
                        page.fill("#pos-disc", "5")
                        page.click('.pos-pay-btn[data-method="cash"]')
                        page.fill("#pos-tendered", "100")
                        page.wait_for_timeout(350)

                        change_row_display = page.eval_on_selector(
                            "#pos-change-row", "el => getComputedStyle(el).display")
                        assert change_row_display != "none", (
                            f"[{combo}] Change Due is not showing under this fixture (cash 100 against a "
                            f"smaller total) -- the fixture itself is wrong, not the layout")

                        # Opt-in diagnostic (same pattern as P0_ONLY above),
                        # a no-op unless P0_DEBUG_GEOM is set: dumps the
                        # exact rects/scroll deficits this step's own
                        # assertions below check, which is what iteratively
                        # tuned subsystem-retail.js's SHORT TILLS media query
                        # to actually pass this fixture rather than guessing
                        # pixel values from reading the CSS.
                        if os.environ.get("P0_DEBUG_GEOM"):
                            geom = page.evaluate("""() => {
                                const rect = (sel) => { const el = document.querySelector(sel); if (!el) return null;
                                    const r = el.getBoundingClientRect(); return {top:+r.top.toFixed(1), bottom:+r.bottom.toFixed(1), h:+r.height.toFixed(1)}; };
                                const box = (sel) => { const el = document.querySelector(sel); if (!el) return null;
                                    return {client:el.clientHeight, scroll:el.scrollHeight}; };
                                return {
                                    win: window.innerHeight,
                                    paneHdr: rect('.pos-right .pos-pane-hdr'), cart: rect('#pos-cart'),
                                    summary: rect('.pos-summary'), summaryBox: box('.pos-summary'),
                                    footer: rect('.pos-checkout-footer'), total: rect('#pos-total'),
                                    tax: rect('#pos-tax'), disc: rect('#pos-disc'),
                                    promoRow: rect('#pos-promo-row'), discRow: rect('#pos-discamt-row'),
                                    checkoutBtn: rect('#pos-checkout-btn'), right: rect('.pos-right'),
                                };
                            }""")
                            print(f"GEOM [{combo}]: {geom}")

                        def reachable(elem_id, container_sel=None):
                            box = page.eval_on_selector(
                                f"#{elem_id}",
                                "el => { const r = el.getBoundingClientRect(); "
                                "const cs = getComputedStyle(el); "
                                "return {top:r.top, bottom:r.bottom, left:r.left, right:r.right, "
                                "x:r.x+r.width/2, y:r.y+r.height/2, w:r.width, h:r.height, "
                                "display:cs.display, "
                                "inViewport:(r.top>=0 && r.bottom<=window.innerHeight)}; }")
                            assert box["display"] != "none" and box["w"] > 0 and box["h"] > 0, (
                                f"[{combo}] #{elem_id} has no visible box (display={box['display']!r}, "
                                f"{box['w']}x{box['h']})")
                            assert box["inViewport"], (
                                f"[{combo}] #{elem_id} is outside the viewport with NO scroll performed -- "
                                f"not reachable without scrolling")
                            hit = page.evaluate(
                                "(pt) => { const el = document.elementFromPoint(pt.x, pt.y); "
                                "return !!(el && el.closest && el.closest(pt.sel)); }",
                                {**box, "sel": f"#{elem_id}"})
                            assert hit, (
                                f"[{combo}] elementFromPoint at #{elem_id}'s own centre does not resolve "
                                f"inside it -- something else is painted over it")
                            # Round 3 (item #7(c)/#8 "half-hidden total"
                            # regression, medium): the centre-point hit test
                            # above passes even when MOST of an element is
                            # clipped -- #pos-total was measured at only 60%
                            # of its own height visible inside .pos-summary
                            # at 1366x768/ar/heavy while this exact
                            # centre-point check still passed, because the
                            # centre itself still landed inside the visible
                            # sliver (probe2 total_visibility). This asserts
                            # the element's FULL rect, not just its centre,
                            # lies inside its scrolling container's own
                            # visible box -- the thing a cashier actually
                            # has to read, not just tap.
                            if container_sel:
                                cbox = page.eval_on_selector(
                                    container_sel,
                                    "el => { const r = el.getBoundingClientRect(); "
                                    "return {top:r.top, bottom:r.bottom}; }")
                                assert box["top"] >= cbox["top"] - 0.5 and box["bottom"] <= cbox["bottom"] + 0.5, (
                                    f"[{combo}] #{elem_id}'s full rect (top={box['top']:.1f}, "
                                    f"bottom={box['bottom']:.1f}) is not fully inside {container_sel}'s own "
                                    f"visible rect (top={cbox['top']:.1f}, bottom={cbox['bottom']:.1f}) -- "
                                    f"only part of it is actually visible, even though its centre point is")
                            return box

                        # #pos-total (grand total), Tax, the Discount % input
                        # and BOTH the Promotions and Discount rows -- item
                        # #7(c)'s own defect list, verbatim. Each one's FULL
                        # rect, not just its centre, must lie inside
                        # .pos-summary's own visible box (see reachable()'s
                        # own review-fix comment above).
                        for money_id in ("pos-total", "pos-tax", "pos-disc", "pos-promo-row", "pos-discamt-row"):
                            reachable(money_id, container_sel=".pos-summary")

                        # .pos-grand-label ("TOTAL", no id of its own) --
                        # item #7(c)/#8's own defect named this specifically
                        # ("the 'Total' label shows 3.6 of 19.5px").
                        label_box = page.eval_on_selector(
                            ".pos-total-band .pos-grand-label",
                            "el => { const r = el.getBoundingClientRect(); "
                            "return {top:r.top, bottom:r.bottom, h:r.height}; }")
                        label_summary_box = page.eval_on_selector(
                            ".pos-summary",
                            "el => { const r = el.getBoundingClientRect(); return {top:r.top, bottom:r.bottom}; }")
                        assert label_box["h"] > 0, f"[{combo}] .pos-grand-label has no rendered height"
                        assert (label_box["top"] >= label_summary_box["top"] - 0.5
                                and label_box["bottom"] <= label_summary_box["bottom"] + 0.5), (
                            f"[{combo}] .pos-grand-label's rect ({label_box}) is not fully inside "
                            f".pos-summary's visible rect ({label_summary_box}) -- the 'Total' caption is clipped")

                        # Charge's own rect lies fully inside .pos-right AND
                        # the viewport -- item #7(c)'s "Charge is clipped on
                        # a shop with customers" finding, verbatim.
                        reachable("pos-checkout-btn")
                        checkout_box = page.eval_on_selector(
                            "#pos-checkout-btn", "el => el.getBoundingClientRect().toJSON()")
                        right_box = page.eval_on_selector(
                            ".pos-right", "el => el.getBoundingClientRect().toJSON()")
                        assert checkout_box["top"] >= right_box["top"] - 0.5 and (
                            checkout_box["bottom"] <= right_box["bottom"] + 0.5), (
                            f"[{combo}] Charge's own rect {checkout_box} is not fully inside .pos-right's "
                            f"{right_box} -- clipped")

                        # >= 3 of 4 cart rows fully visible WITH a promoted
                        # line among them -- item #7(b)'s own finding.
                        promo_row_boxes = page.eval_on_selector_all(
                            "#pos-cart .pos-cart-row",
                            "els => els.map(el => { const r = el.getBoundingClientRect(); "
                            "return {top:r.top,bottom:r.bottom}; })")
                        promo_cart_box = page.eval_on_selector(
                            "#pos-cart",
                            "el => { const r = el.getBoundingClientRect(); return {top:r.top,bottom:r.bottom}; }")
                        promo_fully_visible = sum(
                            1 for r in promo_row_boxes
                            if r["top"] >= promo_cart_box["top"] - 0.5 and r["bottom"] <= promo_cart_box["bottom"] + 0.5)
                        assert promo_fully_visible >= 3, (
                            f"[{combo}] with a PROMOTED line in the cart, only {promo_fully_visible} of "
                            f"{len(promo_row_boxes)} rows are fully inside the cart box; rows={promo_row_boxes}")

        # Restore the harness's own default state for every scenario after this one.
        page.evaluate("() => RetailSystem._clearCart()")
        set_lang(page, "en")
        set_theme(page, "light")
        page.set_viewport_size({"width": 1440, "height": 900})

    check("item #7(a-d): no clipped names, no icon placeholder, >=3/4 cart rows visible, tenders hittable, "
          "F1/F2 toggle Cash Tendered -- at 1366x768/1440x900, EN/AR, sand/Day, >=16 products",
          item7_layout_matrix)

    # =========================================================================
    # ROUND 2, T3 (item #7(a) "guard vacuous" finding, closed): round 1's own
    # honest investigation (kept below in git history, not here) found that
    # item7_layout_matrix's name-clip check only ever stresses ONE tile, and
    # that reverting grid-auto-rows:max-content -> auto ALONE did not
    # reproduce clipping in this Chromium at all -- two stress attempts
    # (rewriting every name to a long string; swapping in all-long-name data
    # at a squeezed 1440x640 viewport) both still passed with the rule
    # reverted, so nothing in the suite actually guarded it.
    #
    # The missing piece, found by testing the round-2 task text's OWN
    # instruction literally: grid-auto-rows:auto alone genuinely does not
    # clip in this engine, but grid-auto-rows:auto COMBINED WITH the removed
    # placeholder icon span restored (a third flex child competing for the
    # SAME min-block-size:118px card, .pos-card's own
    # "justify-content:center;gap:4px" column) does, decisively: measured
    # live, 0 of 30 stressed tiles clipped with the rule alone reverted,
    # 30 of 30 clipped once the icon span came back too (.pos-card-name
    # height collapsed from ~34px to 9px, a hard floor its own
    # -webkit-line-clamp:2 could not fill because the FLEX PARENT never grew
    # to give it the room). That is the ORIGINAL defect state item #7(a)
    # describes -- both changes shipped together -- and reproducing it needs
    # both together too.
    #
    # This lives here, injected live against the REAL running page, rather
    # than as a source-file edit-run-revert cycle, for the same reason this
    # file's own "review fix, item #8: _money_epsilon..." check already
    # calls the real function under several synthetic conditions in one
    # script: a standing regression guard has to DEMONSTRATE causation
    # (the rule is what prevents the clip) inside the one test that will
    # keep running, not just assert the current, already-fixed state looks
    # fine -- the exact gap that made the old check vacuous.
    # =========================================================================
    def item7a_clipped_name_guard_is_non_vacuous():
        _, cat_body = api(page, "POST", "/api/sub/retail/categories", {"name": "T3 Stress"})
        cat_id = ((cat_body or {}).get("data") or {}).get("id")
        assert cat_id, f"T3 stress category was not created: {cat_body}"
        long_base = "Premium Wireless Bluetooth Noise-Cancelling Over-Ear Headphones"
        for i in range(20):
            _, body = api(page, "POST", "/api/sub/retail/products", {
                "name": f"{long_base} {i}", "sku": f"T3-{i}", "sell_price": 10, "cost_price": 5,
                "category_id": cat_id, "unit": "pc",
            })
            assert (body or {}).get("status") == "success", f"T3 stress product {i} was not created: {body}"

        # 1440x640: short and wide, so .pos-product-grid is a flex item with
        # a constrained CROSS-axis size and genuinely overflows vertically
        # with 20 two-line tiles -- the exact shape item #7(a)'s own REFS
        # describe the trigger needing ("a grid that is ALSO a flex item
        # with a constrained cross-axis size").
        page.set_viewport_size({"width": 1440, "height": 640})
        go_pos(page)
        # this._activeCat set directly, not through _setCat(catId, btn) --
        # that function unconditionally does btn.classList.add(...), and
        # there is no real category-pill button to pass for a category the
        # rail has not rendered a pill for yet in this fresh mount.
        page.evaluate("(id) => { RetailSystem._activeCat = id; RetailSystem._renderPOSGrid(); }", cat_id)
        page.wait_for_timeout(400)

        def measure_clipping():
            return page.evaluate("""
                () => {
                    const els = Array.from(document.querySelectorAll('.pos-card-name'));
                    const rows = els.map(el => {
                        const r = el.getBoundingClientRect();
                        const lh = parseFloat(getComputedStyle(el).lineHeight) || 1;
                        return {height: r.height, lineHeight: lh};
                    });
                    const clipped = rows.filter(x => x.height < x.lineHeight * 1.8);
                    return {total: rows.length, clippedCount: clipped.length};
                }
            """)

        baseline = measure_clipping()
        assert baseline["total"] >= 20, f"T3 stress fixture did not render (only {baseline['total']} tiles)"
        assert baseline["clippedCount"] == 0, (
            f"BASELINE (the real, fixed product): {baseline['clippedCount']} of {baseline['total']} long "
            f"names are already clipped before any mutation -- item #7(a) itself has regressed")

        # Round 2, till R2 ("T3 guard still cannot see the real grid-auto-
        # rows fix being reverted"): the adversarial re-review of the "closed"
        # finding above found that neither assertion this test made actually
        # depended on the SOURCE FILE's own grid-auto-rows:max-content rule --
        # baseline runs against the real page as-is, and the mutation below
        # ALWAYS forces grid-auto-rows:auto from a page-level style tag, so
        # reverting the real source rule to auto (and deleting the page-
        # level override entirely) still passed every check here: the page
        # override was doing the fix's job, not the shipped CSS. This step B
        # -- between the no-stress baseline (A) and the combined mutation
        # (C) below -- injects ONLY the stress icon, with NO page-level
        # override, so it measures the REAL, currently-shipped rule under
        # stress. Mutation-proved by literally reverting subsystem-
        # retail.js's own `grid-auto-rows:max-content;` to `grid-auto-
        # rows:auto;` and rerunning this exact step:
        #   RED (source reverted): 20 of 20 clipped -- identical to state C,
        #     because with no page override this step now measures the
        #     broken source directly.
        #   GREEN (source restored): 0 of 20 clipped, quoted below.
        page.evaluate("""
            () => {
                document.querySelectorAll('.pos-card').forEach(btn => {
                    if (btn.querySelector('.pos-card-icon-mutant')) return;
                    const span = document.createElement('span');
                    span.className = 'pos-card-icon-mutant';
                    span.setAttribute('aria-hidden', 'true');
                    span.style.fontSize = '28px';
                    span.style.lineHeight = '1';
                    span.textContent = '\\u{1F4E6}';
                    btn.insertBefore(span, btn.firstChild);
                });
            }
        """)
        page.wait_for_timeout(250)
        icon_only = measure_clipping()
        assert icon_only["clippedCount"] == 0, (
            f"STEP B (icon restored, NO page-level override -- the REAL shipped rule alone): "
            f"{icon_only['clippedCount']} of {icon_only['total']} clipped. If this is nonzero on the "
            f"unmutated source, item #7(a) has regressed for real; if it stays zero after reverting "
            f"grid-auto-rows:max-content -> auto in the source, this step is vacuous again.")

        # The mutation: BOTH pieces of the original defect, together --
        # grid-auto-rows reverted to the browser default, and the removed
        # placeholder icon restored as a real flex child ahead of the name
        # (already on the page from step B above; this adds the CSS half).
        page.add_style_tag(content=".pos-product-grid { grid-auto-rows: auto !important; }")
        page.evaluate("""
            () => {
                document.querySelectorAll('.pos-card').forEach(btn => {
                    if (btn.querySelector('.pos-card-icon-mutant')) return;
                    const span = document.createElement('span');
                    span.className = 'pos-card-icon-mutant';
                    span.setAttribute('aria-hidden', 'true');
                    span.style.fontSize = '28px';
                    span.style.lineHeight = '1';
                    span.textContent = '\\u{1F4E6}';
                    btn.insertBefore(span, btn.firstChild);
                });
            }
        """)
        page.wait_for_timeout(250)
        mutated = measure_clipping()
        assert mutated["clippedCount"] > 0, (
            f"MUTATION (grid-auto-rows:auto + restored icon span, the original defect state): "
            f"{mutated['clippedCount']} of {mutated['total']} clipped -- expected clipping to reappear. "
            f"The guard is vacuous again: it cannot tell the original defect from the fix.")

        # Clean-up: a fresh mount rebuilds .pos-product-grid's markup (no
        # mutant icon spans) and re-injects this component's own stylesheet
        # (the real grid-auto-rows:max-content, at normal CSS specificity,
        # beats the page-level override this test added, so nothing has to
        # be removed by hand). Restore the working viewport for whatever
        # runs next.
        go_pos(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(200)

    check("item #7(a) T3: 20 stressed long-name tiles are unclipped on the real, fixed product, and clip "
          "again when BOTH pieces of the original defect (grid-auto-rows:auto + the removed icon span) are "
          "restored together -- the guard can tell the fix from the defect", item7a_clipped_name_guard_is_non_vacuous)

    # =========================================================================
    # ITEM #7(c) -- Charge lives in its own non-scrolling footer: recheck the
    # <=640px sheet mode, and that Void stays well clear of it (never a
    # mis-tap risk at till speed, per the Void-to-Charge separation test).
    # =========================================================================
    def item7c_footer_and_sheet_mode():
        page.set_viewport_size({"width": 1366, "height": 768})
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        for pid in (PID_1, PID_2, PID_3, PID_4):
            page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
        page.wait_for_timeout(300)

        # Round 2, till R2 (items #7(c)/#8 layout re-budget): Void moved out
        # of its own bar (.pos-cart-tools, now removed) into .pos-pane-hdr,
        # right next to Hold -- see that markup's own comment for the
        # measured height this reclaims. Void is now FARTHER from Charge
        # than before (opposite ends of the pane), so the old "stays >40px
        # clear of Charge's footer" geometry check no longer measures a real
        # risk. The real risk moved to Void sitting right next to Hold, and
        # the mitigation moved with it: _voidSale() now gates the actual
        # clear behind a _confirm() dialog (the SAME pattern every other
        # destructive control on this file uses), so THIS is what the guard
        # proves instead -- a mis-tap opens a dialog, it does not silently
        # empty the sale, and Cancel leaves the cart exactly as it was.
        footer_box = page.eval_on_selector(".pos-checkout-footer", "el => el.getBoundingClientRect().toJSON()")
        cart_count_before = page.evaluate("() => RetailSystem._cart.length")
        assert cart_count_before == 4, f"expected the 4-line fixture cart, got {cart_count_before} lines"
        page.click('.pos-pane-hdr .pos-clear-btn')
        page.wait_for_selector("#ret-confirm-dialog", timeout=5000)
        page.click("#ret-confirm-cancel")
        page.wait_for_selector("#ret-confirm-dialog", state="detached", timeout=5000)
        cart_count_after_cancel = page.evaluate("() => RetailSystem._cart.length")
        assert cart_count_after_cancel == 4, (
            f"Cancel on the Void confirm dialog must leave the cart untouched -- "
            f"expected 4 lines, got {cart_count_after_cancel}")
        page.click('.pos-pane-hdr .pos-clear-btn')
        page.wait_for_selector("#ret-confirm-dialog", timeout=5000)
        page.click("#ret-confirm-ok")
        page.wait_for_selector("#ret-confirm-dialog", state="detached", timeout=5000)
        cart_count_after_confirm = page.evaluate("() => RetailSystem._cart.length")
        assert cart_count_after_confirm == 0, (
            f"Confirming the Void dialog must clear the cart -- expected 0 lines, "
            f"got {cart_count_after_confirm}")
        for pid in (PID_1, PID_2, PID_3, PID_4):
            page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
        page.wait_for_timeout(300)

        # The footer does not scroll away: scroll .pos-summary (its sibling)
        # to the end and confirm Charge's own position is unaffected.
        page.evaluate(
            "() => { const s = document.querySelector('.pos-summary'); if (s) s.scrollTop = s.scrollHeight; }")
        page.wait_for_timeout(150)
        footer_box_after = page.eval_on_selector(".pos-checkout-footer", "el => el.getBoundingClientRect().toJSON()")
        assert abs(footer_box_after["top"] - footer_box["top"]) < 1, (
            "Charge's footer moved when .pos-summary was scrolled -- it is supposed to be a fixed, "
            "non-scrolling sibling, not part of the scrolling summary")

        # <=640px sheet mode: open the phone cart sheet and confirm Charge is
        # reachable without any further scroll, and is not clipped by the
        # sheet's own bounds.
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        page.click("#pos-peek-charge")
        page.wait_for_timeout(400)
        checkout_box = page.eval_on_selector("#pos-checkout-btn", "el => el.getBoundingClientRect().toJSON()")
        assert 0 <= checkout_box["top"] < 844 and checkout_box["bottom"] <= 845, (
            f"In the <=640px sheet, Charge is at top={checkout_box['top']:.1f} "
            f"bottom={checkout_box['bottom']:.1f} in an 844px-tall viewport -- not fully in view")
        center_x = checkout_box["x"] + checkout_box["width"] / 2
        center_y = checkout_box["top"] + checkout_box["height"] / 2
        hit_is_checkout = page.evaluate(
            "(pt) => { const el = document.elementFromPoint(pt.x, pt.y); "
            "return !!(el && el.closest && el.closest('#pos-checkout-btn')); }",
            {"x": center_x, "y": center_y})
        assert hit_is_checkout, "In the <=640px sheet, the centre of Charge does not hit Charge itself"

        page.set_viewport_size({"width": 1366, "height": 768})
        page.wait_for_timeout(200)

    check("item #7(c): Charge's footer never scrolls, Void's confirm dialog protects it now that Void "
          "sits next to Hold (Cancel leaves the cart untouched, Confirm clears it), and Charge is "
          "reachable/hittable in the <=640px sheet", item7c_footer_and_sheet_mode)

    # =========================================================================
    # ITEM #8 -- totals explain the promotion: post-promotion line total
    # (struck-through original), Promotions and Discount rows, money identity
    # at 3dp. (Till side -- #8-display is C6's, over customer-display.js.)
    # =========================================================================
    def item8_promotion_and_discount_breakdown():
        go(page, "dashboard")
        _, promo_body = api(page, "POST", "/api/sub/retail/promotions",
                             {"name": "P0 Test Promo", "product_id": PID_1, "discount_pct": 20})
        assert promo_body.get("status") == "success", f"promotion was not created: {promo_body}"

        go_pos(page)   # fresh mount -- _loadPOSData() re-fetches /promotions/active
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)   # Widget, 10, 20% promo
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)   # Gadget, 5, no promo
        page.wait_for_timeout(300)
        page.fill("#pos-disc", "10")   # manual 10% on top of the promotion
        page.wait_for_timeout(300)

        totals = page.evaluate("() => RetailSystem._currentTotals")
        subtotal, tax, total = totals["subtotal"], totals["tax"], totals["total"]
        promotions, manual_discount = totals["promotions"], totals["manualDiscount"]
        # Review fix (was a hardcoded `0.0005`): `MONEY_EPS` is
        # `_money_epsilon(CURRENCY)`, the SAME half-minor-unit gate every
        # server-side money check in retail_api.py uses. A bare 0.0005 here
        # would silently stop meaning "half a fil" the day this file (or a
        # copy of this scenario) ever ran against a 2dp currency.
        assert promotions > MONEY_EPS, f"expected a nonzero promotions figure, got {promotions}"
        assert manual_discount > MONEY_EPS, f"expected a nonzero manual discount figure, got {manual_discount}"

        identity = subtotal - promotions - manual_discount + tax
        assert abs(identity - total) < MONEY_EPS, (
            f"Subtotal({subtotal}) - Promotions({promotions}) - Discount({manual_discount}) + Tax({tax}) "
            f"= {identity}, expected exactly Total ({total})")

        promo_row_display = page.eval_on_selector("#pos-promo-row", "el => getComputedStyle(el).display")
        assert promo_row_display != "none", "the Promotions row is hidden despite a nonzero promotion"
        disc_row_display = page.eval_on_selector("#pos-discamt-row", "el => getComputedStyle(el).display")
        assert disc_row_display != "none", "the Discount row is hidden despite a nonzero manual discount"

        promo_text = page.eval_on_selector("#pos-promo-value", "el => el.textContent")
        disc_text = page.eval_on_selector("#pos-discamt-value", "el => el.textContent")
        assert promo_text.strip().startswith("-"), f"Promotions value {promo_text!r} is not shown as a reduction"
        assert disc_text.strip().startswith("-"), f"Discount value {disc_text!r} is not shown as a reduction"

        # Post-promotion line total on the promoted line, with the original struck through.
        has_struck_original = page.locator("#pos-cart .pos-price-orig").count() > 0
        assert has_struck_original, "no struck-through original price rendered for the promoted line"

        # Review fix: the two assertions above only prove the SHOW half of
        # "own row, own visibility, hidden below half a minor unit"
        # (_recalc's own comment on this). They would stay green even if
        # _recalc's hide/show logic broke into "always display the row" --
        # nothing here ever put the cart back to zero and checked the OTHER
        # side. Clearing the cart and the manual discount is the negative
        # case: with no promotion and no manual discount left, both rows
        # must go back to `display:none`, the exact state _renderPOS's own
        # markup ships them in before any recalc ever runs.
        page.evaluate("() => RetailSystem._clearCart()")
        page.fill("#pos-disc", "0")
        page.wait_for_timeout(300)
        promo_row_display_zero = page.eval_on_selector("#pos-promo-row", "el => getComputedStyle(el).display")
        assert promo_row_display_zero == "none", (
            f"Promotions row is still shown (display={promo_row_display_zero!r}) with zero promotion -- "
            f"the hide-below-threshold half of the show/hide logic is broken")
        disc_row_display_zero = page.eval_on_selector("#pos-discamt-row", "el => getComputedStyle(el).display")
        assert disc_row_display_zero == "none", (
            f"Discount row is still shown (display={disc_row_display_zero!r}) with zero manual discount -- "
            f"the hide-below-threshold half of the show/hide logic is broken")
        # Cart/discount are already cleared/zeroed by the negative case above
        # -- nothing left to tidy up for the next scenario.

    check("item #8: Promotions and Discount rows both show, post-promotion line total struck-through, "
          "Subtotal - Promotions - Discount + Tax = Total at 3dp (MONEY_EPS-gated), and both rows hide "
          "again once promotion/discount return to zero", item8_promotion_and_discount_breakdown)

    # =========================================================================
    # REVIEW FIX (2026-09-27, high) -- item #7's own matrix (item7_layout_
    # matrix above) runs BEFORE any promotion exists in this file (the
    # promotion is created inside item8, further down) -- so its "3 of 4
    # cart rows fully visible" check never once exercised a promoted line,
    # and the fixture hid the bug (ENGINEERING.md failure shape 2, named
    # verbatim in this item's own review finding). A promoted line's
    # .pos-line-total shows two money figures (see that rule's own
    # review-fix comment); with no floor on .pos-line-main, the extra width
    # used to squeeze the product NAME down to single-digit pixels ('Widget'
    # rendered as an unreadable fragment). Reuses item #8's still-active
    # "P0 Test Promo" (20% on PID_1) rather than minting a second promotion
    # this scenario would have to clean up.
    # =========================================================================
    def review_promoted_line_keeps_name_readable():
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)  # Widget, 20% promo (item #8's fixture)
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_3)
        page.wait_for_timeout(350)

        has_struck = page.locator("#pos-cart .pos-price-orig").count() > 0
        assert has_struck, "no promoted line in this cart -- the fixture did not actually create one"

        name_width = page.eval_on_selector(
            "#pos-cart .pos-cart-row:has(.pos-price-orig) .pos-item-name",
            "el => el.getBoundingClientRect().width")
        assert name_width >= 40, (
            f"the promoted line's product name is only {name_width:.1f}px wide (expected >= 40px) -- "
            f".pos-line-total's two stacked figures are still squeezing .pos-line-main")

        page.evaluate("() => RetailSystem._clearCart()")

    check("review fix, item #7(b)/#8: a promoted cart line keeps its product name at a readable width "
          "(>= 40px) instead of being squeezed by the struck-through original + new total",
          review_promoted_line_keeps_name_readable)

    # =========================================================================
    # REVIEW FIX (2026-09-27, high) -- item #8's MONEY_EPS gate above swapped
    # a hardcoded `0.0005` for `_money_epsilon(CURRENCY)`, but every scenario
    # in this file runs against ONE company, seeded JOD-only (line ~224) --
    # `_money_epsilon('JOD')` and a hand-typed `0.0005` are numerically
    # IDENTICAL, so no mutation of "hardcode it back" could ever turn any
    # check in this file red. That is the reviewer's own finding verbatim:
    # "Like-for-like swap inside the test file... no product mutation can
    # distinguish them."
    #
    # This closes it the only way possible without a second, non-JOD company
    # (out of scope -- every product/sale/format assumption in this file is
    # JOD-shaped): call the REAL `_money_epsilon` directly against several
    # currencies with DIFFERENT minor-unit counts and pin the exact values a
    # hardcoded, JOD-shaped literal could never produce for any of them. A
    # mutation that breaks `_money_epsilon` itself (e.g. hardcodes its
    # return, or drops the /2.0 halving, or stops reading the currency's own
    # quantum) is what this catches -- restoring it is what item #8's own
    # MONEY_EPS-gated identity check above then continues to depend on.
    # =========================================================================
    def review_money_epsilon_is_currency_sensitive():
        jod_eps = _money_epsilon('JOD')  # 3dp minor unit (0.001) -> half = 0.0005
        usd_eps = _money_epsilon('USD')  # 2dp minor unit (0.01)  -> half = 0.005
        jpy_eps = _money_epsilon('JPY')  # 0dp minor unit (1)     -> half = 0.5
        assert jod_eps == 0.0005, f"_money_epsilon('JOD') = {jod_eps}, expected 0.0005 (3dp currency)"
        assert usd_eps == 0.005, f"_money_epsilon('USD') = {usd_eps}, expected 0.005 (2dp currency)"
        assert jpy_eps == 0.5, f"_money_epsilon('JPY') = {jpy_eps}, expected 0.5 (0dp currency)"
        # The actual claim: a single hardcoded literal (0.0005, or any other
        # one number) cannot be correct for all three -- proving these three
        # calls returned three DIFFERENT values is what proves the function
        # is genuinely reading each currency's own quantum, not just
        # returning a constant that happens to equal JOD's.
        assert len({jod_eps, usd_eps, jpy_eps}) == 3, (
            f"_money_epsilon returned the same value for currencies with different minor-unit counts "
            f"(JOD={jod_eps}, USD={usd_eps}, JPY={jpy_eps}) -- it is no longer currency-sensitive")
        assert MONEY_EPS == jod_eps, (
            f"this file's own MONEY_EPS ({MONEY_EPS}) does not equal the live _money_epsilon(CURRENCY) "
            f"({jod_eps}) -- CURRENCY drifted from what the company was actually seeded with")

    check("review fix, item #8: _money_epsilon is genuinely currency-sensitive (JOD/USD/JPY each give a "
          "different half-minor-unit), not a hardcoded literal that happens to match JOD",
          review_money_epsilon_is_currency_sensitive)

    # =========================================================================
    # ROUND 2, T2 (item #8) -- ONE CONVENTION for Promotions vs Discount,
    # proven IDENTICAL across the till's own live preview, the customer-
    # display relay payload (interface I-2) and the completed sale's
    # receipt. The item8 scenario above already proves the IDENTITY (Subtotal
    # - Promotions - Discount + Tax = Total) and that both rows can show --
    # it never once compared the till's PREVIEW numbers against what the
    # ACTUAL sale it was about to ring would show. `_recalc()`'s old split
    # gave a promo-winning line only the slice ABOVE the manual baseline
    # (`subtotal*manualFrac` applied uniformly first, the promo's own excess
    # layered on top) -- algebraically the same COMBINED discAmt, but a
    # smaller Promotions figure and a larger Discount figure than
    # `_saleDiscountBreakdown` (server `applied_promotion`/`discount_amount`
    # per line, WINNER-TAKES-ALL) computes for the identical sale. Fixed in
    # `_recalc()` (see its own review-fix comment) by accumulating
    # `winnerPromotions`/`winnerDiscount` the same winner-takes-all way.
    # =========================================================================
    def item8_t2_cross_surface_attribution():
        _, t2_promo_body = api(page, "POST", "/api/sub/retail/promotions",
                                {"name": "P0 T2 Promo", "product_id": PID_1, "discount_pct": 20})
        assert t2_promo_body.get("status") == "success", f"T2 promotion was not created: {t2_promo_body}"

        def run_case(cart_ids, manual_pct):
            go_pos(page)
            page.evaluate("() => RetailSystem._clearCart()")
            for pid in cart_ids:
                page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
            page.wait_for_timeout(300)
            page.fill("#pos-disc", str(manual_pct))
            page.wait_for_timeout(500)   # > the 150ms relay debounce (item #11's own proven wait)

            totals = page.evaluate("() => RetailSystem._currentTotals")
            till_promo_text = page.eval_on_selector("#pos-promo-value", "el => el.textContent")
            till_disc_text = page.eval_on_selector("#pos-discamt-value", "el => el.textContent")
            till_promo_shown = page.eval_on_selector("#pos-promo-row", "el => getComputedStyle(el).display") != "none"
            till_disc_shown = page.eval_on_selector("#pos-discamt-row", "el => getComputedStyle(el).display") != "none"

            identity = totals["subtotal"] - totals["promotions"] - totals["manualDiscount"] + totals["tax"]
            assert abs(identity - totals["total"]) < MONEY_EPS, (
                f"till preview identity: Subtotal({totals['subtotal']}) - Promotions({totals['promotions']}) "
                f"- Discount({totals['manualDiscount']}) + Tax({totals['tax']}) = {identity}, "
                f"expected Total ({totals['total']})")

            _, relay_body = api(page, "GET", "/api/sub/retail/customer-display/state?since=0")
            relay_msg = relay_body.get("message") or {}
            relay_promo = relay_msg.get("promotions")
            relay_disc = relay_msg.get("discount")

            # Charge on credit against the unlimited-credit customer -- the
            # same recipe item #1's own scenario already proved works, and
            # avoids computing a Cash Tendered amount here.
            page.select_option("#pos-customer", CUST_ID)
            page.wait_for_timeout(200)
            page.click('.pos-pay-btn[data-method="credit"]')
            page.wait_for_timeout(200)
            page.click("#pos-checkout-btn")
            page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
            page.wait_for_timeout(250)

            receipt_bd = page.evaluate("() => RetailSystem._saleDiscountBreakdown(RetailSystem._lastSaleData)")
            half_minor = page.evaluate("() => RetailSystem._halfMinorUnit()")

            page.click('#ret-receipt-dialog button:has-text("New Sale")')
            page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
            page.wait_for_timeout(200)

            return {
                "totals": totals, "till_promo_text": till_promo_text, "till_disc_text": till_disc_text,
                "till_promo_shown": till_promo_shown, "till_disc_shown": till_disc_shown,
                "relay_promo": relay_promo, "relay_disc": relay_disc,
                "receipt_bd": receipt_bd, "half_minor": half_minor,
            }

        def fmt(v):
            return page.evaluate("(v) => RetailSystem._fmt(v)", v)

        # -- (a) SAME LINE: a single-product cart, so its ENTIRE saving can
        # only ever land in ONE bucket under winner-takes-all -- proven BOTH
        # directions (promo strictly beats manual; manual strictly beats
        # promo), per ENGINEERING.md's "prove both directions of anything
        # that both denies and allows".
        for manual_pct, promo_wins, label in ((5, True, "promo wins"), (30, False, "manual wins")):
            r = run_case([PID_1], manual_pct)
            if promo_wins:
                assert r["till_promo_shown"] and not r["till_disc_shown"], (
                    f"(a same line, {label}): till rows shown promo={r['till_promo_shown']} "
                    f"disc={r['till_disc_shown']}, expected Promotions only")
                assert r["relay_disc"] is None, (
                    f"(a same line, {label}): relay 'discount' leaked as {r['relay_disc']!r}, expected null "
                    f"(the manual % did not win this line)")
                assert r["receipt_bd"]["discount"] < r["half_minor"], (
                    f"(a same line, {label}): receipt discount bucket is {r['receipt_bd']['discount']}, "
                    f"expected ~0 -- the server's own applied_promotion attribution disagrees with the till")
                till_text, relay_val, receipt_val = r["till_promo_text"], r["relay_promo"], r["receipt_bd"]["promotions"]
            else:
                assert r["till_disc_shown"] and not r["till_promo_shown"], (
                    f"(a same line, {label}): till rows shown promo={r['till_promo_shown']} "
                    f"disc={r['till_disc_shown']}, expected Discount only")
                assert r["relay_promo"] is None, (
                    f"(a same line, {label}): relay 'promotions' leaked as {r['relay_promo']!r}, expected null "
                    f"(the promotion did not strictly win this line -- a tie or a loss both go to Discount)")
                assert r["receipt_bd"]["promotions"] < r["half_minor"], (
                    f"(a same line, {label}): receipt promotions bucket is {r['receipt_bd']['promotions']}, "
                    f"expected ~0 -- the server's own applied_promotion attribution disagrees with the till")
                till_text, relay_val, receipt_val = r["till_disc_text"], r["relay_disc"], r["receipt_bd"]["discount"]

            expected_text = "-" + fmt(receipt_val)
            assert till_text.strip() == expected_text, (
                f"(a same line, {label}): till shows {till_text!r}, the receipt's own server-derived "
                f"breakdown expects {expected_text!r} -- the till's PREVIEW disagrees with the sale it is "
                f"about to ring")
            assert relay_val == fmt(receipt_val), (
                f"(a same line, {label}): relay payload shows {relay_val!r}, receipt-derived expects "
                f"{fmt(receipt_val)!r}")

        # -- (b) DIFFERENT LINES: Widget (promo, wins its own line) + Gadget
        # (no promotion at all) -- both buckets nonzero at once, each traced
        # back to its OWN line, never blended.
        r = run_case([PID_1, PID_2], 10)
        assert r["till_promo_shown"] and r["till_disc_shown"], (
            f"(b different lines): expected both rows shown, got promo={r['till_promo_shown']} "
            f"disc={r['till_disc_shown']}")
        assert r["receipt_bd"]["promotions"] > r["half_minor"] and r["receipt_bd"]["discount"] > r["half_minor"], (
            f"(b different lines): receipt buckets are {r['receipt_bd']} -- expected both nonzero")

        expected_promo_text = "-" + fmt(r["receipt_bd"]["promotions"])
        expected_disc_text = "-" + fmt(r["receipt_bd"]["discount"])
        assert r["till_promo_text"].strip() == expected_promo_text, (
            f"(b different lines): till Promotions {r['till_promo_text']!r} != receipt-derived "
            f"{expected_promo_text!r}")
        assert r["till_disc_text"].strip() == expected_disc_text, (
            f"(b different lines): till Discount {r['till_disc_text']!r} != receipt-derived "
            f"{expected_disc_text!r}")
        assert r["relay_promo"] == fmt(r["receipt_bd"]["promotions"]), (
            f"(b different lines): relay promotions {r['relay_promo']!r} != receipt-derived "
            f"{fmt(r['receipt_bd']['promotions'])!r}")
        assert r["relay_disc"] == fmt(r["receipt_bd"]["discount"]), (
            f"(b different lines): relay discount {r['relay_disc']!r} != receipt-derived "
            f"{fmt(r['receipt_bd']['discount'])!r}")

        identity = r["totals"]["subtotal"] - r["totals"]["promotions"] - r["totals"]["manualDiscount"] + r["totals"]["tax"]
        assert abs(identity - r["totals"]["total"]) < MONEY_EPS, (
            f"(b different lines) till identity: {identity} != {r['totals']['total']}")

        page.fill("#pos-disc", "0")
        page.evaluate("() => RetailSystem._clearCart()")
        go_pos(page)

    check("T2 (item #8): Promotions/Discount attribution is IDENTICAL across the till preview, the relay "
          "payload and the receipt -- both directions on a single shared line (promo wins / manual wins) "
          "and split correctly across two different lines", item8_t2_cross_surface_attribution)

    # =========================================================================
    # ROUND 2, till R2 (item #8/I-2, critical) -- MONEY IDENTITY AT 3DP.
    # T2 above already proves till/relay/receipt agree with EACH OTHER, but
    # every fixture it uses (PID_1=10, PID_2=5, whole-number prices) never
    # crosses a real ROUND_HALF_UP boundary, so a till that summed unrounded
    # full-precision figures and rounded only the invoice-level total at the
    # end could still pass T2 while disagreeing with the SERVER by a fil --
    # exactly the defect this round's adversarial review measured. These two
    # fixtures use fractional (3dp) prices specifically because they DO cross
    # that boundary, and assert the till's PREVIEW total/discount against the
    # real, completed sale's own persisted figures -- not just against
    # itself.
    # =========================================================================
    def item8_money_identity_at_3dp():
        pid_2500 = create_product("R2 Money 2.500", "R2-MONEY-1", 2.500)
        _, patch_body = api(page, "PATCH", f"/api/sub/retail/products/{pid_2500}", {"tax_rate": 16})
        assert patch_body.get("status") == "success", f"could not set tax_rate on R2 Money 2.500: {patch_body}"

        # (a) single line, 2.500 @ 7.5% manual, 16% tax: gross 2.500,
        # discount round(2.500*0.075)=0.188, taxable 2.312,
        # tax round(2.312*0.16)=0.370, total 2.500-0.188+0.370=2.682 --
        # the UNROUNDED formula this pass replaced gave 2.683 here (measured
        # live before the fix, quoted in this item's own evidence).
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", pid_2500)
        page.wait_for_timeout(300)
        page.fill("#pos-disc", "7.5")
        page.wait_for_timeout(300)

        till_total_a = page.evaluate("() => RetailSystem._currentTotals.total")
        expected_a = 2.682
        assert abs(till_total_a - expected_a) < 1e-9, (
            f"(a) till preview total is {till_total_a}, expected exactly {expected_a} "
            f"(2.500 - round(2.500*0.075) + round((2.500-0.188)*0.16)) -- the unrounded formula this "
            f"pass replaced gave 2.683 here")

        page.select_option("#pos-customer", CUST_ID)
        page.click('.pos-pay-btn[data-method="credit"]')
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.wait_for_timeout(250)
        receipt_total_a = page.evaluate("() => RetailSystem._lastSaleData.total")
        assert abs(receipt_total_a - expected_a) < 1e-9, (
            f"(a) receipt/server total is {receipt_total_a}, expected {expected_a} -- if this is 2.683, "
            f"the SERVER has the bug, not the till; if this is 2.682 but till_total_a above was 2.683, "
            f"the till disagrees with the sale it just rang")
        assert abs(till_total_a - receipt_total_a) < 1e-9, (
            f"(a) till preview ({till_total_a}) and receipt ({receipt_total_a}) disagree by "
            f"{abs(till_total_a - receipt_total_a)} -- the till showed one figure and charged another")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        # (c) two lines at 1.250 each, 15% manual, NO tax (isolates the
        # discount rounding from the tax rounding): each line's own discount
        # rounds to round(1.250*0.15)=0.188, so the COMBINED Discount is
        # 0.376 -- the old uniform `subtotal*0.15` formula gave 0.375
        # (2.500*0.15 rounded once), a fil short of what the receipt (built
        # from two independently-rounded per-line discounts) actually shows.
        pid_1250a = create_product("R2 Money 1.250 A", "R2-MONEY-2A", 1.250)
        pid_1250b = create_product("R2 Money 1.250 B", "R2-MONEY-2B", 1.250)
        api(page, "PATCH", f"/api/sub/retail/products/{pid_1250a}", {"tax_rate": 0})
        api(page, "PATCH", f"/api/sub/retail/products/{pid_1250b}", {"tax_rate": 0})
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", pid_1250a)
        page.evaluate("(id) => RetailSystem._addToCart(id)", pid_1250b)
        page.wait_for_timeout(300)
        page.fill("#pos-disc", "15")
        page.wait_for_timeout(300)

        till_disc_c = page.evaluate("() => RetailSystem._currentTotals.manualDiscount")
        expected_disc_c = 0.376
        assert abs(till_disc_c - expected_disc_c) < 1e-9, (
            f"(c) till preview Discount is {till_disc_c}, expected exactly {expected_disc_c} -- "
            f"2*round(1.250*0.15). The old uniform-baseline formula gave 0.375 here "
            f"(round(2.500*0.15) once) -- a fil short of what two independently-rounded per-line "
            f"discounts sum to")

        page.select_option("#pos-customer", CUST_ID)
        page.click('.pos-pay-btn[data-method="credit"]')
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.wait_for_timeout(250)
        receipt_bd_c = page.evaluate("() => RetailSystem._saleDiscountBreakdown(RetailSystem._lastSaleData)")
        assert abs(receipt_bd_c["discount"] - expected_disc_c) < 1e-9, (
            f"(c) receipt-derived Discount is {receipt_bd_c['discount']}, expected {expected_disc_c}")
        assert abs(till_disc_c - receipt_bd_c["discount"]) < 1e-9, (
            f"(c) till preview Discount ({till_disc_c}) and receipt ({receipt_bd_c['discount']}) disagree")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        page.fill("#pos-disc", "0")
        page.evaluate("() => RetailSystem._clearCart()")
        go_pos(page)

    check("item #8/I-2, money identity at 3dp: a 2.500 line at 7.5% manual with 16% tax rings 2.682, not "
          "the unrounded formula's 2.683, and two 1.250 lines at 15% manual combine to a Discount of "
          "0.376, not the old uniform formula's 0.375 -- till preview matches the receipt exactly in "
          "both cases", item8_money_identity_at_3dp)

    # =========================================================================
    # ROUND 3 (item #8/I-2, medium -- verifier finding): the two scenarios
    # above prove the SUMMARY totals (Subtotal/Discount/Tax/Total) are
    # money-identical at 3dp; neither ever reads a single LINE's own
    # displayed amount. That is exactly where the round-3 defect lived:
    # the receipt modal and the printed slip both showed each line's raw
    # `line_total` (calculate_line's TAXABLE amount, which shifts with
    # tax_calculation_mode) instead of the till's own gross/gross-minus-
    # PROMOTION-only figure -- so under after_discount mode a manually
    # discounted line printed net of its own discount while the Discount
    # row below ALSO subtracted that same amount from a gross Subtotal
    # (lines never summed to the printed Subtotal), and under
    # before_discount mode a promoted line printed at full price while the
    # till showed it struck through. This scenario proves, in BOTH tax
    # modes, on a cart with ONE promoted line and ONE manually-discounted
    # line together (the exact combination the defect's own evidence
    # used): the till's cart row, the receipt modal and the printed slip
    # show the IDENTICAL final amount per line, and the receipt lines sum
    # to Subtotal minus Promotions ONLY -- never minus the manual Discount
    # too, matching _renderCart's own "a manual discount is explained by
    # the Discount row, never per line" rule.
    # =========================================================================
    def item8_receipt_lines_match_till_and_sum_r3():
        def parse_money(s):
            m = re.search(r"[\d.]+$", (s or "").replace(",", ""))
            return float(m.group()) if m else None

        def last_child_texts(sel):
            return page.eval_on_selector_all(
                sel, "els => els.map(e => (e.lastChild ? e.lastChild.textContent : '') .trim())")

        pid_promo_b = create_product("R3 Money Line Promo", "R3-ML-PROMO", 12)
        pid_plain_b = create_product("R3 Money Line Plain", "R3-ML-PLAIN", 8)
        for pid in (pid_promo_b, pid_plain_b):
            _, patch_body = api(page, "PATCH", f"/api/sub/retail/products/{pid}", {"tax_rate": 16})
            assert patch_body.get("status") == "success", f"could not set tax_rate on {pid}: {patch_body}"
        _, promo_body = api(page, "POST", "/api/sub/retail/promotions", {
            "name": "R3 Money Line 20% off", "product_id": pid_promo_b, "discount_pct": 20,
        })
        assert promo_body.get("status") == "success", f"R3 money-line promotion was not created: {promo_body}"

        try:
            for mode in ("after_discount", "before_discount"):
                _, mode_body = api(page, "POST", "/api/sub/retail/settings/tax",
                                    {"tax_calculation_mode": mode})
                assert mode_body.get("status") == "success", f"[{mode}] could not set tax mode: {mode_body}"

                go_pos(page)
                page.evaluate("() => RetailSystem._clearCart()")
                # Promoted line first, manually-discounted (no promo) line
                # second -- the SAME two-line shape (one promo, one manual)
                # the round-3 evidence used, so both branches of
                # _receiptLineFigures's applied_promotion check are
                # exercised in one sale.
                page.evaluate("(id) => RetailSystem._addToCart(id)", pid_promo_b)
                page.evaluate("(id) => RetailSystem._addToCart(id)", pid_plain_b)
                page.wait_for_timeout(300)
                # 10% manual -- strictly below the 20% promotion, so the
                # promotion wins on pid_promo_b's line (applied_promotion
                # set) while the manual 10% wins on pid_plain_b's line
                # (no promotion configured for it at all).
                page.fill("#pos-disc", "10")
                page.wait_for_timeout(300)

                till_lines = last_child_texts("#pos-cart .pos-line-total")
                assert len(till_lines) == 2, f"[{mode}] expected 2 cart lines, got {till_lines}"

                page.select_option("#pos-customer", CUST_ID)
                page.click('.pos-pay-btn[data-method="credit"]')
                page.wait_for_timeout(200)
                page.click("#pos-checkout-btn")
                page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
                page.wait_for_timeout(300)

                receipt_lines = last_child_texts("#ret-receipt-dialog .rcpt-amt")
                assert len(receipt_lines) == 2, f"[{mode}] expected 2 receipt lines, got {receipt_lines}"
                assert till_lines == receipt_lines, (
                    f"[{mode}] till cart lines {till_lines} do not match receipt modal lines "
                    f"{receipt_lines} -- the customer paid one figure and the receipt shows another")

                page.click('#ret-receipt-dialog button:has-text("Print")')
                page.wait_for_timeout(500)
                print_lines = page.evaluate(
                    "() => { const f = document.getElementById('ret-print-frame'); "
                    "if (!f || !f.contentDocument) return []; "
                    "return Array.from(f.contentDocument.querySelectorAll('.rcpt-amt')).map(e => "
                    "(e.lastChild ? e.lastChild.textContent : '').trim()); }")
                assert print_lines == till_lines, (
                    f"[{mode}] printed slip lines {print_lines} do not match the till's own cart lines "
                    f"{till_lines}")

                subtotal = page.evaluate("() => RetailSystem._lastSaleData.subtotal")
                breakdown = page.evaluate(
                    "() => RetailSystem._saleDiscountBreakdown(RetailSystem._lastSaleData)")
                expected_sum = subtotal - breakdown["promotions"]
                actual_sum = sum(parse_money(x) for x in receipt_lines)
                assert abs(actual_sum - expected_sum) < MONEY_EPS, (
                    f"[{mode}] receipt lines sum to {actual_sum}, expected Subtotal ({subtotal}) minus "
                    f"Promotions ({breakdown['promotions']}) = {expected_sum} -- lines={receipt_lines}, "
                    f"breakdown={breakdown}")
                # The negative control this identity exists to catch: summing
                # against Subtotal minus BOTH Promotions and the manual
                # Discount (the old, buggy identity) must NOT also hold,
                # or this scenario could not tell the fix from the bug it
                # replaces -- a manual discount is real money off the
                # invoice, so the two totals only coincide when there is no
                # manual discount at all (never true in this fixture: 10%
                # is always applied to the plain line).
                buggy_sum_target = subtotal - breakdown["promotions"] - breakdown["discount"]
                assert abs(actual_sum - buggy_sum_target) > MONEY_EPS, (
                    f"[{mode}] receipt lines ({actual_sum}) also match the OLD buggy identity "
                    f"(Subtotal - Promotions - Discount = {buggy_sum_target}) -- this fixture's manual "
                    f"discount is not actually exercising the distinction this scenario means to prove")

                page.click('#ret-receipt-dialog button:has-text("New Sale")')
                page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
        finally:
            api(page, "POST", "/api/sub/retail/settings/tax", {"tax_calculation_mode": "after_discount"})
            page.fill("#pos-disc", "0")
            page.evaluate("() => RetailSystem._clearCart()")
            go_pos(page)

    check("item #8/I-2, round 3: with one promoted line and one manually-discounted line together, in "
          "BOTH tax_calculation_mode settings, the till cart row, the receipt modal and the printed slip "
          "show the identical final amount per line, and the receipt lines sum to Subtotal minus "
          "Promotions only (never also minus the manual Discount)",
          item8_receipt_lines_match_till_and_sum_r3)

    # =========================================================================
    # ITEM #14(d) -- a lone page title is not pushed to the header's far edge.
    # Proven directly against the live '#ret-styles' stylesheet with a
    # synthetic '.ret-hdr': a lone '.ret-title' must sit at the START edge
    # (mirrored under RTL), while a lone NON-title control still gets pushed
    # to the END the way the rule always meant to for a real control.
    # =========================================================================
    def item14d_title_not_pushed_to_end():
        def measure(lang):
            set_lang(page, lang)
            return page.evaluate("""
                () => {
                    const hdr1 = document.createElement('div'); hdr1.className = 'ret-hdr';
                    const title = document.createElement('h2'); title.className = 'ret-title'; title.textContent = 'X';
                    hdr1.appendChild(title); document.body.appendChild(hdr1);

                    const hdr2 = document.createElement('div'); hdr2.className = 'ret-hdr';
                    const btn = document.createElement('button'); btn.textContent = 'Y';
                    hdr2.appendChild(btn); document.body.appendChild(hdr2);

                    const r1 = hdr1.getBoundingClientRect(), rt = title.getBoundingClientRect();
                    const r2 = hdr2.getBoundingClientRect(), rb = btn.getBoundingClientRect();
                    hdr1.remove(); hdr2.remove();
                    return {
                        dir: document.documentElement.dir,
                        hdr1Left: r1.left, hdr1Right: r1.right, titleLeft: rt.left, titleRight: rt.right,
                        hdr2Left: r2.left, hdr2Right: r2.right, btnLeft: rb.left, btnRight: rb.right,
                    };
                }
            """)

        m_ltr = measure("en")
        assert m_ltr["dir"] == "ltr", f"expected ltr for English, got {m_ltr['dir']}"
        title_from_start = m_ltr["titleLeft"] - m_ltr["hdr1Left"]
        assert title_from_start < 48, (
            f"[LTR] the lone page title sits {title_from_start:.1f}px from its header's start edge, "
            f"expected it pinned to the start (< 48px), not pushed to the end")
        btn_from_end = m_ltr["hdr2Right"] - m_ltr["btnRight"]
        assert btn_from_end < 48, (
            f"[LTR] a lone NON-title control sits {btn_from_end:.1f}px from the end edge -- "
            f"the rule must still push a real lone control to the end")

        m_rtl = measure("ar")
        assert m_rtl["dir"] == "rtl", f"expected rtl for Arabic, got {m_rtl['dir']}"
        title_from_start_rtl = m_rtl["hdr1Right"] - m_rtl["titleRight"]
        assert title_from_start_rtl < 48, (
            f"[RTL] the lone page title sits {title_from_start_rtl:.1f}px from its header's start edge "
            f"(the RIGHT edge under RTL), expected < 48px")
        btn_from_end_rtl = m_rtl["btnLeft"] - m_rtl["hdr2Left"]
        assert btn_from_end_rtl < 48, (
            f"[RTL] a lone NON-title control sits {btn_from_end_rtl:.1f}px from the end edge "
            f"(the LEFT edge under RTL) -- the rule must still push a real lone control to the end")

        set_lang(page, "en")

    check("item #14(d): a lone .ret-title stays at the header's start edge in both LTR and RTL; "
          "a lone non-title control still gets pushed to the end", item14d_title_not_pushed_to_end)

    # =========================================================================
    # ITEM #14(d), review fix -- the scenario above proves the CSS rule
    # against a hand-built '.ret-hdr'/'.ret-title' pair appended to <body>.
    # That is a real, useful proof of the SELECTOR, but it can never catch a
    # regression in _renderCapabilityRestricted's own markup (subsystem-
    # retail.js) -- an extra wrapper element around the title, say -- because
    # nothing ever asks that function to actually run. This scenario drives
    # the REAL code path instead: a real capability-restricted screen,
    # rendered by the real _renderBranches -> _renderCapabilityRestricted,
    # inside the real #sub-content the shell renders into.
    #
    # Forcing the restriction: `SubsystemApp.capabilities = []` is not a
    # fabricated shortcut, it is the EXACT shape a real restricted session
    # produces. _adoptSessionCapabilities's own comment says why an empty
    # array is kept distinct from `null`: "[] means deny everything this
    # list doesn't name". Setting it directly here is simulating the server
    # having sent that real, valid session shape -- see app-shell.js:515-532
    # -- not inventing a new one. `hasCapability('retail.employees')` then
    # answers false exactly as it would for a cashier/manager session, and
    # `_renderBranches` takes its real, already-shipped restricted branch.
    # =========================================================================
    def item14d_capability_restricted_real_render():
        saved_caps = page.evaluate("() => SubsystemApp.capabilities")
        try:
            page.evaluate("() => { SubsystemApp.capabilities = []; }")
            go(page, "branches", wait_ms=500)
            page.wait_for_selector("#sub-content .ret-hdr .ret-title", timeout=8000)

            # Prove this actually hit the RESTRICTED branch, not the real
            # Branches table (whose header carries an "+ Add Branch" button,
            # never a lone title) -- otherwise a passing assertion below
            # would prove nothing.
            add_branch_count = page.locator("#sub-content button:has-text('Add Branch')").count()
            assert add_branch_count == 0, (
                "the capability override did not take effect -- the real Branches "
                "table rendered instead of the restricted screen, so this scenario "
                "proved nothing")
            child_count = page.eval_on_selector("#sub-content .ret-hdr", "el => el.children.length")
            assert child_count == 1, (
                f"the real _renderCapabilityRestricted header has {child_count} children, "
                f"expected exactly 1 (the lone .ret-title) -- if this ever changes, the "
                f"synthetic scenario above stops matching production markup")

            hdr_box = page.eval_on_selector("#sub-content .ret-hdr", "el => el.getBoundingClientRect().toJSON()")
            title_box = page.eval_on_selector("#sub-content .ret-hdr .ret-title", "el => el.getBoundingClientRect().toJSON()")
            title_from_start = title_box["left"] - hdr_box["left"]
            assert title_from_start < 48, (
                f"[real render] the capability-restricted screen's own .ret-title sits "
                f"{title_from_start:.1f}px from its header's start edge -- pushed toward "
                f"the end edge instead of staying at the natural start, on the ACTUAL "
                f"_renderCapabilityRestricted markup (not the synthetic fixture above)")
        finally:
            # Restore the real session capabilities (never leave a later
            # scenario, or a crew appending after this one, running with a
            # fabricated empty grant list) and leave the till on POS.
            page.evaluate("(caps) => { SubsystemApp.capabilities = caps; }", saved_caps)
            go_pos(page)

    check("item #14(d), real render: a REAL capability-restricted screen (_renderBranches -> "
          "_renderCapabilityRestricted, not a synthetic div) keeps its lone .ret-title at the "
          "header's start edge", item14d_capability_restricted_real_render)

    # =========================================================================
    # PHASE 0 CREW C3c (till-3) -- items #11-till, #12-till, #13-client,
    # #15b/c/d/e-client, #17-client, #19 appended below, per contracts.md's
    # "append" instruction for this file. Nothing above this comment is
    # touched. Reuses the SAME running server/session/page C3a set up at the
    # top of this script -- a fresh AURA_APP_DATA per item's own instruction
    # means fresh per SCRIPT RUN (this whole file, boot to shutdown), not a
    # second server per crew's section.
    # =========================================================================
    set_lang(page, "en")
    set_theme(page, "light")
    page.set_viewport_size({"width": 1440, "height": 900})
    go_pos(page)

    # Shared fixtures for the #13/#15e scenarios below, created once at
    # module scope (same pattern PID_1..4/EXTRA_PIDS/PID_LONG already use)
    # so both a "before" and an "after" scenario can reference the SAME
    # product without Python's nested-function scoping getting in the way.
    _, _pre_default_prod = api(page, "POST", "/api/sub/retail/products", {
        "name": "Pre-Default Product", "sku": "P0-PREDEF", "sell_price": 9, "cost_price": 4,
        "category_id": cat_id, "unit": "pc",
    })
    PRE_DEFAULT_PID = ((_pre_default_prod or {}).get("data") or {}).get("id")
    assert PRE_DEFAULT_PID, f"item #13 baseline product was not created: {_pre_default_prod}"

    _, _avgcost_prod = api(page, "POST", "/api/sub/retail/products", {
        # cost_price deliberately far from the PO's unit_cost below, mirroring
        # item #15e's own bug report verbatim ("coffee received at 1.000
        # still shows 2.900").
        "name": "Avg Cost Coffee", "sku": "P0-AVGCOST", "sell_price": 5, "cost_price": 2.9,
        "category_id": cat_id, "unit": "pc",
    })
    PID_AVGCOST = ((_avgcost_prod or {}).get("data") or {}).get("id")
    assert PID_AVGCOST, f"item #15e avg-cost test product was not created: {_avgcost_prod}"

    # =========================================================================
    # ITEM #11(b)/(d) + #12-till -- interface I-1/I-3. The till's cart state
    # reaches the backend relay (debounced), carrying the shop's own name
    # (I-3's branding-or-signup fallback), and the "Customer screen" button's
    # dot moves grey -> amber -> green off the real /status poll.
    #
    # ORDER MATTERS in this block: the button/dot scenario runs FIRST, before
    # anything else in this crew's section ever calls GET .../customer-
    # display/state -- that GET is the ONLY thing that sets the server's
    # `_CUSTOMER_DISPLAY_LAST_SEEN` (customer_display_status, the till's own
    # /status poll, is explicitly read-only and never touches it -- see that
    # route's own docstring). Running the relay scenario first once made this
    # exact mistake: its own `since=0` GET marked the display "seen", so the
    # very next scenario's "amber right after opening, before anything has
    # polled" assertion found the dot already green -- a real fixture-orders-
    # the-bug-away trap (ENGINEERING.md failure shape 2), not an app defect.
    # Caught by running this file for real rather than trusting the code.
    # =========================================================================
    def item11_button_and_dot_state_machine():
        go_pos(page)
        btn_text = page.eval_on_selector("#pos-display-btn", "el => el.textContent")
        assert "Customer screen" in btn_text, f"the display button was not renamed to 'Customer screen': {btn_text!r}"

        dot_before = page.eval_on_selector("#pos-display-dot", "el => el.className")
        assert "pos-display-dot-amber" not in dot_before and "pos-display-dot-green" not in dot_before, (
            f"the dot is not grey (no amber/green modifier) before the display has ever been opened: {dot_before!r}")

        page.click("#pos-display-btn")
        page.wait_for_timeout(500)  # the first /status poll fires immediately on open
        dot_amber = page.eval_on_selector("#pos-display-dot", "el => el.className")
        assert "pos-display-dot-amber" in dot_amber, (
            f"the dot did not go amber right after opening, before any display has polled .../state: {dot_amber!r}")

        # The real signal customer_display_get_state uses to mark "seen" --
        # a second browser context/device would do exactly this on its own
        # 500-700ms short-poll (C6's side); simulated here via the same API.
        api(page, "GET", "/api/sub/retail/customer-display/state?since=0")
        page.evaluate("() => RetailSystem._pollCustomerDisplayStatus()")
        page.wait_for_timeout(300)
        dot_green = page.eval_on_selector("#pos-display-dot", "el => el.className")
        assert "pos-display-dot-green" in dot_green, (
            f"the dot did not go green once the display had polled .../state within 5s: {dot_green!r}")

    check("item #11(d): the button reads 'Customer screen'; its dot is grey before opening, amber right "
          "after opening, and green once the display has actually polled /status",
          item11_button_and_dot_state_machine)

    def item11_12_relay_carries_cart_and_shop_info():
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)
        page.wait_for_timeout(500)  # > the 150ms debounce in _postToCustomerDisplayRelay

        status, body = api(page, "GET", "/api/sub/retail/customer-display/state?since=0")
        assert status == 200, (
            f"the till's cart never reached the backend relay (GET .../state?since=0 -> {status}): {body}")
        assert (body.get("message") or {}).get("type") == "cart", (
            f"the relay's stored message is not a cart payload: {body}")

        shop = body.get("shop") or {}
        assert shop.get("name") == "P0 Till Test Co", (
            f"shop.name in the relay payload is {shop.get('name')!r}, expected the signup company name "
            f"(interface I-3's branding-or-signup fallback -- SubsystemApp._shopDisplayName())")
        assert shop.get("lang") == "en", f"shop.lang is {shop.get('lang')!r}, expected 'en' (the till's current language)"
        live_theme = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        assert shop.get("theme") == live_theme, f"shop.theme is {shop.get('theme')!r}, expected the live theme {live_theme!r}"

    check("item #11(b)/#12-till, interface I-1/I-3: the till's cart state reaches the backend relay "
          "(debounced), carrying shop {name (signup fallback), lang, theme}",
          item11_12_relay_carries_cart_and_shop_info)

    def item11_native_window_preferred_over_browser_open():
        result = page.evaluate("""
            () => {
                const origOpen = window.open;
                let openCalled = false;
                window.open = (...args) => { openCalled = true; return null; };
                let nativeCalled = false;
                window.pywebview = { api: { open_customer_display: () => { nativeCalled = true; } } };
                RetailSystem._openCustomerDisplay();
                window.open = origOpen;
                delete window.pywebview;
                return { nativeCalled, openCalled };
            }
        """)
        assert result["nativeCalled"], "window.pywebview.api.open_customer_display() exists but was not called"
        assert not result["openCalled"], "window.open() was called even though the native API exists -- it must be preferred"

    check("item #11(a): calls window.pywebview.api.open_customer_display() when it exists, never window.open()",
          item11_native_window_preferred_over_browser_open)

    def item11_toast_on_popup_blocked():
        page.evaluate("""
            () => {
                window.__p0OrigOpen = window.open;
                window.open = () => null;
                RetailSystem._openCustomerDisplay();
            }
        """)
        page.wait_for_timeout(300)
        toast_texts = page.eval_on_selector_all('[role="alert"], [role="status"]', "els => els.map(e => e.textContent)")
        page.evaluate("() => { window.open = window.__p0OrigOpen; delete window.__p0OrigOpen; }")
        assert any("pop-up" in t.lower() or "customer screen" in t.lower() for t in toast_texts if t), (
            f"no toast reported the blocked pop-up. Toasts currently on screen: {toast_texts}")

    check("item #11(a): shows a toast when window.open() returns null (popup blocked, no native API)",
          item11_toast_on_popup_blocked)

    # =========================================================================
    # ITEM #11, interface I-2 -- REVIEWER FIX (2026-09-27, critical), THEN
    # SUPERSEDED (round 2, till R2, I-2/T2 "the display line still shows the
    # promotion" finding).
    #
    # The 2026-09-27 reviewer fix made `_customerDisplayCartPayload` decide
    # with a bare `promo ?` (a promotion RESOLVES for the line) instead of
    # _recalc's own "best price wins" max -- so this test originally proved
    # that a 50% manual discount, bigger than a 20% promotion, still relayed
    # the PROMOTION-only total (8.000) and a non-null originalLineTotal
    # (10.000). That is now recognised as the SAME bug T2 already fixed for
    # the CHARGE: `promo ?` is "a promotion exists", not "a promotion WON",
    # and the round-2 adversarial review measured exactly this case live
    # (Widget 10.000, 20% promotion, manual 30% -- manual wins) showing a
    # struck 'JD 10.000'/'JD 8.000' on the display while the actual charge
    # put the ENTIRE saving in the Discount bucket -- a total a shopper could
    # not reconcile (8.000 - 3.000 + tax != the shown Total). The server's
    # own `applied_promotion` is null on a manual-wins line too.
    #
    # This is a DELIBERATE, DOCUMENTED behaviour change, not a weakened test
    # (ENGINEERING.md rule 2): the OLD assertion below ("manual-wins still
    # shows the promo-only total") is GONE and can no longer be caught by
    # this file -- it is now WRONG behaviour by design. What replaces it
    # proves BOTH directions of the new winner-takes-all-per-line rule
    # (ENGINEERING.md's "prove both directions of anything that both denies
    # and allows"): promo wins (manual 10% < promo 20%) still relays the
    # post-promotion total, exactly as before; manual wins (manual 50% >
    # promo 20%) now relays the line's OWN gross, untouched, with
    # originalLineTotal null -- the manual saving stays where I-2 always
    # said it belongs, the separate invoice-level `discount` field, never
    # smeared into a per-line figure that cannot be added up against the
    # Total. Reuses item #8's still-active "P0 Test Promo" (20% on PID_1)
    # rather than minting a second promotion this scenario would have to
    # clean up.
    # =========================================================================
    def item11_relay_line_total_winner_takes_all():
        go_pos(page)

        # (a) PROMO WINS: manual 10% < the 20% promotion.
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)  # Widget, 10, 20% promo (item #8's fixture)
        page.fill("#pos-disc", "10")
        page.wait_for_timeout(500)     # > the 150ms relay debounce

        status, body = api(page, "GET", "/api/sub/retail/customer-display/state?since=0")
        assert status == 200, f"relay GET failed: {status} {body}"
        line = ((body.get("message") or {}).get("items") or [None])[0]
        assert line, f"relay payload carried no cart items: {body}"

        expected_promo_only = page.evaluate("(v) => RetailSystem._fmt(v)", 10 * (1 - 0.20))
        expected_original = page.evaluate("(v) => RetailSystem._fmt(v)", 10)
        assert line.get("lineTotal") == expected_promo_only, (
            f"(a) promo wins: relay items[0].lineTotal is {line.get('lineTotal')!r}, expected "
            f"{expected_promo_only!r} (post-promotion, promo wins)")
        assert line.get("originalLineTotal") == expected_original, (
            f"(a) promo wins: relay items[0].originalLineTotal is {line.get('originalLineTotal')!r}, "
            f"expected the raw pre-promo line total {expected_original!r}")

        # Round 2, till R2: the TILL's own cart row, not just the relay --
        # "_renderCart's own 'Promo tag'/struck-price shape" the defect's
        # fix text names explicitly ("Use the same predicate for
        # _renderCart's tag and struck price"). #pos-disc's oninput now
        # re-renders the cart (see that input's own markup comment), so this
        # is already live by the time page.fill() above returns.
        assert page.locator("#pos-cart .pos-price-orig").count() > 0, (
            "(a) promo wins: the till's own cart row shows no struck-through original price")
        assert page.locator("#pos-cart .pos-promo-tag").count() > 0, (
            "(a) promo wins: the till's own cart row shows no 'Promo' tag")

        # (b) MANUAL WINS: manual 50% > the 20% promotion -- the winner-
        # takes-all-per-line rule this test used to get backwards.
        page.fill("#pos-disc", "50")
        page.wait_for_timeout(500)

        status, body = api(page, "GET", "/api/sub/retail/customer-display/state?since=0")
        assert status == 200, f"relay GET failed: {status} {body}"
        line = ((body.get("message") or {}).get("items") or [None])[0]
        assert line, f"relay payload carried no cart items: {body}"

        expected_untouched = page.evaluate("(v) => RetailSystem._fmt(v)", 10)
        expected_if_promo_leaks_in = page.evaluate("(v) => RetailSystem._fmt(v)", 10 * (1 - 0.20))
        assert line.get("lineTotal") == expected_untouched, (
            f"(b) manual wins: relay items[0].lineTotal is {line.get('lineTotal')!r}, expected "
            f"{expected_untouched!r} (the line's own gross, untouched) -- {expected_if_promo_leaks_in!r} "
            f"would mean a promotion that LOST is still shown as though it won")
        assert line.get("originalLineTotal") is None, (
            f"(b) manual wins: relay items[0].originalLineTotal is {line.get('originalLineTotal')!r}, "
            f"expected None -- the server's own applied_promotion is null on a manual-wins line too")

        # Round 2, till R2: same TILL-side proof as (a) above, other
        # direction -- ENGINEERING.md's "prove both directions". This is
        # also the specific staleness bug the defect named: "#pos-disc
        # input runs only _recalc, not _renderCart, so at charge time the
        # till line still read 'JD 10.000 JD 8.000'" -- if that were still
        # true, this cart would still show the promo styling from case (a)
        # above, never having re-rendered when the discount changed to 50.
        assert page.locator("#pos-cart .pos-price-orig").count() == 0, (
            "(b) manual wins: the till's own cart row still shows a struck-through price -- "
            "either the winner predicate or the #pos-disc live re-render is stale")
        assert page.locator("#pos-cart .pos-promo-tag").count() == 0, (
            "(b) manual wins: the till's own cart row still shows a 'Promo' tag despite the manual "
            "discount winning this line")

        page.fill("#pos-disc", "0")
        page.evaluate("() => RetailSystem._clearCart()")
        go_pos(page)

    check("item #11, interface I-2/T2 (winner-takes-all, both directions): the customer-display relay's "
          "items[].lineTotal reflects the promotion ONLY when it actually wins the line (manual 10% < "
          "promo 20%), and the line's own untouched gross with a null originalLineTotal when the manual "
          "discount wins instead (manual 50% > promo 20%)",
          item11_relay_line_total_winner_takes_all)

    # =========================================================================
    # ITEM #13, interface I-4 -- Add Product prefilled with the shop's own
    # default GST rate; the Settings field that sets it; existing products
    # untouched.
    # =========================================================================
    def item13_default_tax_rate_prefill_and_settings():
        # (a) With no default set, Add Product prefills 0.
        api(page, "POST", "/api/sub/retail/settings/tax", {"default_tax_rate": None})
        unset_val = page.evaluate(
            "async () => { await RetailSystem._openAddProduct(); "
            "const v = document.getElementById('pm-tax').value; "
            "RetailSystem._closeModalOverlay(document.getElementById('ret-prod-modal')); return v; }")
        assert unset_val in ("0", ""), f"with no default set, Add Product prefilled Tax Rate % with {unset_val!r}, expected 0"
        page.wait_for_timeout(200)

        # (b) With the default set to 16, Add Product prefills 16.
        _, set_body = api(page, "POST", "/api/sub/retail/settings/tax", {"default_tax_rate": 16})
        assert set_body.get("status") == "success", f"could not set default_tax_rate via the API: {set_body}"
        set_val = page.evaluate(
            "async () => { await RetailSystem._openAddProduct(); "
            "const v = document.getElementById('pm-tax').value; "
            "RetailSystem._closeModalOverlay(document.getElementById('ret-prod-modal')); return v; }")
        assert set_val == "16", f"with default_tax_rate=16, Add Product prefilled Tax Rate % with {set_val!r}, expected '16'"
        page.wait_for_timeout(200)

        # (c) The Settings card itself: loads the persisted value, and Save
        # writes a new one through the real UI (not the API shortcut above).
        go(page, "admin-center")
        page.wait_for_selector("#default-tax-card", timeout=10000)
        page.wait_for_timeout(500)
        loaded_val = page.eval_on_selector("#tax-default-rate", "el => el.value")
        assert loaded_val == "16", f"the Settings field did not load the persisted default (16), got {loaded_val!r}"

        page.fill("#tax-default-rate", "8")
        page.click("#tax-default-rate-save-btn")
        page.wait_for_timeout(700)
        _, verify_body = api(page, "GET", "/api/sub/retail/settings/tax")
        saved_rate = (verify_body.get("data") or {}).get("default_tax_rate")
        assert saved_rate == 8, f"the Settings Save button did not persist 8, server now has {saved_rate!r}"

        # (d) Existing products are untouched by any of the above -- the
        # product created BEFORE any default existed is still 0.
        _, list_body = api(page, "GET", "/api/sub/retail/products")
        pre_row = next((p for p in (list_body.get("data") or []) if p["id"] == PRE_DEFAULT_PID), None)
        assert pre_row is not None, "the pre-default baseline product went missing"
        assert pre_row["tax_rate"] == 0, (
            f"the pre-existing product's tax_rate changed to {pre_row['tax_rate']} just because the shop's "
            f"default changed later -- item #13's own wording: 'Existing products are untouched'")

        # Restore a stable default for anything running after this scenario.
        api(page, "POST", "/api/sub/retail/settings/tax", {"default_tax_rate": 16})
        go_pos(page)

    check("item #13, interface I-4: Add Product prefills 0 with no default set and the real default once "
          "one is set; the Settings card loads and saves it through the real UI; an existing product's "
          "tax_rate is never touched", item13_default_tax_rate_prefill_and_settings)

    # =========================================================================
    # ITEM #15b/c -- cheque tiles/table through _money(), .num end-alignment.
    # =========================================================================
    def item15bc_cheque_money_and_num_class():
        _, sup_body = api(page, "POST", "/api/sub/retail/suppliers", {"name": "P0 Cheque Supplier"})
        sup_id = ((sup_body or {}).get("data") or {}).get("id")
        assert sup_id, f"supplier for the cheque test was not created: {sup_body}"
        _, cheque_body = api(page, "POST", "/api/sub/retail/cheques", {
            "direction": "out", "party_id": sup_id, "amount": 123.456,
            "cheque_number": "P0CHQ1", "due_date": "2027-01-01",
        })
        assert cheque_body.get("status") == "success", f"cheque was not recorded: {cheque_body}"

        go(page, "cheques")
        page.wait_for_selector("#cheque-table", timeout=10000)
        page.wait_for_timeout(500)
        page.evaluate("() => RetailSystem._setChequeDirection('out')")
        page.wait_for_timeout(600)

        # (b) the KPI tile shows the real pending amount (123.456, the cheque
        # just recorded, status 'pending' by default) through _setMoney
        # (this._currencyPrefix() + real decimals) -- NOT a bare number with
        # no currency mark, which is what a reverted `el.textContent =
        # String(v)` would still show a real, nonzero digit for. Checking
        # only "not a bare 0" would pass even on the un-fixed code (the raw
        # total IS 123.456, not 0) -- the decisive proof is the MARK, so this
        # asserts the tile is not purely numeric.
        on_hand_text = page.eval_on_selector('[data-k="on_hand_total"]', "el => el.textContent")
        assert on_hand_text and "123.456" in on_hand_text, (
            f"the 'On Hand' cheque KPI tile is {on_hand_text!r}, expected it to contain the pending "
            f"cheque's amount (123.456)")
        stripped_numeric = on_hand_text.strip().replace(',', '')
        assert not stripped_numeric.replace('.', '', 1).replace('-', '', 1).isdigit(), (
            f"the 'On Hand' KPI tile is a bare number with no currency mark at all: {on_hand_text!r}")

        # The row's own Amount cell shows the real amount, formatted with the
        # shop's currency mark and 3 decimals -- not a bare number.
        row_amount_text = page.eval_on_selector(
            '#cheque-table tbody tr:has-text("P0CHQ1") td.num', "el => el.textContent")
        assert "123.456" in row_amount_text, (
            f"the cheque row's Amount cell is {row_amount_text!r}, expected it to contain '123.456'")
        assert row_amount_text.strip() != "123.456", (
            f"the cheque amount has no currency mark at all -- still a bare number: {row_amount_text!r}")

        # (c) .num is actually end-aligned, computed, not merely present as a
        # class name -- text-align:end resolves to 'right' in this LTR page.
        computed_align = page.eval_on_selector(
            '#cheque-table tbody tr:has-text("P0CHQ1") td.num', "el => getComputedStyle(el).textAlign")
        assert computed_align in ("right", "end"), (
            f"the Amount cell's computed text-align is {computed_align!r}, expected right/end")

    check("item #15b/c: cheque KPI tiles and the row Amount column render through _money()/_setMoney "
          "(never a bare '0'), and .num actually computes to end-aligned text",
          item15bc_cheque_money_and_num_class)

    # =========================================================================
    # ITEM #15d, interface I-5 -- Closing Dr/Cr columns with equal totals,
    # relabelled turnover columns, an as-of date control that actually
    # changes what is included.
    # =========================================================================
    def item15d_trial_balance_closing_columns_and_as_of():
        # A sale happens right now, so "today" and "yesterday" as_of filters
        # are guaranteed to differ -- the concrete, live proof that the as_of
        # control actually changes what is included, not just that the
        # column exists.
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)
        page.wait_for_timeout(300)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        import datetime
        today_str = datetime.date.today().isoformat()
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

        _, today_body = api(page, "GET", f"/api/sub/retail/reports/trial-balance?as_of={today_str}")
        _, yday_body = api(page, "GET", f"/api/sub/retail/reports/trial-balance?as_of={yesterday_str}")
        today_data = today_body.get("data") or {}
        yday_data = yday_body.get("data") or {}
        assert today_data.get("total_debit", 0) > yday_data.get("total_debit", 0), (
            f"as_of={yesterday_str} did not exclude today's sale: today total_debit="
            f"{today_data.get('total_debit')}, yesterday-filtered total_debit={yday_data.get('total_debit')}")

        # Closing Dr/Cr totals are equal, on BOTH responses -- "equal totals"
        # is item #15d's own literal requirement, checked directly against
        # the real server response, not assumed from the arithmetic comment.
        for label, data in (("today", today_data), ("yesterday", yday_data)):
            cd, cc = data.get("closing_debit"), data.get("closing_credit")
            assert cd is not None and cc is not None, f"[{label}] closing_debit/closing_credit missing from the response: {data}"
            assert abs(cd - cc) < MONEY_EPS, f"[{label}] closing_debit ({cd}) != closing_credit ({cc})"

        # Now the CLIENT: table has the two new columns, relabelled turnover
        # headers, and the "As of dd/mm/yyyy · All branches" line.
        go(page, "trial-balance")
        page.wait_for_selector("#tb-table", timeout=10000)
        page.wait_for_timeout(500)

        header_texts = page.eval_on_selector_all("#tb-table thead th", "els => els.map(e => e.textContent.trim())")
        assert "Closing Dr" in header_texts, f"table header is missing 'Closing Dr': {header_texts}"
        assert "Closing Cr" in header_texts, f"table header is missing 'Closing Cr': {header_texts}"
        assert "Total debits" in header_texts, f"turnover column was not relabelled 'Total debits': {header_texts}"
        assert "Total credits" in header_texts, f"turnover column was not relabelled 'Total credits': {header_texts}"
        assert "Debit Amount" not in header_texts, f"the old 'Debit Amount' label is still shown: {header_texts}"

        as_of_input_val = page.eval_on_selector("#tb-as-of", "el => el.value")
        assert as_of_input_val == today_str, f"the as-of control did not default to today ({today_str}), got {as_of_input_val!r}"

        as_of_line = page.eval_on_selector("#tb-as-of-line", "el => el.textContent")
        dd, mm, yyyy = today_str.split("-")[2], today_str.split("-")[1], today_str.split("-")[0]
        expected_dmy = f"{dd}/{mm}/{yyyy}"
        assert expected_dmy in as_of_line, f"'As of {expected_dmy}' is not in the as-of line: {as_of_line!r}"
        assert "All branches" in as_of_line, f"'All branches' is not in the as-of line: {as_of_line!r}"

        # Changing the control to yesterday actually reloads and changes the total.
        page.fill("#tb-as-of", yesterday_str)
        page.dispatch_event("#tb-as-of", "change")
        page.wait_for_timeout(600)
        debit_after = page.eval_on_selector("#tb-total-debit", "el => el.textContent")
        assert debit_after != "—", f"changing as_of to yesterday left the client stuck on the loading placeholder: {debit_after!r}"

        # Restore for anything running after this scenario.
        page.fill("#tb-as-of", today_str)
        page.dispatch_event("#tb-as-of", "change")
        page.wait_for_timeout(500)

    check("item #15d, interface I-5: trial-balance as_of=yesterday excludes today's sale, closing_debit "
          "equals closing_credit, and the client shows Closing Dr/Cr columns, relabelled turnover headers "
          "and 'As of dd/mm/yyyy · All branches'", item15d_trial_balance_closing_columns_and_as_of)

    # =========================================================================
    # V5/#15d client (round 3, medium -- verifier finding): _loadTrialBalance
    # used to read sl.ar_subledger/ar_ties (and the ap side) UNCONDITIONALLY
    # -- even when the server marks the comparison `sl.applicable === false`,
    # which general_ledger.py's subledger_tie() still answers with a
    # FABRICATED pair (ar_subledger mirroring ar_gl, ar_ties=true) for
    # backward compatibility with callers that have never branched on that
    # key (see that function's own docstring). This screen was one of them.
    # A past as_of with any later-dated journal -- which by this point in a
    # real shop's life is ordinary daily usage, not a contrived edge case --
    # painted a green 'Ties' badge over a number that had never actually
    # been compared to anything. Measured live (round-3 verifier, live_tb.py
    # on real Chromium): real customers.credit_balance sum 10.0, screen
    # showed 'Customer Balances (Subledger) JD 0.000 Ties'.
    # =========================================================================
    def item_v5_trial_balance_subledger_not_applicable_r3():
        # A fresh sale guarantees at least one gl_journals row dated after
        # yesterday exists, regardless of what earlier scenarios in this
        # suite have or have not already posted (ENGINEERING.md failure
        # shape 2: never assume state another scenario happened to leave).
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)
        page.wait_for_timeout(300)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        import datetime
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        today_str = datetime.date.today().isoformat()

        _, yday_body = api(page, "GET", f"/api/sub/retail/reports/trial-balance?as_of={yesterday_str}")
        yday_sl = (yday_body.get("data") or {}).get("subledger") or {}
        assert yday_sl.get("applicable") is False, (
            f"fixture assumption failed -- the server did not mark as_of={yesterday_str} inapplicable "
            f"after a sale posted today: {yday_sl}")

        go(page, "trial-balance")
        page.wait_for_selector("#tb-table", timeout=10000)
        page.wait_for_timeout(500)
        page.fill("#tb-as-of", yesterday_str)
        page.dispatch_event("#tb-as-of", "change")
        page.wait_for_timeout(700)

        sub_html = page.eval_on_selector("#tb-subledger", "el => el.innerHTML")
        sub_text = page.eval_on_selector("#tb-subledger", "el => el.textContent")
        assert "Not comparable for this date" in sub_text, (
            f"#tb-subledger does not show the 'not applicable' message for a past as_of with a later "
            f"journal: {sub_text!r}")
        assert "Ties" not in sub_text and "Does Not Tie" not in sub_text, (
            f"#tb-subledger still shows a Ties/Does Not Tie badge when the server marked the "
            f"comparison inapplicable: {sub_text!r}")
        assert "ret-badge" not in sub_html, (
            f"#tb-subledger still renders a badge element when the server marked the comparison "
            f"inapplicable: {sub_html!r}")

        # Restore for anything running after this scenario.
        page.fill("#tb-as-of", today_str)
        page.dispatch_event("#tb-as-of", "change")
        page.wait_for_timeout(500)
        go_pos(page)

    check("V5/#15d client, round 3: #tb-subledger shows 'Not comparable for this date' (no Ties/Does "
          "Not Tie badge) when the server marks the subledger comparison inapplicable, instead of a "
          "green Ties badge over a figure the server only mirrored from the GL side",
          item_v5_trial_balance_subledger_not_applicable_r3)

    # =========================================================================
    # ITEM #15e, interface I-6 -- Products shows avg_cost (labelled "Avg
    # cost"), a Stock value at cost KPI, and the PO picker prefers avg_cost.
    # =========================================================================
    def item15e_avg_cost_and_stock_value_and_po_default():
        _, po_body = api(page, "POST", "/api/sub/retail/purchase-orders", {
            "items": [{"product_id": PID_AVGCOST, "quantity": 10, "unit_cost": 1.0}],
        })
        po_id = ((po_body or {}).get("data") or {}).get("id")
        assert po_id, f"PO for the avg-cost test was not created: {po_body}"
        _, recv_body = api(page, "POST", f"/api/sub/retail/purchase-orders/{po_id}/receive")
        assert recv_body.get("status") == "success", f"receiving the PO failed: {recv_body}"

        # Confirm the SERVER's own avg_cost actually moved before asserting
        # anything about how this file renders it -- otherwise a client bug
        # and a server bug (out of this crew's scope) would look identical.
        _, list_body = api(page, "GET", "/api/sub/retail/products")
        server_row = next(p for p in (list_body.get("data") or []) if p["id"] == PID_AVGCOST)
        assert abs(server_row["avg_cost"] - 1.0) < 0.001, (
            f"server avg_cost is {server_row['avg_cost']}, expected 1.0 after receiving at unit_cost=1.0 "
            f"-- if this fails, the defect is server-side (C2's scope), not this crew's client render")
        assert abs(server_row["cost_price"] - 2.9) < 0.001, "cost_price must stay at its typed value (2.9)"

        go(page, "products")
        page.wait_for_selector("#prod-table", timeout=10000)
        page.wait_for_timeout(500)

        header_texts = page.eval_on_selector_all("#prod-table thead th", "els => els.map(e => e.textContent.trim())")
        assert "Avg cost" in header_texts, f"Products header does not say 'Avg cost': {header_texts}"
        assert "Cost" not in header_texts, f"the old bare 'Cost' header is still shown: {header_texts}"

        row = page.locator('#prod-table tbody tr', has_text="Avg Cost Coffee").first
        row.wait_for(state="visible", timeout=8000)
        cost_cell_text = row.locator("td.num").first.text_content()
        assert "1.000" in cost_cell_text, (
            f"the Avg cost cell shows {cost_cell_text!r}, expected it to contain the avg_cost (1.000)")
        assert "2.9" not in cost_cell_text, (
            f"the Avg cost cell still shows the typed cost_price (2.900): {cost_cell_text!r}")

        kpi_text = page.eval_on_selector("#prod-stock-value", "el => el.textContent")
        assert kpi_text and kpi_text.strip() not in ("—", ""), f"the Stock value at cost KPI never loaded: {kpi_text!r}"

        # The PO picker's Unit Cost prefill prefers avg_cost (1.000) over the
        # typed cost_price (2.900) for this same product.
        page.evaluate("() => RetailSystem._openCreatePO()")
        page.wait_for_selector("#ret-po-modal", timeout=8000)
        page.wait_for_timeout(300)
        page.select_option("#po-item-prod", PID_AVGCOST)
        page.wait_for_timeout(150)
        prefilled_cost = page.eval_on_selector("#po-item-cost", "el => el.value")
        assert prefilled_cost == "1", f"PO Unit Cost prefilled {prefilled_cost!r}, expected '1' (avg_cost), not cost_price (2.9)"
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-po-modal", state="detached", timeout=5000)

    check("item #15e, interface I-6: Products shows avg_cost as 'Avg cost' (not cost_price) after a PO "
          "receipt moves it, the Stock value at cost KPI loads, and the PO picker prefills avg_cost",
          item15e_avg_cost_and_stock_value_and_po_default)

    # =========================================================================
    # ITEM #17, interface I-7 -- audit log shows actor names (never a raw
    # uid), translated action labels (never SCREAMING_SNAKE), no entity uuid.
    # =========================================================================
    def item17_audit_log_names_and_translated_labels():
        _, cust_body = api(page, "POST", "/api/sub/retail/customers", {"name": "Audit Log Test Customer"})
        assert cust_body.get("status") == "success", f"customer creation for the audit test failed: {cust_body}"

        # Reviewer fix (2026-09-27, medium): the API's OWN response is the
        # decisive proof that actor_name resolution ran on the server --
        # filtered to the exact CUSTOMER_CREATED row this scenario just
        # wrote (never "any row in a shared, ever-growing log"), so a
        # resolver that silently started returning null again could never
        # hide behind some OLDER row that happened to resolve earlier.
        #
        # First draft of THIS test asserted the ADMIN'S TYPED SIGNUP NAME
        # ('Aura Owner', onboard()'s #su-name) here and failed against the
        # real running app -- root-caused, not patched around:
        # _resolve_actor_identities's own docstring is explicit that
        # registry `users` "has NO full_name/name column at all", and
        # derives `employee_name` as `email or employee_id` (email first --
        # Android's own already-shipped `attributedName` ordering, matched
        # here on purpose so the two clients can never disagree). The typed
        # signup name is never stored anywhere this resolver can reach, so
        # the CORRECT resolved value for a fresh admin with no employee_id
        # set is the signup EMAIL (onboard()'s #su-email) -- asserting the
        # display name here would have been testing a value the schema does
        # not carry, not a real regression.
        _, audit_api_body = api(page, "GET", "/api/sub/retail/audit-log?action=CUSTOMER_CREATED&limit=1")
        api_rows = audit_api_body.get("data") or []
        assert api_rows, f"no CUSTOMER_CREATED audit row came back for the customer this scenario just created: {audit_api_body}"
        assert api_rows[0].get("actor_name") == "owner@p0till.local", (
            f"the API's own actor_name for the row this scenario just wrote is "
            f"{api_rows[0].get('actor_name')!r}, expected the signed-in admin's email "
            f"'owner@p0till.local' (onboard()'s #su-email -- _resolve_actor_identities's "
            f"documented `email or employee_id` contract) -- a null/wrong actor_name here "
            f"means _resolve_actor_identities never reached the client, whatever the UI "
            f"cell shows")

        for lang in ("en", "ar"):
            set_lang(page, lang)
            go(page, "audit-log")
            page.wait_for_selector("#aud-table", timeout=10000)
            page.wait_for_timeout(500)

            cell_texts = page.eval_on_selector_all("#aud-table tbody td", "els => els.map(e => e.textContent)")
            assert cell_texts, f"[{lang}] audit log table rendered no cells"
            for txt in cell_texts:
                t2 = (txt or "").strip()
                assert not UUID_PATTERN.search(t2), f"[{lang}] a cell matches a UUID pattern: {t2!r}"
                assert not re.match(r"^[A-Z_]{4,}$", t2), f"[{lang}] a cell shows a raw SCREAMING_SNAKE code: {t2!r}"

            action_options = page.eval_on_selector_all("#aud-action option", "els => els.map(e => e.textContent.trim())")
            assert len(action_options) > 1, f"[{lang}] the action filter has no real options: {action_options}"
            for opt in action_options:
                assert not re.match(r"^[A-Z_]{4,}$", opt), f"[{lang}] a filter option shows raw SCREAMING_SNAKE: {opt!r}"

        # Reviewer fix: the actor-column check used to accept ANY cell in the
        # whole table that was merely not literally 'Unknown user' -- which a
        # completely unrelated column (Action/Entity/Details) could satisfy
        # even if actor_name NEVER reached the client at all. Two independent
        # strengthenings:
        #   1. The column index (previously a bare td:nth-child(2)) is no
        #      longer assumed -- read straight off the table's own <th> text
        #      first, so a future column reorder fails loudly here instead of
        #      silently making the assertion below meaningless.
        #   2. The check is no longer "is not the placeholder" but "IS the
        #      known, resolvable identity" (the admin's signup email,
        #      'owner@p0till.local' -- see the API check above's own comment
        #      for why the email, not the typed display name, is the correct
        #      expectation here) on the MOST RECENT row -- the API check
        #      above already pinned that exact row to the CUSTOMER_CREATED
        #      write this scenario made (ORDER BY timestamp DESC, id DESC in
        #      list_audit_log), so the UI cell is checked against a value
        #      this test itself controls, never a generic "something,
        #      anything, non-placeholder" pass condition.
        set_lang(page, "en")
        go(page, "audit-log")
        page.wait_for_selector("#aud-table", timeout=10000)
        page.wait_for_timeout(500)
        header_texts = page.eval_on_selector_all("#aud-table thead th", "els => els.map(e => e.textContent.trim())")
        assert len(header_texts) > 1 and header_texts[1] == "User", (
            f"expected the 2nd audit-log column to be headed 'User' (what the actor-cell selector "
            f"below assumes), got header row {header_texts!r}")
        first_row_actor = page.eval_on_selector(
            "#aud-table tbody tr:first-child td:nth-child(2)", "el => el.textContent.trim()")
        assert first_row_actor == "owner@p0till.local", (
            f"the most recent audit row's actor cell is {first_row_actor!r}, expected the signed-in "
            f"admin's resolved identity 'owner@p0till.local' -- 'anything but Unknown user' is not "
            f"enough: a wrong or unrelated column's text would also satisfy that weaker check")

        # Reviewer fix: action-label translation was only ever checked for
        # the ABSENCE of a raw SCREAMING_SNAKE code, which a merely-
        # humanized fallback ('Customer created') already satisfies in
        # EITHER language -- it never proved the catalogued label is
        # actually ROUTED through t()/the AR dictionary. This crew's own new
        # keys are not yet merged into locales/ar.json (contracts.md rule 3
        # -- the merge happens at the end), so asserting real Arabic text
        # here today would go red for a reason that has nothing to do with
        # this code (see i18n-C3c.json, which already carries this exact
        # translation, pending merge). Instead: inject a marker translation
        # directly into the LIVE, in-memory AuraI18n.dicts.ar (never the
        # file on disk) for the exact English sentence _AUDIT_ACTION_LABEL
        # maps CUSTOMER_CREATED to, call _auditActionLabel(), and require
        # the marker back. Mutation-provable right now, independent of the
        # pending merge: reverting the t() call around the catalogued label
        # (or dropping the catalog lookup itself) makes this fail today,
        # with no locale file involved.
        injected = page.evaluate("""
            () => {
                const marker = 'P0_I17_MARKER_' + Date.now();
                const prevCurrent = window.AuraI18n.current;
                const hadKey = Object.prototype.hasOwnProperty.call(window.AuraI18n.dicts.ar, 'Customer created');
                const prevVal = window.AuraI18n.dicts.ar['Customer created'];
                window.AuraI18n.current = 'ar';
                window.AuraI18n.dicts.ar['Customer created'] = marker;
                const out = RetailSystem._auditActionLabel('CUSTOMER_CREATED');
                if (hadKey) window.AuraI18n.dicts.ar['Customer created'] = prevVal;
                else delete window.AuraI18n.dicts.ar['Customer created'];
                window.AuraI18n.current = prevCurrent;
                return { marker, out };
            }
        """)
        assert injected["out"] == injected["marker"], (
            f"_auditActionLabel('CUSTOMER_CREATED') did not return the AR dictionary's translation "
            f"for 'Customer created' (injected {injected['marker']!r}), got {injected['out']!r} -- "
            f"the catalogued label is not actually routed through t()/AuraI18n.dicts.ar")

    check("item #17, interface I-7: audit log shows a resolved actor name (not a uid), translated action "
          "labels in both cells and the filter (never SCREAMING_SNAKE), and no uuid entity id -- in EN and AR",
          item17_audit_log_names_and_translated_labels)

    # =========================================================================
    # ITEM #19 -- dashboard chart legend and status-pill labels through t();
    # app-rendered long dates use ar-JO-u-nu-latn (Levantine month names).
    # =========================================================================
    def item19_arabic_legend_and_levantine_dates():
        set_lang(page, "ar")
        go(page, "dashboard")
        page.wait_for_timeout(1200)  # let the payment-method chart build

        # The Chart.js instance's own COMPUTED legend items (chart.legend.
        # legendItems, built by generateLabels() on render) -- not
        # chart.data.labels, which retailPaymentChartConfig() deliberately
        # keeps as the RAW method keys (see that function's own comment:
        # retail_payment_chart_negative_bucket_test.js -- an existing test
        # this crew does not own -- pins data.labels to exactly
        # ['cash','card'], so translation lives in Chart.js's own label-
        # DISPLAY hook instead). legendItems[i].text is what Chart.js
        # actually PAINTS onto the canvas legend, which is the real claim
        # this scenario is proving.
        legend_texts = page.evaluate("""
            () => {
                const canvas = document.getElementById('r-dash-pay');
                const chart = canvas && window.Chart && Chart.getChart ? Chart.getChart(canvas) : null;
                return (chart && chart.legend && chart.legend.legendItems)
                    ? chart.legend.legendItems.map(i => i.text) : null;
            }
        """)
        assert legend_texts, "no payment-method chart (with a rendered legend) is registered on #r-dash-pay"
        RAW_KEYS = {"cash", "card", "mobile", "transfer", "credit", "voucher"}
        raw_leftover = [l for l in legend_texts if l in RAW_KEYS]
        assert not raw_leftover, f"the chart legend still carries raw, untranslated method keys: {raw_leftover} (full: {legend_texts})"

        # The dashboard's own recent-transactions payment-method badges --
        # real DOM text, not canvas-drawn.
        badge_texts = page.eval_on_selector_all("#sub-content .ret-badge", "els => els.map(e => e.textContent.trim())")
        raw_badge_leftover = [b for b in badge_texts if b.lower() in RAW_KEYS]
        assert not raw_badge_leftover, f"a status-pill badge still shows a raw method key: {raw_badge_leftover}"

        # Levantine month name for a September date, Western digits kept.
        date_text = page.evaluate("() => RetailSystem._localeDate(new Date(Date.UTC(2026, 8, 15, 12, 0, 0)))")
        assert "أيلول" in date_text, f"expected the Levantine month name أيلول for a September date, got {date_text!r}"
        assert "سبتمبر" not in date_text, f"date still shows the pan-Arabic month name سبتمبر, not the Levantine one: {date_text!r}"
        assert any(ch.isascii() and ch.isdigit() for ch in date_text), (
            f"date lost its Western digits: {date_text!r}")

        set_lang(page, "en")

    check("item #19: the Arabic dashboard's chart legend and status-pill badges carry no raw English "
          "payment-method word, and app-rendered long dates show أيلول (Levantine), not سبتمبر, with "
          "Western digits kept", item19_arabic_legend_and_levantine_dates)

    # =========================================================================
    # ROUND 2, T6 (#19 low) -- Sales History / sale-detail status pills, PO
    # status and returns refund_method all go through t() with catalogued
    # labels now, instead of a raw lowercase code ('completed'/'pending'/
    # 'received'/'cash'/...) leaking onto an otherwise-translated screen.
    # =========================================================================
    def item_t6_status_pills_translated():
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)
        page.wait_for_timeout(300)
        page.click('.pos-pay-btn[data-method="cash"]')
        page.fill("#pos-tendered", "5")
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        t6_sale_id = page.evaluate("() => RetailSystem._lastSaleData.id")
        t6_sale_number = page.evaluate("() => RetailSystem._lastSaleData.sale_number")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        # Sales History row + sale detail: 'completed' must render as the
        # catalogued 'Completed', never the raw lowercase code.
        go(page, "sales")
        page.wait_for_selector("#sh-table", timeout=10000)
        page.wait_for_timeout(400)
        # Locate the row by sale id via the row's own onclick attribute
        # rather than guessing at visible text -- robust regardless of
        # short/legacy document-number formatting.
        row_html = page.eval_on_selector(
            f'#sh-table tbody tr:has(button[onclick*="{t6_sale_id}"])', "el => el.innerHTML")
        assert "Completed" in row_html, (
            f"Sales History status pill does not show the catalogued 'Completed' label. Row HTML: {row_html!r}")
        assert "completed<" not in row_html and ">completed<" not in row_html, (
            f"Sales History status pill still shows the raw lowercase code. Row HTML: {row_html!r}")

        page.evaluate("(id) => RetailSystem._viewSale(id)", t6_sale_id)
        page.wait_for_selector("#ret-sale-dialog", timeout=8000)
        page.wait_for_timeout(300)
        detail_html = page.eval_on_selector("#ret-sale-dialog", "el => el.innerHTML")
        assert "Completed" in detail_html, (
            f"sale-detail status pill does not show the catalogued 'Completed' label.")
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-sale-dialog", state="detached", timeout=5000)

        # Returns: refund_method 'cash' must render as the catalogued 'Cash'.
        page.evaluate("() => RetailSystem._openCreateReturn()")
        page.wait_for_selector("#ret-return-dialog", timeout=8000)
        page.fill("#ret-sale-search", t6_sale_number)
        page.click('button:has-text("Lookup")')
        page.wait_for_timeout(500)
        page.evaluate("() => document.querySelectorAll('.ret-item-cb').forEach(cb => cb.checked = true)")
        page.click("#ret-save-btn")
        page.wait_for_timeout(700)

        go(page, "returns")
        page.wait_for_selector("#ret-table, table.ret-table", timeout=10000)
        page.wait_for_timeout(400)
        returns_html = page.eval_on_selector("#sub-content", "el => el.innerHTML")
        assert ">Cash<" in returns_html, (
            f"returns refund_method badge does not show the catalogued 'Cash' label. Screen HTML tail: "
            f"{returns_html[-800:]!r}")

        # PO status: create a PO (default status 'pending') and check its
        # list-row and view-modal badges both show 'Pending', not 'pending'.
        _, sup_body = api(page, "POST", "/api/sub/retail/suppliers", {"name": "T6 Status Supplier"})
        sup_id = ((sup_body or {}).get("data") or {}).get("id")
        _, po_body = api(page, "POST", "/api/sub/retail/purchase-orders", {
            "supplier_id": sup_id, "items": [{"product_id": PID_3, "quantity": 2, "unit_cost": 3}],
        })
        assert po_body.get("status") == "success", f"T6 PO was not created: {po_body}"
        po_id = ((po_body or {}).get("data") or {}).get("id")

        go(page, "purchases")
        page.wait_for_selector("#po-table", timeout=10000)
        page.wait_for_timeout(400)
        po_row_html = page.eval_on_selector(
            f'#po-table tbody tr:has(button[onclick*="_viewPO({po_id})"])', "el => el.innerHTML")
        assert "Pending" in po_row_html, (
            f"PO list status pill does not show the catalogued 'Pending' label. Row HTML: {po_row_html!r}")

        page.evaluate("(id) => RetailSystem._viewPO(id)", po_id)
        page.wait_for_selector("#ret-po-view-dialog", timeout=8000)
        page.wait_for_timeout(300)
        po_detail_html = page.eval_on_selector("#ret-po-view-dialog", "el => el.innerHTML")
        assert "Pending" in po_detail_html, "PO view-modal status pill does not show the catalogued 'Pending' label"
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-po-view-dialog", state="detached", timeout=5000)
        go_pos(page)

    check("T6 (#19 low): Sales History and sale-detail status pills, PO status (list + view) and returns "
          "refund_method all render the catalogued t() label, never the raw lowercase code",
          item_t6_status_pills_translated)

    # =========================================================================
    # ROUND 3 (xss-defense-in-depth, high -- verifier finding on T6 above):
    # T6 proves status pills show the TRANSLATED label, never the raw code
    # -- it never touched escaping. All four call sites this defence-in-
    # depth item names (`_badge(this._esc(t(this._humanizeSnakeCode(
    # status))))` -- PO list, PO view, Sales History, sale detail) had
    # their `_esc(...)` wrap removed in a live mutation and T6 stayed
    # green throughout, because `status` is a fixed server enum under
    # NORMAL use ('completed'/'pending'/...) and `_esc()` is a no-op on
    # plain alphanumeric text -- nothing in T6's own assertions can tell
    # "escaped" from "not escaped" apart for that input. `status` cannot
    # be SET to an arbitrary string through the real create routes either
    # (the crew's own original reasoning for not mutation-proving this at
    # all) -- so this scenario exercises the REAL, unmodified render code
    # with a hostile status by intercepting the network responses those
    # four screens actually read and rewriting just the `status` field of
    # the fixture's own real sale/PO before the frontend ever sees it.
    # The render functions themselves are never touched, only what they
    # are handed -- the same shape R3-19dash (above) already uses for
    # payment_method, extended here to all four `status` call sites and to
    # a field the real API genuinely refuses to store directly.
    # =========================================================================
    def item_xss_status_pill_escaping_defense_in_depth_r3():
        page.evaluate("() => { window.r3statusxssfired = false; }")
        # All-lowercase, no underscores -- deliberately, not a stylistic
        # choice: every one of the four render sites this scenario proves
        # (sale status, PO status) routes the raw string through
        # `_humanizeSnakeCode()` BEFORE `_esc()` (see that helper's own
        # comment; it is what turns 'completed' into 'Completed' for a
        # real status), which lower-cases and replaces '_' with ' ' as a
        # LEGITIMATE, EXPECTED transform, unrelated to escaping. A payload
        # with underscores/uppercase (tried first) got silently rewritten
        # by that transform before ever reaching _esc() -- measured live:
        # 'window.__r3StatusXssFired=true' round-tripped as 'window.
        # r3statusxssfired=true' (correctly escaped, but not textually
        # identical, which broke this scenario's own round-trip check, not
        # the product). Avoiding underscores/uppercase makes
        # _humanizeSnakeCode's transform the IDENTITY function on this
        # exact string, so a round-trip comparison is meaningful again.
        hostile_status = '<img src=x onerror="window.r3statusxssfired=true">'
        import html as _html

        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)
        page.wait_for_timeout(200)
        page.click('.pos-pay-btn[data-method="cash"]')
        page.fill("#pos-tendered", "5")
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        xss_sale_id = page.evaluate("() => RetailSystem._lastSaleData.id")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        _, sup_body = api(page, "POST", "/api/sub/retail/suppliers", {"name": "XSS Status Supplier"})
        sup_id = ((sup_body or {}).get("data") or {}).get("id")
        _, po_body = api(page, "POST", "/api/sub/retail/purchase-orders", {
            "supplier_id": sup_id, "items": [{"product_id": PID_3, "quantity": 1, "unit_cost": 3}],
        })
        assert po_body.get("status") == "success", f"XSS-defense PO was not created: {po_body}"
        xss_po_id = ((po_body or {}).get("data") or {}).get("id")

        def _mutate_route(pattern, mutate):
            def handler(route):
                resp = route.fetch()
                body = resp.json()
                mutate(body)
                route.fulfill(response=resp, json=body)
            page.route(pattern, handler)
            return handler

        def _mutate_sale_list(body):
            for row in (body.get("data") or []):
                if row.get("id") == xss_sale_id:
                    row["status"] = hostile_status

        def _mutate_sale_detail(body):
            sale = ((body.get("data") or {}).get("sale")) or {}
            if sale.get("id") == xss_sale_id:
                sale["status"] = hostile_status

        def _mutate_po_list(body):
            for row in (body.get("data") or []):
                if row.get("id") == xss_po_id:
                    row["status"] = hostile_status

        def _mutate_po_detail(body):
            po = ((body.get("data") or {}).get("po")) or {}
            if po.get("id") == xss_po_id:
                po["status"] = hostile_status

        def _assert_escaped(surface, has_img, html_text):
            assert not has_img, f"[{surface}] a real <img> element was rendered for a hostile status"
            assert not page.evaluate("() => window.r3statusxssfired"), (
                f"[{surface}] the hostile status's onerror payload actually fired")
            assert hostile_status in _html.unescape(html_text), (
                f"[{surface}] the hostile status string did not round-trip (escaped or dropped, not "
                f"merely rendered inert): {html_text[-600:]!r}")

        handlers = []
        try:
            # -- Sales History list ---------------------------------------
            pat1 = f"{BASE}/api/sub/retail/sales/recent*"
            h1 = _mutate_route(pat1, _mutate_sale_list)
            handlers.append((pat1, h1))
            go(page, "sales")
            page.wait_for_selector("#sh-table", timeout=10000)
            page.wait_for_timeout(500)
            row_sel = f'#sh-table tbody tr:has(button[onclick*="{xss_sale_id}"])'
            row_html = page.eval_on_selector(row_sel, "el => el.innerHTML")
            row_has_img = page.eval_on_selector(row_sel, "el => !!el.querySelector('img[src=\"x\"]')")
            _assert_escaped("Sales History row", row_has_img, row_html)

            # -- Sale detail ------------------------------------------------
            pat2 = f"{BASE}/api/sub/retail/sales/{xss_sale_id}"
            h2 = _mutate_route(pat2, _mutate_sale_detail)
            handlers.append((pat2, h2))
            page.evaluate("(id) => RetailSystem._viewSale(id)", xss_sale_id)
            page.wait_for_selector("#ret-sale-dialog", timeout=8000)
            page.wait_for_timeout(300)
            detail_html = page.eval_on_selector("#ret-sale-dialog", "el => el.innerHTML")
            detail_has_img = page.eval_on_selector(
                "#ret-sale-dialog", "el => !!el.querySelector('img[src=\"x\"]')")
            _assert_escaped("sale detail", detail_has_img, detail_html)
            page.keyboard.press("Escape")
            page.wait_for_selector("#ret-sale-dialog", state="detached", timeout=5000)

            # -- PO list ------------------------------------------------
            pat3 = f"{BASE}/api/sub/retail/purchase-orders"
            h3 = _mutate_route(pat3, _mutate_po_list)
            handlers.append((pat3, h3))
            go(page, "purchases")
            page.wait_for_selector("#po-table", timeout=10000)
            page.wait_for_timeout(500)
            po_row_sel = f'#po-table tbody tr:has(button[onclick*="_viewPO({xss_po_id})"])'
            po_row_html = page.eval_on_selector(po_row_sel, "el => el.innerHTML")
            po_row_has_img = page.eval_on_selector(po_row_sel, "el => !!el.querySelector('img[src=\"x\"]')")
            _assert_escaped("PO list row", po_row_has_img, po_row_html)

            # -- PO view ------------------------------------------------
            pat4 = f"{BASE}/api/sub/retail/purchase-orders/{xss_po_id}"
            h4 = _mutate_route(pat4, _mutate_po_detail)
            handlers.append((pat4, h4))
            page.evaluate("(id) => RetailSystem._viewPO(id)", xss_po_id)
            page.wait_for_selector("#ret-po-view-dialog", timeout=8000)
            page.wait_for_timeout(300)
            po_detail_html = page.eval_on_selector("#ret-po-view-dialog", "el => el.innerHTML")
            po_detail_has_img = page.eval_on_selector(
                "#ret-po-view-dialog", "el => !!el.querySelector('img[src=\"x\"]')")
            _assert_escaped("PO view", po_detail_has_img, po_detail_html)
            page.keyboard.press("Escape")
            page.wait_for_selector("#ret-po-view-dialog", state="detached", timeout=5000)
        finally:
            for pattern, handler in handlers:
                try:
                    page.unroute(pattern, handler)
                except Exception:  # noqa: BLE001
                    pass
            go_pos(page)

    check("XSS-defense-in-depth, status pill escaping (verifier finding on T6 (#19 low)): a hostile "
          "status string served by the REAL sale/purchase-order endpoints (network-intercepted, never "
          "the render code itself) renders as escaped, inert text -- no injected <img>, no fired "
          "onerror -- on the Sales History row, sale-detail modal, PO list row AND PO view modal",
          item_xss_status_pill_escaping_defense_in_depth_r3)

    # =========================================================================
    # ROUND 2, till R2 -- two stored-XSS findings the T4 sweep never covered
    # (that sweep is product/PO/supplier/notes text; neither of these is any
    # of those):
    #   (1) a hostile CUSTOMER NAME, created at a cashier-level capability,
    #       rendered bare in Sales History and the Returns list.
    #   (2) a hostile refund_method: create_sale never validates
    #       payment_method, so a hostile string posted there survives into
    #       create_return's refund_method (defaulted from the sale's own
    #       payment_method when none is sent) and used to reach the Returns
    #       list as live markup via T6's own `_badge(t(_humanizeSnakeCode(...)))`
    #       -- unescaped, and _badge() does not escape its own argument.
    # =========================================================================
    R2_XSS_NAME = 'Hostile Customer <img src=x onerror="window.__r2XssFired=true">Name&"Co'
    R2_XSS_METHOD = '<img src=x onerror="window.__r2MethodXssFired=true">'

    def item_r2_hostile_customer_and_refund_method():
        page.evaluate("() => { window.__r2XssFired = false; window.__r2MethodXssFired = false; }")

        _, hcust_body = api(page, "POST", "/api/sub/retail/customers", {"name": R2_XSS_NAME})
        hcust_id = ((hcust_body or {}).get("data") or {}).get("id")
        assert hcust_id, f"hostile-name customer was not created: {hcust_body}"
        api(page, "PATCH", f"/api/sub/retail/customers/{hcust_id}", {"credit_mode": "unlimited"})

        # (1) Sales History: sell to the hostile-name customer.
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)
        page.wait_for_timeout(300)
        page.select_option("#pos-customer", hcust_id)
        page.click('.pos-pay-btn[data-method="credit"]')
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        r2_sale_id = page.evaluate("() => RetailSystem._lastSaleData.id")
        r2_sale_number = page.evaluate("() => RetailSystem._lastSaleData.sale_number")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        go(page, "sales")
        page.wait_for_selector("#sh-table", timeout=10000)
        page.wait_for_timeout(400)
        # A real DOM query for an actual <img> ELEMENT, exactly like T4's own
        # assert_no_img -- not a substring search on innerHTML, which would
        # also match this file's OWN "<img src=x onerror=...>" example text
        # sitting in an adjacent HTML comment in the source template.
        sh_row_sel = f'#sh-table tbody tr:has(button[onclick*="_viewSale({r2_sale_id})"])'
        sh_has_img = page.eval_on_selector(sh_row_sel, "el => !!el.querySelector('img[src=\"x\"]')")
        assert not sh_has_img, "(1) Sales History customer_name was not escaped -- a real <img> element rendered"
        fired = page.evaluate("() => window.__r2XssFired")
        assert not fired, "(1) Sales History: the hostile customer name's onerror payload EXECUTED"

        # (2) refund_method XSS: post a sale with a HOSTILE payment_method
        # directly (create_sale never validates it), then a return with NO
        # refund_method so create_return defaults it from the sale's own
        # payment_method -- reproducing the exact path the evidence measured.
        _, hpm_sale_body = api(page, "POST", "/api/sub/retail/sales", {
            "items": [{"product_id": PID_1, "quantity": 1, "unit_price": 10, "line_total": 10}],
            "payment_method": R2_XSS_METHOD, "amount_paid": 10,
        })
        assert hpm_sale_body.get("status") == "success", (
            f"hostile-payment_method sale was not created (server-side validation may have changed -- "
            f"if so, this finding may already be closed upstream): {hpm_sale_body}")
        hpm_sale_id = ((hpm_sale_body or {}).get("data") or {}).get("id")
        hpm_sale_number = (hpm_sale_body.get("data") or {}).get("sale_number")

        _, hpm_return_body = api(page, "POST", "/api/sub/retail/returns", {
            "sale_id": hpm_sale_id,
            "items": [{"product_id": PID_1, "quantity": 1, "line_total": 10}],
        })
        assert hpm_return_body.get("status") == "success", f"hostile-refund_method return was not created: {hpm_return_body}"

        go(page, "returns")
        page.wait_for_selector("#ret-table, table.ret-table", timeout=10000)
        page.wait_for_timeout(400)
        # Real DOM query, not a substring search -- see the Sales History
        # check above for why (this file's own example text in a source
        # comment would otherwise false-positive the check).
        returns_has_img = page.eval_on_selector("#sub-content", "el => !!el.querySelector('img[src=\"x\"]')")
        assert not returns_has_img, "(2) Returns list refund_method was not escaped -- a real <img> element rendered"
        method_fired = page.evaluate("() => window.__r2MethodXssFired")
        assert not method_fired, "(2) Returns list: the hostile refund_method's onerror payload EXECUTED"

        # Also confirm the hostile customer name did not inject on the
        # Returns list, on a return processed FOR that customer (a separate
        # row from the refund_method case above, since (2)'s return is a
        # walk-in sale).
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_3)
        page.wait_for_timeout(300)
        page.select_option("#pos-customer", hcust_id)
        page.click('.pos-pay-btn[data-method="credit"]')
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        r2b_sale_number = page.evaluate("() => RetailSystem._lastSaleData.sale_number")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        page.evaluate("() => { window.__r2XssFired = false; }")
        page.evaluate("() => RetailSystem._openCreateReturn()")
        page.wait_for_selector("#ret-return-dialog", timeout=8000)
        page.fill("#ret-sale-search", r2b_sale_number)
        page.click('button:has-text("Lookup")')
        page.wait_for_timeout(500)
        page.evaluate("() => document.querySelectorAll('.ret-item-cb').forEach(cb => cb.checked = true)")
        page.click("#ret-save-btn")
        page.wait_for_timeout(700)

        go(page, "returns")
        page.wait_for_selector("#ret-table, table.ret-table", timeout=10000)
        page.wait_for_timeout(400)
        returns_has_img_2 = page.eval_on_selector("#sub-content", "el => !!el.querySelector('img[src=\"x\"]')")
        assert not returns_has_img_2, "(1b) Returns list customer_name was not escaped -- a real <img> element rendered"
        fired_2 = page.evaluate("() => window.__r2XssFired")
        assert not fired_2, "(1b) Returns list: the hostile customer name's onerror payload EXECUTED"

        go_pos(page)

    check("round 2, till R2: a hostile customer name is escaped (no injected <img>, no fired onerror) on "
          "Sales History and the Returns list, and a hostile refund_method (from a sale whose "
          "payment_method create_sale never validates) is escaped on the Returns list too",
          item_r2_hostile_customer_and_refund_method)

    # =========================================================================
    # ROUND 2, till R2 (item #12-till) -- the printed receipt header falls
    # back to the SIGNUP company name, not the vendor's own 'Aura Retail',
    # when branding_business_name is empty (I-3's own read-time fallback).
    # =========================================================================
    def item12_till_receipt_header_signup_fallback():
        _, branding_body = api(page, "GET", "/api/sub/retail/settings/branding")
        branding = branding_body.get("data") or {}
        signup_name = branding.get("business_name")
        assert signup_name and signup_name != "Aura Retail", (
            f"this fresh install's signup company name is {signup_name!r} -- expected the real "
            f"company name typed at signup, not empty or the vendor default")
        assert not branding.get("branding_business_name"), (
            f"branding_business_name is already set ({branding.get('branding_business_name')!r}) on this "
            f"fresh install -- this scenario needs the UNSET case to prove the signup fallback")

        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)
        page.wait_for_timeout(300)
        page.click('.pos-pay-btn[data-method="cash"]')
        page.fill("#pos-tendered", "10")
        page.wait_for_timeout(200)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        page.wait_for_timeout(200)

        # Same proven recipe item #6's own print check already uses: the
        # dialog's own Print button (not a hand-rolled _printReceipt() call
        # with the wrong arguments), then read #ret-print-frame back.
        page.click('#ret-receipt-dialog button:has-text("Print")')
        page.wait_for_timeout(500)
        print_frame_text = page.evaluate(
            "() => { const f = document.getElementById('ret-print-frame'); "
            "return f && f.contentDocument ? f.contentDocument.body.innerText : ''; }")
        assert print_frame_text, "the print frame has no content -- _printReceipt did not run"
        first_line = print_frame_text.strip().splitlines()[0] if print_frame_text.strip() else ""
        assert first_line == signup_name, (
            f"printed receipt header's first line is {first_line!r}, expected the signup company name "
            f"{signup_name!r}")

        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
        go_pos(page)

    check("item #12-till: the printed receipt header shows the signup company name, not the vendor "
          "default 'Aura Retail', when branding_business_name has never been set",
          item12_till_receipt_header_signup_fallback)

    # =========================================================================
    # ROUND 3, till -- closing the serial mutation runner's NO-GUARD/VACUOUS
    # findings against round-1/round-2's own till fixes (round2_result.json,
    # key mutation.proofs). Every fix below already exists in source; only
    # the guard was missing or could not tell fixed from broken. Each
    # scenario is intentionally self-contained (its own fixture, its own
    # `go_pos(page)`/`go(page, ...)` reset) so it can be isolated with
    # P0_ONLY for a fast mutation-proof cycle without depending on where an
    # earlier scenario left the page.
    # =========================================================================

    def _db_execute_retry(sql, params):
        """Same short-retry-on-OperationalError shape as count_sales() above,
        for the one write this file needs to make directly against the
        scratch retail.db (simulating a sale synced in from another till --
        item #16's own scenario, run2_result.json's own recipe)."""
        last_err = None
        for _ in range(5):
            try:
                conn = sqlite3.connect(str(RETAIL_DB), timeout=5)
                try:
                    conn.execute(sql, params)
                    conn.commit()
                finally:
                    conn.close()
                return
            except sqlite3.OperationalError as e:  # noqa: PERF203
                last_err = e
                time.sleep(0.2)
        raise last_err

    # -------------------------------------------------------------------
    # (a) item #16: a short number shared by two tills is REFUSED, naming
    # both device tags -- never silently resolved to whichever row sorts
    # first -- and a genuinely unique short number in that SAME shop still
    # resolves. Round-1/round-2 shipped the refusal code (subsystem-
    # retail.js's own "Review fix (2026-09-27, medium -- item #16
    # regression)" comment) with no test at all (round2_result.json:
    # "16-ambiguous-return-lookup ... no_test: No test references it").
    # -------------------------------------------------------------------
    def item16_ambiguous_short_number_both_directions_r3():
        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_1)
        page.wait_for_timeout(300)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        sale_a_number = page.evaluate("() => RetailSystem._lastSaleData.sale_number")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        m = re.match(r"^([A-Za-z]+-\d+)-([0-9a-f]{8})-([0-9a-f]{8})$", sale_a_number)
        assert m, (
            f"sale A's own number {sale_a_number!r} is not the legacy 4-part shape this scenario "
            f"needs (SALE-NNNNNN-<company8>-<device8>) -- this shop may be on the newer per-till "
            f"series, which #16's own text says is opt-in and out of Phase 0's scope")
        short_a, company_frag = m.group(1), m.group(2)

        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_2)
        page.wait_for_timeout(300)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        sale_b_id = page.evaluate("() => RetailSystem._lastSaleData.id")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        # Renumber sale B in the scratch DB to SALE A's own short form, with a
        # DIFFERENT device tag -- simulating a second till's sale synced in
        # with a colliding short number (#16's own reasoning: "two tills can
        # both mint SALE-000016"). Same recipe as run2_result.json's own
        # measurement ("Sale B ... renumbered ... to '...-ffff0000'").
        colliding_number = f"{short_a}-{company_frag}-ffff0000"
        _db_execute_retry("UPDATE sales SET sale_number=? WHERE id=?", (colliding_number, sale_b_id))

        # Sale C: a THIRD, untouched sale -- proves a short number ELSEWHERE
        # in the shop still resolves normally while sale A's own short number
        # is ambiguous (the "unique short number still resolves" half).
        page.evaluate("(id) => RetailSystem._addToCart(id)", PID_3)
        page.wait_for_timeout(300)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        sale_c_id = page.evaluate("() => RetailSystem._lastSaleData.id")
        sale_c_number = page.evaluate("() => RetailSystem._lastSaleData.sale_number")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
        short_c = re.match(r"^([A-Za-z]+-\d+)", sale_c_number).group(1)

        # DENY half: sale A's own short number is now ambiguous -- refused.
        go(page, "returns")
        page.wait_for_timeout(400)
        page.click('button:has-text("Process Return")')
        page.wait_for_selector("#ret-return-dialog", timeout=8000)
        page.fill("#ret-sale-search", short_a)
        page.click('#ret-return-dialog button:has-text("Lookup")')
        page.wait_for_timeout(500)
        ambiguous_toasts = page.eval_on_selector_all(
            '[role="alert"], [role="status"]', "els => els.map(e => e.textContent)")
        assert any("More than one till" in (txt or "") for txt in ambiguous_toasts), (
            f"looking up the now-ambiguous short number {short_a!r} did not show the 'More than one "
            f"till...' refusal toast. Toasts on screen: {ambiguous_toasts}")
        found_sale_id_ambiguous = page.evaluate("() => RetailSystem._returnSaleId")
        assert found_sale_id_ambiguous is None, (
            f"an ambiguous short number silently resolved to sale id {found_sale_id_ambiguous!r} "
            f"instead of being refused")

        # ALLOW half, in the SAME ambiguous shop: sale C's own unique short
        # number still resolves normally.
        page.fill("#ret-sale-search", short_c)
        page.click('#ret-return-dialog button:has-text("Lookup")')
        page.wait_for_timeout(500)
        found_sale_id_unique = page.evaluate("() => RetailSystem._returnSaleId")
        assert found_sale_id_unique == sale_c_id, (
            f"looking up the UNIQUE short number {short_c!r} (in a shop that also has an ambiguous "
            f"one) resolved to {found_sale_id_unique!r}, expected sale C's own id {sale_c_id}")
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-return-dialog", state="detached", timeout=5000)
        go_pos(page)

    check("R3-16a, item #16 (round 3): a short number shared by two tills (renumbered in the scratch "
          "DB to simulate a synced-in sale) is REFUSED with both device tags named, never silently "
          "resolved -- and a genuinely unique short number in that SAME shop still resolves normally",
          item16_ambiguous_short_number_both_directions_r3)

    # -------------------------------------------------------------------
    # (b) item #16: audit Details text is shortened too (_shortenDocNumbersInText),
    # full number kept in the title. round2_result.json: "16-audit-details-
    # not-shortened ... no_test: No test references it."
    # -------------------------------------------------------------------
    def item16_audit_details_shortened_r3():
        _, sup_body = api(page, "POST", "/api/sub/retail/suppliers", {"name": "R3 Audit Details Supplier"})
        sup_id = ((sup_body or {}).get("data") or {}).get("id")
        assert sup_id, f"supplier for the audit-details test was not created: {sup_body}"
        _, po_body = api(page, "POST", "/api/sub/retail/purchase-orders", {
            "supplier_id": sup_id, "items": [{"product_id": PID_1, "quantity": 2, "unit_cost": 4}],
        })
        assert po_body.get("status") == "success", f"R3 audit-details PO was not created: {po_body}"
        po_id = ((po_body or {}).get("data") or {}).get("id")
        po_number = (po_body.get("data") or {}).get("po_number")
        assert po_number, f"PO number missing from create response: {po_body}"
        _, recv_body = api(page, "POST", f"/api/sub/retail/purchase-orders/{po_id}/receive")
        assert recv_body.get("status") == "success", f"receiving the R3 audit-details PO failed: {recv_body}"

        _, audit_body = api(page, "GET", "/api/sub/retail/audit-log?action=PO_RECEIVED&limit=1")
        api_rows = audit_body.get("data") or []
        assert api_rows, f"no PO_RECEIVED audit row came back for the PO this scenario just received: {audit_body}"
        assert po_number in (api_rows[0].get("details") or ""), (
            f"the API's own audit details for this PO_RECEIVED row do not carry the full PO number "
            f"{po_number!r} -- the fixture itself is wrong: {api_rows[0]}")

        go(page, "audit-log")
        page.wait_for_selector("#aud-table", timeout=10000)
        page.wait_for_timeout(600)
        page.evaluate(
            "() => { document.getElementById('aud-action').value='PO_RECEIVED'; "
            "RetailSystem._applyAuditFilters(); }")
        page.wait_for_timeout(500)
        details_cells = page.eval_on_selector_all("#aud-table tbody td:last-child", "els => els.map(e => e.textContent)")
        assert details_cells, "no Details cells rendered for the PO_RECEIVED filter"
        legacy_doc_number = re.compile(r"[A-Za-z]+-\d+-[0-9a-f]{8}-[0-9a-f]{8}")
        for txt in details_cells:
            assert not legacy_doc_number.search(txt or ""), (
                f"an audit Details cell still shows the full legacy document number: {txt!r}")
        short_po = re.match(r"^([A-Za-z]+-\d+)", po_number).group(1)
        assert any(short_po in (txt or "") for txt in details_cells), (
            f"the Details cell dropped the PO number entirely instead of shortening it (expected "
            f"{short_po!r} somewhere in {details_cells})")
        title_texts = page.eval_on_selector_all("#aud-table tbody td:last-child bdi", "els => els.map(e => e.title)")
        assert any(po_number in (ti or "") for ti in title_texts), (
            f"the full PO number {po_number!r} is not preserved in any Details cell's title attribute: "
            f"{title_texts}")

        page.evaluate("() => RetailSystem._clearAuditFilters()")
        go_pos(page)

    check("R3-16b, item #16 (audit details shortened): a PO_RECEIVED audit row's Details cell shows "
          "only the short document number, never the full legacy 4-part string, with the full string "
          "kept in the cell's title", item16_audit_details_shortened_r3)

    # -------------------------------------------------------------------
    # (c) item #11(d): a toast fires within ~9.5s of the display being
    # opened when it NEVER polls .../state at all -- the "msAgo == null"
    # branch, distinct from the green->amber transition item #11(d)'s
    # existing test already covers. The server's own "display last seen"
    # mark is shared process-wide for this till across every scenario in
    # this file (several earlier scenarios simulate a poll via a real GET
    # .../state), so this scenario intercepts the till's OWN /status poll
    # at the network level and forces it to report "never seen",
    # deterministically -- otherwise a stale-but-real last-seen timestamp
    # from an earlier scenario could leak in and mask the very case under
    # test (ENGINEERING.md failure shape 2: the fixture manufactures the
    # state that hides the bug).
    # -------------------------------------------------------------------
    def item11d_never_connects_toast_r3():
        go_pos(page)

        def _fake_status(route):
            route.fulfill(status=200, content_type="application/json",
                           body='{"display_last_seen_ms_ago": null}')

        page.route(f"{BASE}/api/sub/retail/customer-display/status", _fake_status)
        try:
            page.evaluate("""
                () => {
                    if (RetailSystem._displayStatusPollTimer) {
                        clearInterval(RetailSystem._displayStatusPollTimer);
                        RetailSystem._displayStatusPollTimer = null;
                    }
                    RetailSystem._displayWasGreen = undefined;
                    RetailSystem._displayNeverConnectedToastShown = false;
                    RetailSystem._displayOpened = true;
                    RetailSystem._displayOpenedAtMs = Date.now();
                    RetailSystem._startCustomerDisplayStatusPoll();
                }
            """)
            toast_seen = False
            deadline = time.time() + 9.5
            while time.time() < deadline:
                texts = page.eval_on_selector_all(
                    '[role="alert"], [role="status"]', "els => els.map(e => e.textContent)")
                if any("has not responded" in (t or "") for t in texts):
                    toast_seen = True
                    break
                page.wait_for_timeout(300)
            assert toast_seen, (
                "no 'has not responded' toast fired within 9.5s of a display that opened but never "
                "polled .../state at all")
        finally:
            page.unroute(f"{BASE}/api/sub/retail/customer-display/status", _fake_status)
            page.evaluate("""
                () => {
                    if (RetailSystem._displayStatusPollTimer) {
                        clearInterval(RetailSystem._displayStatusPollTimer);
                        RetailSystem._displayStatusPollTimer = null;
                    }
                    RetailSystem._displayWasGreen = undefined;
                    RetailSystem._displayNeverConnectedToastShown = false;
                }
            """)

    check("R3-11d, item #11(d): a toast fires within ~9.5s when the customer display was opened but "
          "never polls .../state at all (distinct from the green->amber silence case)",
          item11d_never_connects_toast_r3)

    # -------------------------------------------------------------------
    # (d) item #15d: the as-of default uses the shop's LOCAL calendar date,
    # not the UTC date, proven with a REAL fixed clock + timezone_id --
    # round2_result.json's own recipe ("Playwright context with
    # timezone_id='Asia/Amman' and clock.set_fixed_time 2026-09-28T01:30
    # +03:00"). Needs a SEPARATE browser context (timezone_id is fixed at
    # context creation), carrying the same session via storage_state so no
    # second sign-up/licence-seed is needed.
    # -------------------------------------------------------------------
    def item15d_local_date_fixed_clock_amman_r3():
        import datetime as _dt
        storage = ctx.storage_state()
        amman_ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                         timezone_id="Asia/Amman", storage_state=storage)
        try:
            apage = amman_ctx.new_page()
            apage.goto(BASE)
            dismiss_intro_overlay(apage)
            apage.wait_for_selector("#sub-header-section", timeout=20000)

            # 2026-09-28T01:30:00 Amman-local == 2026-09-27T22:30:00 UTC.
            # Local calendar date: the 28th. UTC calendar date: the 27th --
            # the exact local/UTC midnight gap item #15d exists to close.
            fixed_utc = _dt.datetime(2026, 9, 27, 22, 30, 0, tzinfo=_dt.timezone.utc)
            apage.clock.set_fixed_time(fixed_utc)

            utc_date = apage.evaluate("() => new Date().toISOString().slice(0,10)")
            assert utc_date == "2026-09-27", (
                f"fixture check: expected the UTC date to be 2026-09-27 under this fixed clock, got "
                f"{utc_date!r} -- the local/UTC gap this scenario needs is not actually present")
            local_date = apage.evaluate("() => RetailSystem._localISODate(new Date())")
            assert local_date == "2026-09-28", (
                f"_localISODate() under a fixed Amman clock at 01:30 local returned {local_date!r}, "
                f"expected '2026-09-28' (the LOCAL calendar date)")

            go(apage, "trial-balance")
            apage.wait_for_selector("#tb-table", timeout=10000)
            apage.wait_for_timeout(500)
            as_of_val = apage.eval_on_selector("#tb-as-of", "el => el.value")
            assert as_of_val == "2026-09-28", (
                f"#tb-as-of defaulted to {as_of_val!r} under a fixed Amman clock at 01:30 local (UTC "
                f"2026-09-27), expected the LOCAL date '2026-09-28' -- item #15d's own defect was "
                f"defaulting to the UTC date instead")
        finally:
            amman_ctx.close()

    check("R3-15d, item #15d (fixed-clock, timezone_id=Asia/Amman): the trial-balance as-of default "
          "uses the shop's LOCAL calendar date, not the UTC date, at 01:30 local when the two disagree",
          item15d_local_date_fixed_clock_amman_r3)

    # -------------------------------------------------------------------
    # (e) item #15e, the other direction: a product NEVER received through a
    # PO (PID_2 "Gadget", only ever stock-adjusted -- see the seeding
    # section above) keeps avg_cost at its seeded 0, so the Avg cost cell
    # must fall back to the typed cost_price (2.500) rather than show the
    # raw, wrong 'JD 0.000'. round2_result.json: "item #15e only checks a
    # product after a PO receipt; the zero-avg_cost case is never rendered."
    # -------------------------------------------------------------------
    def item15e_never_received_cost_fallback_r3():
        _, list_body = api(page, "GET", "/api/sub/retail/products")
        never_received_row = next(p for p in (list_body.get("data") or []) if p["id"] == PID_2)
        assert never_received_row["avg_cost"] == 0, (
            f"fixture assumption broken: PID_2 (Gadget) has already been received through a PO "
            f"(avg_cost={never_received_row['avg_cost']}) -- this scenario needs a genuinely "
            f"never-received product")
        assert abs(never_received_row["cost_price"] - 2.5) < 0.001, (
            f"fixture assumption broken: PID_2's cost_price is {never_received_row['cost_price']}, "
            f"expected 2.5 (create_product's own round(price/2,3) for a sell_price of 5)")

        go(page, "products")
        page.wait_for_selector("#prod-table", timeout=10000)
        page.wait_for_timeout(500)
        row = page.locator('#prod-table tbody tr', has_text="Gadget").first
        row.wait_for(state="visible", timeout=8000)
        cost_cell_text = row.locator("td.num").first.text_content()
        assert "2.5" in cost_cell_text, (
            f"a never-received product's Avg cost cell shows {cost_cell_text!r}, expected it to fall "
            f"back to the typed cost_price (2.500) since avg_cost is 0")
        assert "0.000" not in cost_cell_text, (
            f"a never-received product's Avg cost cell shows the raw zero avg_cost instead of the "
            f"cost_price fallback: {cost_cell_text!r}")
        go_pos(page)

    check("R3-15e, item #15e (never-received product): a product with avg_cost=0 (never received "
          "through a PO) shows its typed cost_price in the Avg cost cell, not a bare 'JD 0.000'",
          item15e_never_received_cost_fallback_r3)

    # -------------------------------------------------------------------
    # (f) item #15(c) -- ROUND 3 REWRITE (U1(f), verifier finding): the
    # round-2 version only ever COLLECTED elements that already carry
    # `.num` and checked those were end-aligned -- so "every money cell
    # carries .num" was completely unguarded: as long as the existing KPI
    # spans and account-row cells kept `.num`, the check stayed green no
    # matter how many OTHER money cells lost it. Verified live (probe4
    # demo_R3_15c): with `class="num"` removed from BOTH render sites this
    # item names (subsystem-retail.js's balance-sheet and cash-flow account
    # rows), the round-2 assertion still passed -- 7 `.num` cells remained
    # on the Balance Sheet with 4 real money cells (JD 11.600, JD 5.000,
    # JD 10.000, JD 1.600) left start-aligned and completely unnoticed; 5
    # remained on Cash Flow with 1 unnoticed. This version instead finds
    # every MONEY-LOOKING leaf cell by its own rendered TEXT (a 3dp amount,
    # this fixture's own JOD currency -- `_fmt`'s own `toFixed(3)`), then
    # asserts EACH ONE carries `.num` (`el.closest('.num')`, not merely "is
    # tagged .num already") and computes right/end alignment -- a cell that
    # silently lost the class now fails on the class-membership assertion
    # even if some other cell nearby still has it.
    # -------------------------------------------------------------------
    def item15c_num_balance_sheet_cash_flow_r3():
        # Self-contained fixture: under P0_ONLY isolation (this file's own
        # opt-in mutation-proof filter), NO earlier scenario has necessarily
        # run, so the balance sheet/cash flow can otherwise show ZERO rows
        # on a fresh company -- which would make "no money cells rendered"
        # look like a pass-condition failure indistinguishable from the real
        # defect (ENGINEERING.md failure shape 2: a fixture that manufactures
        # the state that hides the bug). A real cash sale guarantees a Cash
        # asset movement (Balance Sheet) and an operating cash inflow (Cash
        # Flow) regardless of what has or has not run before this scenario.
        _, r3bs_sale_body = api(page, "POST", "/api/sub/retail/sales", {
            "items": [{"product_id": PID_1, "quantity": 1, "unit_price": 10, "line_total": 10}],
            "payment_method": "cash", "amount_paid": 10,
        })
        assert r3bs_sale_body.get("status") == "success", (
            f"R3-15c fixture sale was not created: {r3bs_sale_body}")

        # Leaf `td`/`span` elements only (`c.children.length === 0`) --
        # this screen nests money spans inside summary cards, so scanning
        # every ancestor too would over-count the SAME figure once per
        # containing element, not once per actual cell.
        MONEY_CELL_JS = r"""
            el => {
                const cells = Array.from(el.querySelectorAll('td, span'))
                    .filter(c => c.children.length === 0 && /\d\.\d{3}\)?$/.test((c.textContent||'').trim()));
                const missingNum = cells.filter(c => !c.closest('.num'));
                const misaligned = cells.filter(c => {
                    const a = getComputedStyle(c.closest('.num') || c).textAlign;
                    return a !== 'right' && a !== 'end';
                });
                return {
                    count: cells.length,
                    missing: missingNum.map(c => c.textContent.trim()),
                    misaligned: misaligned.map(c => c.textContent.trim()),
                };
            }
        """

        go(page, "balance-sheet")
        page.wait_for_selector("#bs-body", timeout=10000)
        page.wait_for_timeout(700)
        bs = page.eval_on_selector("#bs-body", MONEY_CELL_JS)
        assert bs["count"] > 0, "no money-looking cell (a 3dp amount) found in #bs-body"
        assert not bs["missing"], (
            f"Balance Sheet: {len(bs['missing'])} of {bs['count']} money-looking cell(s) carry no "
            f".num ancestor: {bs['missing']}")
        assert not bs["misaligned"], (
            f"Balance Sheet: {len(bs['misaligned'])} of {bs['count']} money-looking cell(s) are not "
            f"right/end-aligned in LTR: {bs['misaligned']}")

        go(page, "cash-flow")
        page.wait_for_selector("#cf-body", timeout=10000)
        page.wait_for_timeout(700)
        cf = page.eval_on_selector("#cf-body", MONEY_CELL_JS)
        assert cf["count"] > 0, "no money-looking cell (a 3dp amount) found in #cf-body"
        assert not cf["missing"], (
            f"Cash Flow: {len(cf['missing'])} of {cf['count']} money-looking cell(s) carry no "
            f".num ancestor: {cf['missing']}")
        assert not cf["misaligned"], (
            f"Cash Flow: {len(cf['misaligned'])} of {cf['count']} money-looking cell(s) are not "
            f"right/end-aligned in LTR: {cf['misaligned']}")
        go_pos(page)

    check("R3-15c, item #15(c), round 3 rewrite: every MONEY-LOOKING cell on the Balance Sheet and "
          "Cash Flow screens (found by its own rendered text, not by already carrying .num) has a "
          ".num ancestor and computes text-align right/end in LTR", item15c_num_balance_sheet_cash_flow_r3)

    # -------------------------------------------------------------------
    # (g) item #17, Arabic -- ROUND 3 REWRITE (U1(g), verifier finding on
    # the round-2 version of this scenario, kept in git history rather
    # than here): "either is fine" (accepting the untranslated English
    # fallback) was the whole hole. round2_result.json's own stated reason
    # -- "this crew's own new Arabic strings are pending the end-of-phase
    # merge" -- is no longer true by round 3: the merge HAS landed
    # (measure_r3 p17_arabic_audit: 50 real rows, 0 Latin-only action/
    # entity cells, 0 Latin-only filter options, on the live shipped
    # catalogue). Verified live (probe4 demo_R3_17): deleting 'Cheque
    # created'/'Purchase order created'/'Cheque'/'Purchase order' from the
    # running AuraI18n.dicts.ar left the action/entity cells and the
    # filter option reading in PLAIN ENGLISH on an Arabic page, and the
    # round-2 assertions below (not the raw code, some row matches the
    # filter option, no row contains the raw code) still ALL PASSED --
    # none of them can tell "translated" from "English fallback" apart,
    # because an untranslated t() call is a legitimate different-shaped
    # PASS for every one of those checks. This version instead resolves
    # the ENGLISH label via _auditActionLabel/_auditEntityLabel with the
    # language forced to 'en' (never a hand-typed literal, so it can never
    # drift from the catalogue source itself), looks that English label up
    # in the live AuraI18n.dicts.ar directly, and asserts the RENDERED
    # Arabic cell/filter text equals THAT Arabic value byte-for-byte -- a
    # test that cannot pass on an English fallback, because English is
    # never equal to a non-empty Arabic string. Also checks the ENTITY
    # column for the first time (round 2 only ever checked Action) and
    # scans every action/entity cell and filter option for ANY Latin-only
    # text, not merely the raw SCREAMING_SNAKE code.
    # -------------------------------------------------------------------
    def item17_arabic_cheque_po_catalogue_r3():
        _, sup_body = api(page, "POST", "/api/sub/retail/suppliers", {"name": "R3 Arabic Audit Supplier"})
        sup_id = ((sup_body or {}).get("data") or {}).get("id")
        assert sup_id, f"supplier for the Arabic audit-catalogue test was not created: {sup_body}"
        _, po_body = api(page, "POST", "/api/sub/retail/purchase-orders", {
            "supplier_id": sup_id, "items": [{"product_id": PID_1, "quantity": 1, "unit_cost": 4}],
        })
        assert po_body.get("status") == "success", f"R3 Arabic-audit PO was not created: {po_body}"

        _, cheque_body = api(page, "POST", "/api/sub/retail/cheques", {
            "direction": "out", "party_id": sup_id, "amount": 50,
            "cheque_number": "R3CHQ1", "due_date": "2027-02-01",
        })
        assert cheque_body.get("status") == "success", f"R3 Arabic-audit cheque was not created: {cheque_body}"

        for action in ("CHEQUE_CREATED", "PO_CREATED"):
            _, audit_body = api(page, "GET", f"/api/sub/retail/audit-log?action={action}&limit=1")
            rows = audit_body.get("data") or []
            assert rows, f"no {action} audit row came back for the fixture this scenario just created: {audit_body}"

        has_cheque_key = page.evaluate(
            "() => Object.prototype.hasOwnProperty.call(RetailSystem._AUDIT_ACTION_LABEL, 'CHEQUE_CREATED')")
        assert has_cheque_key, "CHEQUE_CREATED is not a key in RetailSystem._AUDIT_ACTION_LABEL"

        # (action, entity) pairs this scenario's own fixture just created --
        # entity strings match retail_api.py's own _audit() calls exactly
        # (PO_CREATED: 'purchase_order'; 'CHEQUE_'+event_type: 'cheque').
        CASES = (("CHEQUE_CREATED", "cheque"), ("PO_CREATED", "purchase_order"))

        # English labels resolved from the LIVE catalogue functions
        # themselves (never a hand-typed literal, so this can never drift
        # from what _auditActionLabel/_auditEntityLabel actually return),
        # with AuraI18n.current forced to 'en' for the duration regardless
        # of the till's own current language -- both functions route
        # through t(), which reads that field.
        labels_en = page.evaluate(
            """(cases) => {
                const prevLang = AuraI18n.current;
                AuraI18n.current = 'en';
                try {
                    return cases.map(([action, entity]) => ([
                        RetailSystem._auditActionLabel(action),
                        RetailSystem._auditEntityLabel(entity),
                    ]));
                } finally { AuraI18n.current = prevLang; }
            }""", list(CASES))

        # Round 3 (U1(g), verifier finding): the decisive step round 2
        # never took -- each English label must actually BE a key in the
        # live Arabic dictionary. If it is not, the Arabic-equality
        # assertions below would compare rendered text against the
        # ENGLISH label itself (AuraI18n.t's own documented fallback:
        # "(text in d) ? d[text] : text"), which would make this test pass
        # on a completely untranslated screen -- exactly the round-2 hole.
        arabic_labels = page.evaluate(
            "(labels) => labels.map(l => AuraI18n.dicts.ar[l] || null)",
            [lbl for pair in labels_en for lbl in pair])
        for i, (action, entity) in enumerate(CASES):
            action_en, entity_en = labels_en[i]
            action_ar, entity_ar = arabic_labels[i * 2], arabic_labels[i * 2 + 1]
            assert action_ar, (
                f"[{action}] {action_en!r} has no entry in the live AuraI18n.dicts.ar -- the Arabic "
                f"catalogue this item depends on is missing this action")
            assert entity_ar, (
                f"[{entity}] {entity_en!r} has no entry in the live AuraI18n.dicts.ar -- the Arabic "
                f"catalogue this item depends on is missing this entity")

        LATIN_RE = r"[A-Za-z]"
        ARABIC_RE = r"[؀-ۿݐ-ݿ]"

        def assert_no_latin_only(where, text):
            if re.search(LATIN_RE, text) and not re.search(ARABIC_RE, text):
                raise AssertionError(f"[{where}] is Latin-only in Arabic: {text!r}")

        try:
            set_lang(page, "ar")
            go(page, "audit-log")
            page.wait_for_selector("#aud-table", timeout=10000)
            page.wait_for_timeout(600)

            # Item #17's own wording, checked directly rather than only
            # inferred from the per-action equality checks below: the
            # ENTITY filter's own options must carry no Latin-only text
            # either (round 2 never looked at this select at all).
            entity_option_texts = page.eval_on_selector_all(
                "#aud-entity option", "els => els.map(e => e.textContent)")
            for txt in entity_option_texts:
                assert_no_latin_only("#aud-entity option", txt)

            for i, (action, entity) in enumerate(CASES):
                action_en, entity_en = labels_en[i]
                action_ar, entity_ar = arabic_labels[i * 2], arabic_labels[i * 2 + 1]

                page.evaluate(
                    "(a) => { document.getElementById('aud-action').value=a; "
                    "RetailSystem._applyAuditFilters(); }", action)
                page.wait_for_timeout(500)

                cells = page.eval_on_selector_all(
                    "#aud-table tbody tr",
                    "els => els.map(e => ({ action: (e.children[2]||{}).textContent||'', "
                    "entity: (e.children[3]||{}).textContent||'' }))")
                assert cells, f"[{action}] no rows rendered for this filter"

                option_text = page.eval_on_selector(
                    f'#aud-action option[value="{action}"]', "el => el.textContent") or ""

                # The decisive claim round 2 never made: the RENDERED text
                # equals the ARABIC catalogue value, not merely "differs
                # from the raw code". An untranslated English fallback can
                # never satisfy this: action_ar/entity_ar are non-empty
                # Arabic strings (asserted above), and an English sentence
                # is never equal to one.
                assert option_text.strip() == action_ar, (
                    f"[{action}] filter option reads {option_text!r}, expected the Arabic catalogue "
                    f"value {action_ar!r} for the English label {action_en!r}")
                assert any(c["action"].strip() == action_ar for c in cells), (
                    f"[{action}] no row's action cell equals the Arabic catalogue value {action_ar!r} "
                    f"-- cells: {cells}")
                assert any(c["entity"].strip() == entity_ar for c in cells), (
                    f"[{entity}] no row's entity cell equals the Arabic catalogue value {entity_ar!r} "
                    f"for the English label {entity_en!r} (round 2 never checked the entity column at "
                    f"all) -- cells: {cells}")

                assert_no_latin_only(f"[{action}] filter option", option_text)
                for c in cells:
                    assert_no_latin_only(f"[{action}] a row's action cell", c["action"])
                    assert_no_latin_only(f"[{action}] a row's entity cell", c["entity"])
                assert not any(action in c["action"] for c in cells), (
                    f"[{action}] a row's action cell contains the raw action code: {cells}")

            # Mutation-prove THIS scenario's own decisive check (contracts.md
            # rule 4), not just the product code: deleting ONE key from the
            # live Arabic dictionary must turn the equality assertion above
            # RED. Restored in a try/finally so no later scenario ever sees
            # the dictionary with a key missing, whatever happens above.
            mutate_action, _mutate_entity = CASES[0]
            mutate_key = labels_en[0][0]  # 'Cheque created'
            page.evaluate(
                "(k) => { window.__r3_saved_ar = AuraI18n.dicts.ar[k]; delete AuraI18n.dicts.ar[k]; }",
                mutate_key)
            try:
                page.evaluate(
                    "(a) => { document.getElementById('aud-action').value=a; "
                    "RetailSystem._applyAuditFilters(); }", mutate_action)
                page.wait_for_timeout(500)
                mutated_cells = page.eval_on_selector_all(
                    "#aud-table tbody tr", "els => els.map(e => (e.children[2]||{}).textContent||'')")
                # With the key deleted, AuraI18n.t() falls back to the
                # ENGLISH key itself -- the row's action cell must now read
                # 'Cheque created', never the Arabic value this scenario's
                # own assertion above required. If it did not, this
                # scenario's Arabic-equality check cannot actually catch a
                # real catalogue regression either.
                assert any(txt.strip() == mutate_key for txt in mutated_cells), (
                    f"MUTATION PROOF FAILED: with {mutate_key!r} deleted from AuraI18n.dicts.ar, no "
                    f"row's action cell fell back to the English key -- cells: {mutated_cells}")
                print(f"      MUTATION PROOF (R3-17): deleting {mutate_key!r} from AuraI18n.dicts.ar "
                      f"-> action cell reads {mutate_key!r} (English fallback) -- RED confirmed")
            finally:
                page.evaluate(
                    "(k) => { if (window.__r3_saved_ar !== undefined) AuraI18n.dicts.ar[k] = window.__r3_saved_ar; "
                    "delete window.__r3_saved_ar; }", mutate_key)
                page.evaluate(
                    "(a) => { document.getElementById('aud-action').value=a; "
                    "RetailSystem._applyAuditFilters(); }", mutate_action)
                page.wait_for_timeout(400)
                restored_cells = page.eval_on_selector_all(
                    "#aud-table tbody tr", "els => els.map(e => (e.children[2]||{}).textContent||'')")
                assert any(txt.strip() == arabic_labels[0] for txt in restored_cells), (
                    f"MUTATION RESTORE FAILED: after restoring {mutate_key!r} in AuraI18n.dicts.ar, no "
                    f"row's action cell went back to the Arabic value {arabic_labels[0]!r} -- "
                    f"cells: {restored_cells}")
        finally:
            page.evaluate("() => RetailSystem._clearAuditFilters()")
            set_lang(page, "en")
            go_pos(page)

    check("R3-17, item #17 (Arabic, cheque + PO), round 3 rewrite: the rendered action cell, entity "
          "cell and filter option for CHEQUE_CREATED/cheque and PO_CREATED/purchase_order each equal "
          "the LIVE Arabic catalogue value for their English label (never merely 'differs from the raw "
          "code', which an untranslated English fallback also satisfies), no cell or filter option is "
          "Latin-only, and the check is itself mutation-proven by deleting one catalogue key at runtime "
          "and restoring it", item17_arabic_cheque_po_catalogue_r3)

    # -------------------------------------------------------------------
    # (h) item #5: a hostile SKU (double quote + <img onerror>) round-trips
    # safely in sale detail AND PO detail -- item #5's own test only ever
    # covers the NAME field; T4 covers name/variant_label/notes/supplier,
    # never a hostile SKU. round2_result.json: "No fixture anywhere uses a
    # hostile SKU ... neither SKU site is guarded."
    # -------------------------------------------------------------------
    def item5_hostile_sku_sale_and_po_detail_r3():
        page.evaluate("() => { window.__t4XssFired = false; }")
        _, sku_prod_body = api(page, "POST", "/api/sub/retail/products", {
            "name": "R3 Hostile SKU Product", "sku": XSS_PAYLOAD, "sell_price": 8,
            "cost_price": 4, "category_id": None, "unit": "pc",
        })
        sku_pid = ((sku_prod_body or {}).get("data") or {}).get("id")
        assert sku_pid, f"hostile-SKU product was not created: {sku_prod_body}"
        api(page, "POST", f"/api/sub/retail/products/{sku_pid}/stock-adjust",
            {"quantity": 20, "reason": "R3 hostile sku seed"})

        go_pos(page)
        page.evaluate("() => RetailSystem._clearCart()")
        page.evaluate("(id) => RetailSystem._addToCart(id)", sku_pid)
        page.wait_for_timeout(300)
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        sku_sale_id = page.evaluate("() => RetailSystem._lastSaleData.id")
        page.click('#ret-receipt-dialog button:has-text("New Sale")')
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        go(page, "sales")
        page.wait_for_selector("#sh-table", timeout=10000)
        page.evaluate("(id) => RetailSystem._viewSale(id)", sku_sale_id)
        page.wait_for_selector("#ret-sale-dialog", timeout=8000)
        page.wait_for_timeout(300)
        sku_cell_text = page.eval_on_selector(
            "#ret-sale-dialog table.ret-table tbody tr td:nth-child(2)", "el => el.textContent")
        assert sku_cell_text == XSS_PAYLOAD, (
            f"sale-detail SKU cell text was {sku_cell_text!r}, expected the literal {XSS_PAYLOAD!r}")
        has_img_sale = page.eval_on_selector(
            "#ret-sale-dialog table.ret-table tbody tr td:nth-child(2)",
            "el => !!el.querySelector('img[src=\"x\"]')")
        assert not has_img_sale, "sale-detail SKU cell rendered a real <img> element"
        assert not page.evaluate("() => window.__t4XssFired"), "sale-detail SKU: the onerror payload EXECUTED"
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-sale-dialog", state="detached", timeout=5000)

        _, sup_body = api(page, "POST", "/api/sub/retail/suppliers", {"name": "R3 Hostile SKU Supplier"})
        sup_id = ((sup_body or {}).get("data") or {}).get("id")
        _, po_body = api(page, "POST", "/api/sub/retail/purchase-orders", {
            "supplier_id": sup_id, "items": [{"product_id": sku_pid, "quantity": 3, "unit_cost": 4}],
        })
        assert po_body.get("status") == "success", f"hostile-SKU PO was not created: {po_body}"
        po_id = ((po_body or {}).get("data") or {}).get("id")

        page.evaluate("() => { window.__t4XssFired = false; }")
        page.evaluate("(id) => RetailSystem._viewPO(id)", po_id)
        page.wait_for_selector("#ret-po-view-dialog", timeout=8000)
        page.wait_for_timeout(300)
        po_sku_text = page.eval_on_selector(
            "#ret-po-view-dialog table.ret-table tbody tr td:nth-child(2)", "el => el.textContent")
        assert po_sku_text == XSS_PAYLOAD, (
            f"PO-detail SKU cell text was {po_sku_text!r}, expected the literal {XSS_PAYLOAD!r}")
        has_img_po = page.eval_on_selector("#ret-po-view-dialog", "el => !!el.querySelector('img[src=\"x\"]')")
        assert not has_img_po, "PO-detail SKU cell rendered a real <img> element"
        assert not page.evaluate("() => window.__t4XssFired"), "PO-detail SKU: the onerror payload EXECUTED"
        page.keyboard.press("Escape")
        page.wait_for_selector("#ret-po-view-dialog", state="detached", timeout=5000)
        go_pos(page)

    check("R3-5sku, item #5: a hostile SKU (double-quote + <img onerror>) round-trips the exact "
          "literal string, with no injected <img> and no onerror execution, in BOTH sale detail and "
          "PO detail", item5_hostile_sku_sale_and_po_detail_r3)

    # -------------------------------------------------------------------
    # (i) item #19/_paymentMethodLabel: a hostile payment_method posted
    # through the API is escaped on the DASHBOARD's Recent Transactions
    # badge, not only the Returns list (already covered by the round-2 R2
    # scenario above via refund_method's default-from-payment_method path).
    # -------------------------------------------------------------------
    def item19_dashboard_payment_method_xss_r3():
        page.evaluate("() => { window.__r3PmXssFired = false; }")
        hostile_method = '<img src=x onerror="window.__r3PmXssFired=true">'
        _, sale_body = api(page, "POST", "/api/sub/retail/sales", {
            "items": [{"product_id": PID_1, "quantity": 1, "unit_price": 10, "line_total": 10}],
            "payment_method": hostile_method, "amount_paid": 10,
        })
        assert sale_body.get("status") == "success", (
            f"hostile-payment_method sale was not created (server-side validation may have changed -- "
            f"if so, this finding may already be closed upstream): {sale_body}")
        sale_id = ((sale_body or {}).get("data") or {}).get("id")
        assert sale_id, f"hostile-payment_method sale id missing: {sale_body}"

        go(page, "dashboard")
        page.wait_for_timeout(1000)
        dash_row_sel = f'#sub-content tr[onclick*="_viewSale({sale_id})"]'
        dash_has_img = page.eval_on_selector(dash_row_sel, "el => !!el.querySelector('img[src=\"x\"]')")
        assert not dash_has_img, (
            "the Dashboard's Recent Transactions row rendered a real <img> element for a hostile "
            "payment_method")
        assert not page.evaluate("() => window.__r3PmXssFired"), (
            "the Dashboard's payment-method badge executed the onerror payload")
        dash_row_html = page.eval_on_selector(dash_row_sel, "el => el.innerHTML")
        import html as _html
        assert hostile_method in _html.unescape(dash_row_html), (
            f"the Dashboard's payment-method badge did not round-trip the hostile string. Row HTML: "
            f"{dash_row_html!r}")
        go_pos(page)

    check("R3-19dash, a hostile payment_method posted through the API is escaped (no injected <img>, "
          "no fired onerror) on the Dashboard's Recent Transactions payment badge, not only the "
          "Returns list", item19_dashboard_payment_method_xss_r3)

    # -------------------------------------------------------------------
    # (k) review fix item #7(b)/#8: round2_result.json found the existing
    # "review fix, item #7(b)/#8" check's >=40px assertion vacuous ("the
    # round-1 RED ... no longer reproduces at this scenario's viewport").
    #
    # INVESTIGATED LIVE, not assumed (ENGINEERING.md: run the real artefact).
    # Measured against the REAL running app, with the CSS fix's own line
    # (subsystem-retail.js's `.pos-line-main { flex:1;min-inline-size:60px }`)
    # toggled by hand: at every viewport tried (1120x1000, 1366x768,
    # 1440x900) and with BOTH a short promoted name (Widget) and a long one
    # (32 chars), removing `min-inline-size:60px` entirely (falling back to
    # the flex default `min-width:auto`) does NOT shrink the rendered name
    # below 40px -- it renders WIDER (measured 137px against 60-87.8px WITH
    # the explicit floor present), because `.pos-line-main`'s OTHER children
    # (`.pos-item-meta`'s struck-price line, `.pos-promo-tag`) now ALWAYS
    # contribute a larger nowrap min-content run than 60px, and a plain
    # block container's automatic minimum size is the MAX of every child's
    # own min-content -- not just `.pos-item-name`'s. Both the promo-tag's
    # own `overflow:hidden` (added after item #7(b)'s row-height fix,
    # `.pos-promo-tag { overflow:hidden }`) and the up-front `.money`
    # nowrap/tabular-nums utility class postdate whatever DOM shape the
    # round-1 defect was originally measured against, and together they now
    # make `min-inline-size:60px` REDUNDANT for the specific "name < 40px"
    # collapse: the codebase has moved on, and no fixture this crew can
    # construct reproduces that exact geometric failure any more.
    #
    # Given that, this proves the one thing that IS still real and still
    # mutation-provable: the PROTECTIVE DECLARATION ITSELF is present and
    # active (a future refactor that silently drops it is exactly the kind
    # of regression this item exists to catch, even though today's DOM
    # shape has other, newer protections too) -- `getComputedStyle(...)
    # .minWidth` (the physical property `min-inline-size` resolves to in
    # this LTR page) must read the authored '60px', not the browser's
    # 'auto' default. The >=40px geometric measurement stays alongside it
    # as a live safety net, honestly noted as unable to currently
    # distinguish fixed-from-broken given the DOM shape above.
    #
    # ROUND 3 CORRECTION (U1(k), verifier finding): "unreproducible
    # ANYWHERE" above was WRONG -- the investigation only tried
    # 1120x1000/1366x768/1440x900. At 1280x720 (added to this loop below),
    # `min-inline-size:0` DOES reproduce a real collapse: the promoted
    # name measured 25.8px, against 59.1-87.8px at the wider two
    # viewports this loop already covered (probe4 demo_R3_7b). The
    # verifier also found this test's own mutation history had drifted to
    # the WRONG broken state: an investigation step REMOVED the
    # declaration entirely (falling back to `min-width:auto`) rather than
    # restoring ec6a2850's real pre-fix value, and that removed-entirely
    # state causes a DIFFERENT regression this test never looked at --
    # the name balloons (299.8px measured) and pushes `.pos-line-total`
    # outside the cart, at every viewport, not just the narrow one. Both
    # gaps are closed below: a 1280x720 step, and a geometric assertion
    # that catches ballooning as well as collapse.
    # -------------------------------------------------------------------
    def item7b_promoted_line_width_floor_r3():
        for (w, h) in ((1366, 768), (1440, 900), (1280, 720)):
            page.set_viewport_size({"width": w, "height": h})
            go_pos(page)
            page.evaluate("() => RetailSystem._clearCart()")
            for pid in (PID_1, PID_2, PID_R2PROMO, PID_4):
                page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
            page.select_option("#pos-customer", CUST_ID)
            page.fill("#pos-disc", "5")
            page.click('.pos-pay-btn[data-method="cash"]')
            page.fill("#pos-tendered", "100")
            page.wait_for_timeout(400)

            has_struck = page.locator("#pos-cart .pos-price-orig").count() > 0
            assert has_struck, f"[{w}x{h}] no promoted line in this cart -- the fixture did not actually create one"

            min_width = page.eval_on_selector(
                "#pos-cart .pos-line-main", "el => getComputedStyle(el).minWidth")
            assert min_width == "60px", (
                f"[{w}x{h}] .pos-line-main's computed min-width is {min_width!r}, expected the "
                f"authored '60px' floor (item #7(b)'s own `min-inline-size:60px`) -- got the browser's "
                f"'auto' default, meaning the declaration has been dropped or overridden")

            name_width = page.eval_on_selector(
                "#pos-cart .pos-cart-row:has(.pos-price-orig) .pos-item-name",
                "el => el.getBoundingClientRect().width")
            assert name_width >= 40, (
                f"[{w}x{h}] the promoted line's product name is only {name_width:.1f}px wide "
                f"(expected >= 40px)")

            # Round 3 (U1(k)): the OTHER half a dropped/removed floor
            # breaks -- ballooning, not collapsing. .pos-line-total's full
            # rect must stay inside #pos-cart, and the row must never
            # scroll horizontally past its own container.
            cart_box = page.eval_on_selector("#pos-cart", "el => el.getBoundingClientRect().toJSON()")
            line_total_box = page.eval_on_selector(
                "#pos-cart .pos-cart-row:has(.pos-price-orig) .pos-line-total",
                "el => el.getBoundingClientRect().toJSON()")
            assert (line_total_box["left"] >= cart_box["left"] - 0.5
                    and line_total_box["right"] <= cart_box["right"] + 0.5), (
                f"[{w}x{h}] the promoted line's .pos-line-total ({line_total_box}) is not fully inside "
                f"#pos-cart ({cart_box}) -- pushed outside it")
            row_overflow = page.eval_on_selector(
                "#pos-cart .pos-cart-row:has(.pos-price-orig)", "el => el.scrollWidth - el.clientWidth")
            assert row_overflow <= 1, (
                f"[{w}x{h}] the promoted line's row overflows its own container by {row_overflow}px "
                f"(scrollWidth - clientWidth) -- it should shrink to fit, never scroll horizontally")

        page.evaluate("() => RetailSystem._clearCart()")
        page.fill("#pos-disc", "0")
        page.set_viewport_size({"width": 1440, "height": 900})
        go_pos(page)

    check("R3-7bwidth, review fix item #7(b)/#8: .pos-line-main's authored min-inline-size:60px floor "
          "(computed min-width) is present at 1366x768/1440x900/1280x720, the promoted line's product "
          "name stays >= 40px wide, .pos-line-total stays fully inside #pos-cart, and the row never "
          "overflows it horizontally", item7b_promoted_line_width_floor_r3)

    # -------------------------------------------------------------------
    # U3 -- the native customer-display window's title, in the till's own
    # language: window.pywebview.api.open_customer_display(title) now gets
    # a title built from t('Customer Display') (already catalogued in both
    # locales -- see 'Customer Display' in locales/en.json and ar.json,
    # 'شاشة الزبون'), prefixed by the shop's own name (via
    # _customerDisplayShopInfo(), the SAME branding-or-signup resolver the
    # relay payload already uses -- interface I-3) when one is known. Built
    # against the desktop fixer's own parallel `open_customer_display(
    # title=None, shop_name=None)` signature. Proves BOTH directions: a
    # known shop name is prefixed, and an UNKNOWN one (mocked here via
    # _customerDisplayShopInfo, since this scenario's own shop always HAS a
    # signup name -- a second fresh install is out of scope for this single
    # E2E script) falls back to the bare translated title.
    # -------------------------------------------------------------------
    def item_u3_native_window_title_r3():
        shop_name = page.evaluate("() => RetailSystem._customerDisplayShopInfo().name")
        assert shop_name, (
            f"fixture assumption broken: this scenario's shop has no resolvable name "
            f"({shop_name!r}) -- the 'known name' half of this test needs one")

        # (a) shop name known -- prefixed.
        result_known = page.evaluate("""
            () => {
                let capturedArgs = null;
                window.pywebview = { api: { open_customer_display: (...args) => { capturedArgs = args; } } };
                RetailSystem._openCustomerDisplay();
                delete window.pywebview;
                return capturedArgs;
            }
        """)
        expected_known = f"{shop_name} — Customer Display"
        assert result_known and len(result_known) >= 1, (
            f"open_customer_display was called with no arguments at all: {result_known}")
        assert result_known[0] == expected_known, (
            f"(a) known shop name: native window title was {result_known[0]!r}, expected "
            f"{expected_known!r}")

        # (b) shop name UNKNOWN (mocked) -- bare translated title, no dangling separator.
        result_unknown = page.evaluate("""
            () => {
                const orig = RetailSystem._customerDisplayShopInfo;
                RetailSystem._customerDisplayShopInfo = () => ({ name: null, lang: 'en', theme: 'light' });
                let capturedArgs = null;
                window.pywebview = { api: { open_customer_display: (...args) => { capturedArgs = args; } } };
                RetailSystem._openCustomerDisplay();
                delete window.pywebview;
                RetailSystem._customerDisplayShopInfo = orig;
                return capturedArgs;
            }
        """)
        assert result_unknown and result_unknown[0] == "Customer Display", (
            f"(b) unknown shop name: native window title was {result_unknown[0]!r}, expected the bare "
            f"'Customer Display' with no dangling em dash or shop-name prefix")

    check("R3-U3, native window title: window.pywebview.api.open_customer_display() is called with a "
          "title prefixed by the shop's own name when known, and the bare translated 'Customer Display' "
          "when it is not", item_u3_native_window_title_r3)

    ctx.close()
    browser.close()

server.shutdown()
thread.join(timeout=5)

passed = sum(1 for _, ok in RESULTS if ok)
total = len(RESULTS)
print("\n" + "=" * 70)
print(f"RESULTS: {passed}/{total} passed")
for name, ok in RESULTS:
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")

if passed != total:
    sys.exit(1)
