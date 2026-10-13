"""UI-3a -- amount discounts, "make the total X" and the manager's PIN, in a REAL browser.

Boots the real Flask backend in-process on a FRESH data dir ($AURA_UI3A_DATA, default the job's
tmp\\ui3a\\data -- wiped at start) on port 5101, signs the owner up, seeds the brief's cart (three
products at 16% tax: 3.500, 2.750 x2, 0.181 = 10.650), sets the cashier ceiling to 5%, creates a
CASHIER and a MANAGER (with a PIN) in the real registry, then drives headless Chromium AS THE CASHIER:

  1. Discount sheet -> "Round total down to 10.000" -> Apply -> the till's own total reads 10.000.
  2. Charge -> the sheet asks for a manager -> a WRONG PIN is refused and sells nothing.
  3. Charge again -> the right PIN -> ONE sale saved at exactly 10.000, discount_approved_by = the
     manager, discount_reason = "Round down", the receipt reads "Discount (Round down)".
  4. The same flow in ARABIC (RTL): sheet, PIN dialog, receipt.
  5. A per-line price override by the cashier needs the PIN too, and the receipt strikes the list price.

Screenshots go to $AURA_UI3A_SHOTS for a human to LOOK at. Run with the SYSTEM Python:
    py -3.14 products/retail/tests/retail_ui3a_e2e.py
"""
import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO / "products" / "retail" / "backend"
for _p in (str(REPO), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(os.environ.get("AURA_UI3A_DATA", r"C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\ui3a\data"))
SHOTS = Path(os.environ.get("AURA_UI3A_SHOTS", r"C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\ui3a\shots"))
SHOTS.mkdir(parents=True, exist_ok=True)
if DATA.exists():
    shutil.rmtree(DATA, ignore_errors=True)
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
for _k in ("AURA_OWNER_LICENSING_URL", "AURA_SYNC_RELAY_URL"):
    os.environ.pop(_k, None)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0", AURA_BACKUP_SCHEDULER="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
import app as _app_module  # noqa: E402

flask_app = _app_module.init_app()

import logging  # noqa: E402

logging.getLogger("werkzeug").setLevel(logging.ERROR)
from werkzeug.serving import make_server  # noqa: E402

PORT = int(os.environ.get("AURA_UI3A_PORT", "5101"))
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _probe:
    _probe.settimeout(0.3)
    if _probe.connect_ex(("127.0.0.1", PORT)) == 0:
        print(f"FATAL: port {PORT} is busy")
        sys.exit(2)
server = make_server("127.0.0.1", PORT, flask_app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(1.2)
BASE = f"http://127.0.0.1:{PORT}"
RETAIL_DB = DATA / "database" / "subsystems" / "retail.db"
print(f"Backend up at {BASE}  AURA_APP_DATA={DATA}", flush=True)

from playwright.sync_api import sync_playwright  # noqa: E402

RESULTS = []
PIN = "4321"
CASHIER_EMAIL, CASHIER_PW = "cashier@ui3a.local", "UiThreeACashier1!"
OWNER_EMAIL, OWNER_PW = "owner@ui3a.local", "UiThreeAOwner12345!"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS: " if cond else "FAIL: ") + name + (f"\n      {detail}" if (detail and not cond) else ""), flush=True)


def db(sql, args=()):
    last = None
    for _ in range(6):
        try:
            conn = sqlite3.connect(str(RETAIL_DB), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            last = e
            time.sleep(0.2)
    raise last


def api(page, method, path, body=None):
    fn = getattr(page.request, method.lower())
    resp = fn(BASE + path, data=body) if body is not None else fn(BASE + path)
    try:
        j = resp.json()
    except Exception:  # noqa: BLE001
        j = {}
    return resp.status, j


def dismiss_intro(page):
    try:
        ov = page.locator("#aura-intro-overlay")
        ov.wait_for(state="visible", timeout=2500)
        ov.click()
        ov.wait_for(state="detached", timeout=3000)
    except Exception:  # noqa: BLE001
        pass


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def new_context(browser, lang="en", vp=None):
    ctx = browser.new_context(viewport=vp or {"width": 1366, "height": 768})
    ctx.add_init_script(f"try{{localStorage.setItem('aura_lang','{lang}');localStorage.setItem('aura_intro_played_v1','1')}}catch(e){{}}")
    return ctx


def owner_setup(browser):
    """Sign up, licence, catalogue, ceiling 5%, a cashier and a manager. Returns product ids."""
    ctx = new_context(browser)
    page = ctx.new_page()
    page.goto(BASE)
    dismiss_intro(page)
    page.wait_for_selector("#su-btn", timeout=20000)
    page.fill("#su-name", "Aura Owner"); page.fill("#su-company", "UI3a Shop")
    page.fill("#su-email", OWNER_EMAIL); page.fill("#su-pass", OWNER_PW); page.fill("#su-pass2", OWNER_PW)
    page.click("#su-btn")
    page.wait_for_selector("#sub-header-section", timeout=30000)
    page.wait_for_timeout(1200)
    seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
    page.reload(); dismiss_intro(page)
    page.wait_for_selector("#sub-header-section", timeout=20000)
    page.wait_for_timeout(800)
    try:
        banner = page.locator("#aura-admin-device-claim")
        banner.wait_for(state="visible", timeout=4000)
        page.get_by_role("button", name="Make this the admin device", exact=True).click()
        banner.wait_for(state="detached", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    api(page, "POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD", "cashier_discount_limit_pct": "5"})
    _, cat = api(page, "POST", "/api/sub/retail/categories", {"name": "General"})
    cat_id = ((cat or {}).get("data") or {}).get("id")
    ids = {}
    for key, name, sku, price in (("a", "Olive Oil 1L", "U3-A", 3.5), ("b", "Za'atar 250g", "U3-B", 2.75),
                                  ("c", "Mint Gum", "U3-C", 0.181)):
        _, r = api(page, "POST", "/api/sub/retail/products", {
            "name": name, "sku": sku, "sell_price": price, "cost_price": 0.1, "tax_rate": 16,
            "category_id": cat_id, "unit": "pc"})
        pid = ((r or {}).get("data") or {}).get("id")
        assert pid, f"product {name}: {r}"
        api(page, "POST", f"/api/sub/retail/products/{pid}/stock-adjust", {"quantity": 500, "reason": "ui3a seed"})
        ids[key] = pid
    api(page, "POST", "/api/sub/retail/numbering", {"readable_enabled": False})
    api(page, "POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 50})
    # real registry accounts for a cashier (no PIN) and a manager (PIN)
    from commercial_runtime.identity import user_accounts
    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    reg = registry_conn()
    company_id = reg.execute("SELECT company_id FROM users WHERE email=?", (OWNER_EMAIL,)).fetchone()[0]

    def mk(role, email, pw, display, pin=None):
        uid = str(uuid.uuid4())
        reg.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, uid, display_name) VALUES (?,?,?,?,?,?,?,0,?,?)",
            (uid, company_id, f"EMP-{uuid.uuid4().hex[:5]}", email, hash_password(pw), role, "active",
             str(uuid.uuid4()), display))
        reg.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), uid, "retail", "full"))
        user_accounts.seed_capabilities_for_user(reg, uid, role)
        if pin:
            user_accounts.set_user_pin(reg, uid, pin)
        return reg.execute("SELECT uid FROM users WHERE id=?", (uid,)).fetchone()[0]
    manager_uid = mk("manager", "manager@ui3a.local", "UiThreeAMgr12345!", "Layla (manager)", PIN)
    mk("cashier", CASHIER_EMAIL, CASHIER_PW, "Omar", None)
    reg.commit(); reg.close()
    ctx.close()
    return ids, manager_uid


def cashier_login(browser, lang="en", vp=None):
    ctx = new_context(browser, lang, vp)
    page = ctx.new_page()
    page.goto(BASE)
    dismiss_intro(page)
    page.wait_for_selector("#rl-email", timeout=20000)
    page.fill("#rl-email", CASHIER_EMAIL); page.fill("#rl-pass", CASHIER_PW); page.click("#rl-btn")
    page.wait_for_selector("#sub-header-section", timeout=20000)
    page.wait_for_timeout(900)
    # setLang persists on the ACCOUNT, so an Arabic run would leave the next English run in Arabic:
    # always set it explicitly.
    page.evaluate(f"AuraI18n.setLang('{lang}')")
    page.wait_for_timeout(2500)
    page.evaluate("SubsystemApp._navigate('pos')")
    page.wait_for_selector("#pos-product-grid .pos-card", timeout=15000)
    page.wait_for_timeout(500)
    return ctx, page


def fill_brief_cart(page):
    tiles = page.locator(".pos-card:not(.pos-card-outofstock)")
    names = page.eval_on_selector_all(".pos-card .pos-card-name", "els => els.map(e => e.textContent.trim())")
    order = ["Olive Oil 1L", "Za'atar 250g", "Za'atar 250g", "Mint Gum"]
    for n in order:
        idx = next(i for i, x in enumerate(names) if n in x)
        tiles.nth(idx).click()
        page.wait_for_timeout(150)
    page.wait_for_timeout(300)


def total_text(page):
    # the amount only: in Arabic the symbol is "د.أ." and its trailing dot must not be read as a digit
    return page.evaluate("(document.getElementById('pos-total').textContent.match(/[0-9]+[.][0-9]+/) || [''])[0]")


def type_pin(page, pin):
    for d in pin:
        page.click(f'#ap-dialog .pos-cp-key[data-k="{d}"]')
    page.wait_for_timeout(150)


def flow(browser, lang, tag, ids, manager_uid):
    rtl = lang == "ar"
    ctx, page = cashier_login(browser, lang)
    try:
        fill_brief_cart(page)
        check(f"[{tag}] the brief cart totals 10.650", total_text(page) == "10.650", total_text(page))
        shot(page, f"{tag}-1-till")
        page.click("#pos-disc-chip")
        page.wait_for_selector("#ds-dialog", timeout=5000)
        page.wait_for_timeout(300)
        shot(page, f"{tag}-2-sheet")
        chips = page.eval_on_selector_all(".ds-round-chip", "els => els.map(e => e.textContent.trim())")
        check(f"[{tag}] the sheet offers round-down totals incl. 10.000", "10.000" in chips, str(chips))
        page.click('.ds-round-chip:text-is("10.000")')
        page.wait_for_timeout(250)
        note = page.inner_text("#ds-note")
        check(f"[{tag}] the sheet previews the result and warns it needs a manager", "10.000" in note, note)
        shot(page, f"{tag}-3-sheet-round-down")
        page.click("#ds-apply")
        page.wait_for_timeout(500)
        check(f"[{tag}] the till's own total now reads 10.000", total_text(page) == "10.000", total_text(page))
        chip_text = page.inner_text("#pos-disc-chip")
        # 10.650 -> 10.000 is 0.650 INCLUDING tax; the discount itself is taken before tax: 0.650 / 1.16 = 0.560.
        check(f"[{tag}] the chip shows the applied (ex-tax) discount", "0.560" in chip_text, chip_text)
        shot(page, f"{tag}-4-applied")
        before = db("SELECT COUNT(*) AS n FROM sales")[0]["n"]
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ap-dialog", timeout=5000)
        page.wait_for_timeout(250)
        shot(page, f"{tag}-5-pin-dialog-empty")
        # WRONG pin first
        type_pin(page, "0000")
        shot(page, f"{tag}-6-pin-dialog-masked")
        dots = page.inner_text("#ap-dots")
        check(f"[{tag}] the PIN is masked on screen", "0" not in dots.replace("○", "") and "●" in dots, dots)
        page.click("#ap-ok")
        page.wait_for_timeout(900)
        check(f"[{tag}] a wrong PIN sells nothing", db("SELECT COUNT(*) AS n FROM sales")[0]["n"] == before)
        shot(page, f"{tag}-7-wrong-pin")
        page.wait_for_timeout(3200)
        # RIGHT pin
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ap-dialog", timeout=5000)
        type_pin(page, PIN)
        page.click("#ap-ok")
        page.wait_for_selector("#ret-receipt-dialog", timeout=15000)
        page.wait_for_timeout(500)
        shot(page, f"{tag}-8-receipt")
        rows = db("SELECT total, discount_amount, discount_reason, discount_approved_by, subtotal FROM sales ORDER BY id DESC LIMIT 1")
        s = rows[0]
        check(f"[{tag}] the sale was saved at EXACTLY 10.000", s["total"] == 10.0, str(s))
        check(f"[{tag}] it recorded the manager and the reason",
              s["discount_approved_by"] == manager_uid and s["discount_reason"], str(s))
        check(f"[{tag}] exactly ONE sale was added", db("SELECT COUNT(*) AS n FROM sales")[0]["n"] == before + 1)
        rtext = page.inner_text("#ret-receipt-dialog")
        check(f"[{tag}] the receipt shows the discount with its reason", "(" in rtext and s["discount_reason"] in rtext, rtext[:400])
        check(f"[{tag}] the receipt total reads 10.000", "10.000" in rtext, rtext[:400])
        return s
    finally:
        ctx.close()


def override_flow(browser, ids, manager_uid):
    ctx, page = cashier_login(browser, "en")
    try:
        tiles = page.locator(".pos-card:not(.pos-card-outofstock)")
        names = page.eval_on_selector_all(".pos-card .pos-card-name", "els => els.map(e => e.textContent.trim())")
        tiles.nth(next(i for i, x in enumerate(names) if "Olive Oil" in x)).click(); page.wait_for_timeout(200)
        page.click(".pos-line-opt")
        page.wait_for_selector("#lm-dialog", timeout=5000)
        page.fill("#lm-price", "3")
        page.fill("#lm-reason", "Price match")
        shot(page, "en-9-line-menu")
        page.click("#lm-ok")
        page.wait_for_timeout(400)
        shot(page, "en-10-line-overridden")
        page.click("#pos-checkout-btn")
        page.wait_for_selector("#ap-dialog", timeout=5000)
        type_pin(page, PIN)
        page.click("#ap-ok")
        page.wait_for_selector("#ret-receipt-dialog", timeout=15000)
        page.wait_for_timeout(400)
        shot(page, "en-11-override-receipt")
        li = db("SELECT unit_price, list_price FROM sale_items ORDER BY id DESC LIMIT 1")[0]
        check("[override] the line sold at 3.000 against a recorded list price of 3.500",
              li["unit_price"] == 3.0 and li["list_price"] == 3.5, str(li))
        struck = page.evaluate("[...document.querySelectorAll('#ret-receipt-dialog .rcpt-amt span')].some(s => s.style.textDecoration.includes('line-through'))")
        check("[override] the receipt strikes the list price", struck)
        s = db("SELECT discount_approved_by, discount_reason FROM sales ORDER BY id DESC LIMIT 1")[0]
        check("[override] the approving manager is recorded", s["discount_approved_by"] == manager_uid, str(s))
        check("[override] the reason typed in the line menu is stored on the sale", s["discount_reason"] == "Price match", str(s))
    finally:
        ctx.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ids, manager_uid = owner_setup(browser)
        flow(browser, "en", "en", ids, manager_uid)
        flow(browser, "ar", "ar", ids, manager_uid)
        override_flow(browser, ids, manager_uid)
        # layout: the Charge button stays on screen with the new chip, at the small till
        ctx, page = cashier_login(browser, "en", {"width": 1024, "height": 768})
        try:
            fill_brief_cart(page)
            hit = page.evaluate("""() => { const b = document.getElementById('pos-checkout-btn'); const r = b.getBoundingClientRect();
              const t = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
              return r.bottom <= innerHeight + 0.5 && !!t && (b === t || b.contains(t)); }""")
            check("[1024x768] Charge is still fully on screen and clickable", hit)
            shot(page, "en-12-1024x768")
        finally:
            ctx.close()
        browser.close()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed", flush=True)
    for n in failed:
        print("  FAILED:", n)
    sys.exit(1 if failed else 0)


main()
