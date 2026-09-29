"""P1a crew T (till) -- live-browser verification for items #1, #2, #3, #6, #8
(see contract.md and p1a-plan.json).

Boots the real Flask backend in-process on a fresh temp AURA_APP_DATA, the
same proven pattern retail_p0_till_e2e.py uses: sign up through the real
first-run form while unlicensed, THEN seed an active licence, THEN reload
(seeding before signup makes the app think it joined a shop and it hangs on
"Connecting to your shop..."). Drives a real headless Chromium via
Playwright against the ACTUAL running app, never a DOM stub.

Standalone script (contract.md's file-ownership table names it that way on
purpose), not a pytest *_test.py: prints PASS/FAIL per check, exits 1 if
anything failed.

Covers, in a real browser, with real computed style/DOM/network evidence:
  - #admin-center: a cashier gets the lock panel; the owner sees every card;
    a delegated branch manager (CAP_EMPLOYEES, not the owner role) sees only
    the two device-local cards (This Device's Branch, Document Numbering).
  - The Discount % box: gone (replaced by the quiet manager link) for a
    cashier without retail.discount; present for the owner. BOTH directions.
  - Credit (F5): greyed with its reason before the cashier ever clicks it --
    "Walk-in customer" with no customer selected, "Not a credit customer"
    once a credit_mode='none' customer is picked.
  - Products: read-only "Stock lookup" (no Add/Edit/Import/Adjust, no
    Actions column) for a cashier without retail.stock.adjust; full CRUD for
    the owner.
  - Receipt "Send on WhatsApp": window.open intercepted, the wa.me link's
    DECODED text contains every product name, the total and the short sale
    number.
  - Product tile monogram: two categories that hash to two DIFFERENT
    --cat-N-* tone indices render two DIFFERENT computed background colours.
  - Chart theme: chart-theme.js's AuraChartTheme.apply() actually mutates
    Chart.defaults from the live theme tokens, and does so again after a
    real theme switch.

Run with the SYSTEM Python (Playwright lives there):
    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_p1a_till_e2e.py
"""
import io
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(r"C:\Users\MSI\Desktop\aura-fullsuits\.claude\worktrees\retail-hardware-viewports")
BACKEND_DIR = REPO / "products" / "retail" / "backend"
for _p in (str(REPO), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRATCH = Path(r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p1a\T")
SCRATCH.mkdir(parents=True, exist_ok=True)

DATA = Path(tempfile.mkdtemp(prefix="aura_p1a_till_", dir=str(SCRATCH)))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

# Seed AFTER signup, not before -- see module docstring.
from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

import app as _app_module  # noqa: E402
flask_app = _app_module.init_app()

import logging  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from werkzeug.serving import make_server  # noqa: E402

import socket  # noqa: E402


def port_is_free(port):
    """FIX ROUND 3 (M8): werkzeug's make_server sets SO_REUSEADDR, and on
    Windows that flag lets a SECOND listener bind a port another process
    already holds -- so the old try/except-around-make_server loop happily
    "succeeded" on an occupied port and the browser then talked to the
    stranger's server (verifier: holdport.py). A plain bind WITHOUT
    SO_REUSEADDR is refused for an occupied port, and a connect probe
    catches a listener that bound with its own reuse flag. Both must say
    "free" before make_server is even tried."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False  # somebody is listening
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))  # no SO_REUSEADDR on purpose
        except OSError:
            return False
    return True


server = None
PORT = None
for candidate in range(5810, 5820):  # crew T's own port range (contract.md)
    if not port_is_free(candidate):
        continue
    try:
        server = make_server("127.0.0.1", candidate, flask_app, threaded=True)
        PORT = candidate
        break
    except OSError:
        continue
if server is None:
    print("FATAL: no free port 5810-5819")
    sys.exit(2)

thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
time.sleep(1.2)
BASE = f"http://127.0.0.1:{PORT}"
print(f"Backend up at {BASE}  AURA_APP_DATA={DATA}")

# ---------------------------------------------------------------------------
# Harness plumbing
# ---------------------------------------------------------------------------
RESULTS = []


def _cleanup_after_failure():
    """A failing check() must not corrupt the page state for every check
    after it. This bit us for real (2026-09-28): item6's assertion used to
    fire mid-test, before its own 'click New Sale to close the receipt'
    cleanup line ever ran (a raised AssertionError skips the rest of the
    function) -- so the receipt's `.ret-modal-overlay` stayed open, and
    EVERY later test that did a real click or navigation failed too, with
    unrelated "modal overlay intercepts pointer events" errors that had
    nothing to do with what they were actually testing. Rather than trust
    every future test author to structure cleanup so it survives an
    assertion (the same "verification harness has the same shapes" trap
    ENGINEERING.md warns about), this runs after ANY failed check and
    forcibly clears the one class of state a UI test can leave behind:
    a stray modal. It does not touch RESULTS or re-raise -- a failure in
    this best-effort cleanup must never mask or replace the real one.
    """
    try:
        owner.evaluate(
            "() => document.querySelectorAll('.ret-modal-overlay').forEach(el => el.remove())"
        )
    except Exception:  # noqa: BLE001
        pass  # `owner` not yet created, or the page/context is gone -- nothing to clean.


_ONLY = [s for s in os.environ.get("P1A_ONLY", "").split("|") if s]
if _ONLY:
    print(f"FILTERED RUN (P1A_ONLY={_ONLY!r}) -- not a full verification")


def check(name, fn):
    # Dev-only iteration aid: P1A_ONLY="M6|M4" runs just the checks whose name
    # contains one of those substrings (fixtures at module level still run).
    # Unset by default, so the shipped behaviour is every check.
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
    page.fill("#su-company", "P1a Till Test Co")
    page.fill("#su-email", "owner@p1atill.local")
    page.fill("#su-pass", "P1aTillTest12345!")
    page.fill("#su-pass2", "P1aTillTest12345!")
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


_CENTRE_JS = """(el) => {
  const r = el.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const inView = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= vw && r.bottom <= vh;
  const hit = document.elementFromPoint(cx, cy);
  return {cx, cy, inView, vw, vh, box: [r.left, r.top, r.right, r.bottom],
          hits: !!hit && (hit === el || el.contains(hit)),
          hitDesc: hit ? (hit.tagName + '#' + hit.id + '.' + String(hit.className).slice(0, 40) + ' <' + hit.outerHTML.slice(0, 140).replace(/\\s+/g, ' ') + '>' + ' parent=' + (hit.parentElement ? hit.parentElement.tagName + '#' + hit.parentElement.id + '.' + String(hit.parentElement.className).slice(0, 40) : '')) : null};
}"""


def reachable(page, locator, what=""):
    """contract.md rule 6b: 'a person can click / see this' is three facts, not
    one. (1) the element's box lies inside the viewport, (2) the topmost thing
    at its centre IS the element (nothing overlaps it), (3) a real mouse click
    at that point is what we then send -- never `force=True`, never a scroll-
    and-hope. Returns the measured geometry. Asserts (1) and (2)."""
    locator.first.scroll_into_view_if_needed(timeout=3000)
    info = locator.first.evaluate(_CENTRE_JS)
    assert info["inView"], f"{what}: box {info['box']} is outside the {info['vw']}x{info['vh']} viewport"
    assert info["hits"], f"{what}: elementFromPoint at its centre hits {info['hitDesc']}, not the element"
    return info


def real_click(page, locator, what=""):
    info = reachable(page, locator, what)
    page.mouse.click(info["cx"], info["cy"])
    return info


def activate_employee(user_id, password):
    """Give an invited (pending_setup, password_hash='PENDING') account a
    real password so it can log in, without going through the emailed
    invite -- the exact technique retail_employee_created_via_screen_can_
    work_test.py's own _activate() uses, for the identical reason (these
    scenarios need a live session belonging to a non-owner)."""
    conn = registry_conn()
    try:
        conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?",
                     (hash_password(password), user_id))
        conn.commit()
    finally:
        conn.close()


def grant_capability(user_id, code):
    """A delegated branch manager needs retail.employees ON TOP OF the
    default manager seed, which deliberately EXCLUDES it (user_accounts.py:
    `ROLE_MANAGER: frozenset(CAPABILITY_CODES) - {CAP_EMPLOYEES,
    CAP_CASH_APPROVE}` -- CAP_EMPLOYEES is the one code a plain manager does
    NOT get by default, matching the delegation-toggle design). The real
    grant route is POST /api/admin/employees/<id>/permissions (owner-only,
    UI-driven); this writes the identical row shape directly, which is
    the right level of realism for a scenario that is testing THIS
    crew's front-end reaction to a given capability set, not the grant
    route itself (a different, already-covered concern -- see e.g.
    retail_employee_management_test.py).

    UPDATE, not INSERT: seed_capabilities_for_user (called by create_employee
    for every new account) already writes a row for EVERY capability code,
    including the ones the role does NOT default to -- at access_level=
    'none', not absent. Found live: a bare INSERT here hit
    `UNIQUE(user_id, subsystem)` immediately, because the manager's own
    'retail.employees' row already existed at 'none'. A test that assumed
    "not seeded" and got "seeded, but 'none'" is exactly the class of
    mismatch reading the real schema first (rather than the plan's own
    shorthand) is meant to catch."""
    conn = registry_conn()
    try:
        conn.execute(
            "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
            (user_accounts.ACCESS_FULL, user_id, code),
        )
        conn.commit()
    finally:
        conn.close()


from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    browser = p.chromium.launch()

    # =========================================================================
    # OWNER: sign up, licence, seed data
    # =========================================================================
    owner_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    owner = owner_ctx.new_page()
    owner.on("pageerror", lambda exc: print(f"      [owner pageerror] {exc}"))
    owner.goto(BASE)
    dismiss_intro_overlay(owner)
    onboard(owner)

    seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
    owner.reload()
    dismiss_intro_overlay(owner)
    owner.wait_for_selector("#sub-header-section", timeout=20000)
    owner.wait_for_timeout(800)

    api(owner, "POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})

    # FIX ROUND 3 (M5): a category's tone is now its CREATION ORDER
    # (`(created_at, id)`, index % 8 + 1), no longer a hash of its uuid -- so
    # the old Python port of that hash is gone. Categories are created 1.1s
    # apart so `created_at` (second resolution) can never tie, which makes the
    # order -- and therefore tones 1, 2, 3 -- exact instead of uuid-random.
    # The check further down asserts those literal numbers, not a mirror of
    # the product's own sort.
    cats = []
    for i, nm in enumerate(("Tone Fixture 0", "Tone Fixture 1", "Tone Fixture 2")):
        if i:
            time.sleep(1.1)
        _, body = api(owner, "POST", "/api/sub/retail/categories", {"name": nm})
        cid = ((body or {}).get("data") or {}).get("id")
        assert cid, f"category seeding failed for {nm}: {body}"
        cats.append(cid)
    CAT_A, CAT_B = cats[0], cats[1]

    def create_product(name, sku, price, category_id=None):
        _, body = api(owner, "POST", "/api/sub/retail/products", {
            "name": name, "sku": sku, "sell_price": price, "cost_price": round(price / 2, 3),
            "category_id": category_id, "unit": "pc",
        })
        pid = ((body or {}).get("data") or {}).get("id")
        assert pid, f"product '{name}' was not created: {body}"
        api(owner, "POST", f"/api/sub/retail/products/{pid}/stock-adjust",
            {"quantity": 100, "reason": "p1a till e2e seed"})
        return pid

    PID_WIDGET = create_product("Aura Test Widget", "P1A-W1", 10, CAT_A)
    PID_GADGET = create_product("Aura Test Gadget", "P1A-G1", 5, CAT_B)

    _, walkin_cust_body = api(owner, "POST", "/api/sub/retail/customers",
                               {"name": "Not A Credit Customer", "phone": "0791234567"})
    NOCREDIT_CUST_ID = ((walkin_cust_body or {}).get("data") or {}).get("id")
    api(owner, "PATCH", f"/api/sub/retail/customers/{NOCREDIT_CUST_ID}", {"credit_mode": "none"})

    api(owner, "POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 50})

    # ── Employees: a cashier and a delegated branch manager ────────────────
    CASHIER_EMAIL = "cashier@p1atill.local"
    CASHIER_PW = "CashierPW1a!"
    _, cashier_body = api(owner, "POST", "/api/admin/employees", {"email": CASHIER_EMAIL, "role": "cashier"})
    assert cashier_body.get("data") or cashier_body.get("user_id") or cashier_body, f"cashier create failed: {cashier_body}"
    conn = registry_conn()
    try:
        CASHIER_ID = conn.execute("SELECT id FROM users WHERE email=?", (CASHIER_EMAIL,)).fetchone()["id"]
    finally:
        conn.close()
    activate_employee(CASHIER_ID, CASHIER_PW)

    MANAGER_EMAIL = "manager@p1atill.local"
    MANAGER_PW = "ManagerPW1a!"
    _, manager_body = api(owner, "POST", "/api/admin/employees", {"email": MANAGER_EMAIL, "role": "manager"})
    assert manager_body, f"manager create failed: {manager_body}"
    conn = registry_conn()
    try:
        MANAGER_ID = conn.execute("SELECT id FROM users WHERE email=?", (MANAGER_EMAIL,)).fetchone()["id"]
    finally:
        conn.close()
    grant_capability(MANAGER_ID, user_accounts.CAP_EMPLOYEES)
    activate_employee(MANAGER_ID, MANAGER_PW)

    # ── FIX ROUND seed data: FIFO aging, XSS names, no-phone customer ──────
    import sqlite3
    RETAIL_DB = DATA / "database" / "subsystems" / "retail.db"

    def backdate_sale(sale_number, days_ago):
        conn = sqlite3.connect(str(RETAIL_DB))
        try:
            conn.execute("UPDATE sales SET created_at = datetime('now', ?) WHERE sale_number = ?",
                         (f"-{days_ago} days", sale_number))
            conn.commit()
        finally:
            conn.close()

    def credit_sale(customer_id, product_id, price):
        _, body = api(owner, "POST", "/api/sub/retail/sales", {
            "idempotency_key": f"fifo-seed-{uuid.uuid4().hex}",
            "customer_id": customer_id,
            "items": [{"product_id": product_id, "quantity": 1, "unit_price": price, "discount_pct": 0}],
            "subtotal": price, "discount_amount": 0, "tax_amount": 0, "total": price,
            "payment_method": "credit", "amount_paid": 0,
        })
        assert body.get("status") == "success", f"credit_sale failed: {body}"
        return body["data"]["sale_number"], body["data"]["id"]

    PID_CREDIT_SEED = create_product("FIFO Seed Product", "P1A-FIFO", 14.5)

    # "Old Payer": a 100-day-old charge, later fully paid off by an account
    # payment (never touching sales.amount_paid) -- the EXACT verifier
    # scenario (F3): a customer_payment settles the OLD debt, a fresh charge
    # exists too. FIFO must allocate the payment to the OLDEST charge first,
    # leaving only today's fresh charge open (0 days overdue).
    _, old_payer_body = api(owner, "POST", "/api/sub/retail/customers",
                             {"name": "Old Payer", "phone": "0794445566"})
    OLD_PAYER_ID = old_payer_body["data"]["id"]
    api(owner, "PATCH", f"/api/sub/retail/customers/{OLD_PAYER_ID}", {"credit_mode": "unlimited"})
    old_sale_number, _ = credit_sale(OLD_PAYER_ID, PID_CREDIT_SEED, 14.5)
    backdate_sale(old_sale_number, 100)
    credit_sale(OLD_PAYER_ID, PID_CREDIT_SEED, 7.25)  # today's fresh charge
    api(owner, "POST", f"/api/sub/retail/customers/{OLD_PAYER_ID}/payments",
        {"amount": 14.5, "method": "cash", "notes": "settles the 100-day-old charge"})

    # "Genuinely Overdue": a real 45-day-old charge, never paid -- the other
    # half of F3's test instruction ("also test a real 45-day overdue debt
    # and check the amount and days").
    _, overdue_body = api(owner, "POST", "/api/sub/retail/customers",
                           {"name": "Genuinely Overdue", "phone": "0795556677"})
    OVERDUE_ID = overdue_body["data"]["id"]
    api(owner, "PATCH", f"/api/sub/retail/customers/{OVERDUE_ID}", {"credit_mode": "unlimited"})
    overdue_sale_number, _ = credit_sale(OVERDUE_ID, PID_CREDIT_SEED, 14.5)
    backdate_sale(overdue_sale_number, 45)

    # No phone at all -- F16.
    _, nophone_body = api(owner, "POST", "/api/sub/retail/customers", {"name": "No Phone At All"})
    NOPHONE_ID = nophone_body["data"]["id"]
    api(owner, "PATCH", f"/api/sub/retail/customers/{NOPHONE_ID}", {"credit_mode": "unlimited"})
    nophone_sale_number, _ = credit_sale(NOPHONE_ID, PID_CREDIT_SEED, 9.0)
    backdate_sale(nophone_sale_number, 10)

    # FIX ROUND 3 (HIGH-1/2, M2): fixtures for the SERVER's FIFO answer. The
    # tests below read what the running server returned; nothing here is a
    # stub. Each price gets its own product because the server recomputes a
    # sale's total from the product's price.
    _price_products = {}

    def product_for_price(price):
        if price not in _price_products:
            _price_products[price] = create_product(
                f"FIFO Price {price}", f"P1A-FP-{str(price).replace('.', '_')}", price)
        return _price_products[price]

    def credit_charge(customer_id, price, days_ago):
        num, _sid = credit_sale(customer_id, product_for_price(price), price)
        backdate_sale(num, days_ago)

    def pay_account(customer_id, amount, days_ago):
        st, body = api(owner, "POST", f"/api/sub/retail/customers/{customer_id}/payments",
                       {"amount": amount, "method": "cash", "notes": "p1a fifo fixture"})
        assert st == 200, f"payment failed: {st} {body}"
        ref = ((body or {}).get("data") or {}).get("reference")
        assert ref, f"payment carried no reference: {body}"
        c = sqlite3.connect(str(RETAIL_DB))
        try:
            n = c.execute(
                "UPDATE payments SET created_at = datetime('now', ?) WHERE reference = ? AND party_id = ?",
                (f"-{days_ago} days", ref, customer_id)).rowcount
            c.commit()
        finally:
            c.close()
        assert n == 1, f"could not backdate payment {ref} (rows={n})"

    def make_debtor(name, phone):
        _, b = api(owner, "POST", "/api/sub/retail/customers", {"name": name, "phone": phone})
        cid = b["data"]["id"]
        api(owner, "PATCH", f"/api/sub/retail/customers/{cid}", {"credit_mode": "unlimited"})
        return cid

    # Verifier CASE D: 20 paid up front 30 days ago, then a 30 charge 10 days
    # ago. The customer owes 10 (the prepayment is carried forward and eaten
    # by the charge). The old client FIFO dropped the prepayment and said 30.
    PREPAID_ID = make_debtor("Prepaid Then Charged", "0796667788")
    pay_account(PREPAID_ID, 20.0, 30)
    credit_charge(PREPAID_ID, 30.0, 10)

    # Verifier CASE C: three 10 charges at 70/40/5 days, then 15 paid 20 days
    # ago. FIFO clears the 70-day charge and 5 of the 40-day one: open = 5 at
    # 40 days + 10 at 5 days (overdue 15, oldest open 5, 40 days).
    THREE_ID = make_debtor("Three Charges One Payment", "0796667799")
    for d in (70, 40, 5):
        credit_charge(THREE_ID, 10.0, d)
    pay_account(THREE_ID, 15.0, 20)

    # M2: >= 7 statement events so "the newest five" and "the oldest five"
    # are DIFFERENT sets. 8 events, every amount distinct and none of the
    # oldest-five amounts (21/22/23) equal to the balance (98) or to the
    # oldest-unpaid remainder (11 = 21 - 4 - 6), so their absence from the
    # message is a real signal. Oldest first: c 21 (80d), c 22 (70d),
    # c 23 (60d), pay 4 (50d), c 13 (40d), c 14 (30d), pay 6 (20d), c 15 (5d).
    MANY_ID = make_debtor("Many Events Customer", "0796667711")
    credit_charge(MANY_ID, 21.0, 80)
    credit_charge(MANY_ID, 22.0, 70)
    credit_charge(MANY_ID, 23.0, 60)
    pay_account(MANY_ID, 4.0, 50)
    credit_charge(MANY_ID, 13.0, 40)
    credit_charge(MANY_ID, 14.0, 30)
    pay_account(MANY_ID, 6.0, 20)
    credit_charge(MANY_ID, 15.0, 5)

    # Hostile names -- F2 (stored XSS). One classic quote-break, one raw
    # <img onerror>; both go through customer creation exactly like a real
    # shop's customer list would carry them (relayed from another device,
    # or simply typed with an apostrophe in a real name).
    XSS_NAME_1 = "Z');window.__pwned=1;//"
    XSS_NAME_2 = "<img src=x onerror=window.__pwned2=1>"
    _, xss1_body = api(owner, "POST", "/api/sub/retail/customers", {"name": XSS_NAME_1, "phone": "0793334455"})
    XSS1_ID = xss1_body["data"]["id"]
    api(owner, "PATCH", f"/api/sub/retail/customers/{XSS1_ID}", {"credit_mode": "unlimited"})
    xss1_sale, _ = credit_sale(XSS1_ID, PID_CREDIT_SEED, 7.25)
    backdate_sale(xss1_sale, 10)

    _, xss2_body = api(owner, "POST", "/api/sub/retail/customers", {"name": XSS_NAME_2, "phone": "0793334456"})
    XSS2_ID = xss2_body["data"]["id"]
    api(owner, "PATCH", f"/api/sub/retail/customers/{XSS2_ID}", {"credit_mode": "unlimited"})
    xss2_sale, _ = credit_sale(XSS2_ID, PID_CREDIT_SEED, 7.25)
    backdate_sale(xss2_sale, 10)

    go_pos(owner)
    print("SEEDING DONE.")

    # =========================================================================
    # Separate browser contexts (own cookie jars) for cashier / manager
    # =========================================================================
    def new_session(email, password):
        ctx = browser.new_context(viewport={"width": 1366, "height": 768})
        pg = ctx.new_page()
        pg.goto(BASE)
        pg.wait_for_selector("body", timeout=15000)
        status, _ = api(pg, "POST", "/api/auth/login", {"email": email, "password": password})
        assert status == 200, f"login as {email} failed with status {status}"
        pg.goto(BASE)
        dismiss_intro_overlay(pg)
        pg.wait_for_selector("#sub-header-section", timeout=20000)
        pg.wait_for_timeout(800)
        return ctx, pg

    cashier_ctx, cashier = new_session(CASHIER_EMAIL, CASHIER_PW)
    manager_ctx, manager = new_session(MANAGER_EMAIL, MANAGER_PW)

    # =========================================================================
    # ITEM #1 -- "Settings follows the person, not the device"
    # =========================================================================
    def item1_owner_sees_every_admin_center_card():
        go(owner, "admin-center")
        owner.wait_for_selector("#branding-card", timeout=8000)
        for sel in ("#branding-card", "#default-tax-card", "#business-day-card", "#device-branch-card", "#doc-series-card"):
            assert owner.query_selector(sel) is not None, f"owner is missing {sel} in Admin Center"

    check("item1: owner sees every Admin Center card (branding/tax/business-day/device-branch/doc-series)",
          item1_owner_sees_every_admin_center_card)

    def item1_cashier_gets_lock_panel():
        go(cashier, "admin-center")
        cashier.wait_for_timeout(400)
        assert cashier.query_selector("#branding-card") is None, "cashier must not see the Branding card"
        assert cashier.query_selector("#device-branch-card") is None, "cashier must not see even the device-local cards"
        # COUNT THE CHECK, NOT THE OUTCOME (ENGINEERING.md rule #7): a missing
        # card alone could mean "hidden by the gate" OR "the section failed
        # to render at all". Assert the lock panel's own heading is present.
        heading = cashier.locator(".ret-title", has_text="Settings")
        assert heading.count() > 0, "expected the #admin-center lock panel's own 'Settings' heading to render"

    check("item1: a cashier navigating directly to #admin-center (deep link) gets the lock panel, not a card",
          item1_cashier_gets_lock_panel)

    def item1_delegated_manager_sees_only_device_local_cards():
        go(manager, "admin-center")
        manager.wait_for_timeout(400)
        assert manager.query_selector("#device-branch-card") is not None, \
            "a delegated branch manager (CAP_EMPLOYEES, not owner) must see the device-local Device Branch card"
        assert manager.query_selector("#doc-series-card") is not None, \
            "a delegated branch manager must see the device-local Document Numbering card"
        for sel in ("#branding-card", "#default-tax-card", "#business-day-card", "#reorder-req-card",
                    "#einvoicing-card", "#wa-reports-card", "#shop-network-card"):
            assert manager.query_selector(sel) is None, \
                f"a delegated branch manager (not the owner) must not see {sel}, an owner-only card"

    check("item1: a delegated branch manager (CAP_EMPLOYEES, not owner role) sees ONLY the two device-local cards",
          item1_delegated_manager_sees_only_device_local_cards)

    # =========================================================================
    # FIX ROUND F5 -- "New Book"/Create Book is owner-only; Claim stays for a manager
    # =========================================================================
    def f5_manager_does_not_see_create_book_owner_does():
        go(owner, "admin-center")
        # P1b: the numbering card now lives in the hub's "Tills & numbering" section, and
        # a hub section that is not on screen is hidden. Click the nav item like a person,
        # then wait for the card to be VISIBLE (unchanged from before). It can no longer
        # catch "the card is on the first screen of Settings": it is one click away now
        # (retail_p1b_hub_e2e.py covers the hub's own layout).
        owner.wait_for_selector('#sh-root .sh-nav-item[data-sh="tills"]', timeout=8000)
        owner.click('#sh-root .sh-nav-item[data-sh="tills"]')
        owner.wait_for_selector("#doc-series-card", timeout=8000)
        assert owner.query_selector('button:has-text("Create Book")') is not None, \
            "the owner must see the Create Book button"

        go(manager, "admin-center")
        # P1b: same as above -- a delegated manager's hub opens on Tills & numbering
        # (their first section), but click it anyway so this does not lean on that order.
        manager.wait_for_selector('#sh-root .sh-nav-item[data-sh="tills"]', timeout=8000)
        manager.click('#sh-root .sh-nav-item[data-sh="tills"]')
        manager.wait_for_selector("#doc-series-card", timeout=8000)
        manager.wait_for_timeout(300)
        assert manager.query_selector('button:has-text("Create Book")') is None, \
            "a delegated branch manager must NOT see Create Book -- create_doc_series is owner-only (403 live)"
        # The server 403 itself, quoted, so this is not merely a UI opinion.
        status, body = api(manager, "POST", "/api/sub/retail/doc-series",
                            {"doc_type": "sale", "code": "MGRTEST", "label": "x", "digits": 6, "start_at": 1})
        assert status == 403, f"expected create_doc_series to 403 for a manager, got {status}: {body}"
        print(f"      server 403 quoted: {body.get('message')!r}")

    check("F5: a delegated branch manager does not see Create Book (owner-only, 403 live); the owner does -- BOTH directions",
          f5_manager_does_not_see_create_book_owner_does)

    # =========================================================================
    # ITEM #3a -- Discount box: gone for a cashier, present for the owner
    # =========================================================================
    def item3a_discount_box_both_directions():
        go_pos(owner)
        assert owner.query_selector("#pos-disc") is not None, "the owner must see the real Discount % box"
        assert owner.query_selector("#pos-disc-link") is None, "the owner must not see the manager-needed link"

        go_pos(cashier)
        assert cashier.query_selector("#pos-disc") is None, "a cashier without retail.discount must not see the % box"
        link = cashier.query_selector("#pos-disc-link")
        assert link is not None, "a cashier without retail.discount must see the quiet manager link instead"
        assert "manager" in (link.inner_text() or "").lower(), \
            f"the link's own text should explain a manager is needed, got: {link.inner_text()!r}"

    check("item3a: Discount % box is present for the owner and absent (replaced by the manager link) for a cashier -- BOTH directions",
          item3a_discount_box_both_directions)

    def item3a_discount_link_is_quiet_not_destructive():
        # "does nothing destructive (Phase 3 wires the manager PIN...)" --
        # clicking it must not start a sale, submit anything, or throw; a
        # toast is the only observable effect.
        go_pos(cashier)
        cashier.click("#pos-disc-link")
        cashier.wait_for_timeout(300)
        cart_len = cashier.evaluate("() => RetailSystem._cart.length")
        assert cart_len == 0, "the quiet Discount link must never start or touch a sale"

    check("item3a: clicking the quiet Discount link does nothing destructive (no PIN flow yet, no sale side effect)",
          item3a_discount_link_is_quiet_not_destructive)

    # =========================================================================
    # ITEM #3b -- Credit (F5) greyed with its reason before the cashier tries it
    # =========================================================================
    def item3b_credit_greyed_walkin():
        go_pos(cashier)
        btn = cashier.query_selector('.pos-pay-btn[data-method="credit"]')
        assert btn is not None, "expected the Credit tender tile to exist (disabled, not removed)"
        # FIX ROUND F7: aria-disabled, NOT the native `disabled` attribute --
        # the tile must stay real-click-reachable so a mouse click can show
        # the same toast F5 already did (a native `disabled` button fires no
        # click event at all in a real browser).
        aria_disabled = cashier.eval_on_selector(
            '.pos-pay-btn[data-method="credit"]', "el => el.getAttribute('aria-disabled')")
        assert aria_disabled == "true", "Credit must be aria-disabled for a walk-in (no customer selected)"
        title = btn.get_attribute("title") or ""
        assert title == "Walk-in customer", f"expected the walk-in reason in the title attribute, got {title!r}"
        # FIX ROUND F7: the reason is also VISIBLE text on the tile itself,
        # not only a tooltip a mouse has to hover to discover.
        # innerText (not textContent) so a display:none element reads as
        # empty -- this proves the reason is actually RENDERED, and that the
        # F-key badge stepped aside for it rather than both showing.
        visible_text = cashier.eval_on_selector(
            '.pos-pay-btn[data-method="credit"] .pos-pay-reason', "el => el.innerText.trim()")
        assert visible_text == "Walk-in customer", \
            f"expected the reason as VISIBLE text on the tile, got {visible_text!r}"
        badge_shown = cashier.eval_on_selector(
            '.pos-pay-btn[data-method="credit"] .pos-pay-kbd', "el => getComputedStyle(el).display !== 'none'")
        assert not badge_shown, "F-key badge must step aside while the reason is showing"

    check("item3b: Credit (F5) is aria-disabled with a VISIBLE reason 'Walk-in customer' before the cashier tries it",
          item3b_credit_greyed_walkin)

    def item3b_credit_greyed_noncredit_customer():
        go_pos(cashier)
        cashier.select_option("#pos-customer", NOCREDIT_CUST_ID)
        cashier.wait_for_timeout(400)
        aria_disabled = cashier.eval_on_selector(
            '.pos-pay-btn[data-method="credit"]', "el => el.getAttribute('aria-disabled')")
        assert aria_disabled == "true", "Credit must stay aria-disabled for a customer whose credit_mode is 'none'"
        reason = cashier.eval_on_selector('.pos-pay-btn[data-method="credit"]', "el => el.getAttribute('data-credit-reason')")
        assert reason == "Not a credit customer", f"expected the non-credit-customer reason, got {reason!r}"
        # Reset back to Walk-in so later scenarios in this same context start clean.
        cashier.select_option("#pos-customer", "")
        cashier.wait_for_timeout(300)

    check("item3b: Credit (F5) is aria-disabled with reason 'Not a credit customer' once a credit_mode='none' customer is picked",
          item3b_credit_greyed_noncredit_customer)

    def item3b_f7_click_on_disabled_tile_shows_same_toast_as_f5():
        # FIX ROUND F7: "A click on the disabled tile shows the same toast F5
        # shows." A REAL mouse click (not an evaluate() call into
        # _setPayment), because the whole point of switching from `disabled`
        # to `aria-disabled` was to make this reachable by an actual click.
        go_pos(cashier)
        cashier.wait_for_selector('.pos-pay-btn[data-method="credit"]')
        cashier.evaluate("() => { window.__toasts = []; const orig = SubsystemApp.showToast; SubsystemApp.showToast = (msg, kind) => { window.__toasts.push([msg, kind]); }; window.__restoreToast = () => { SubsystemApp.showToast = orig; }; }")
        # contract.md rule 6b: box in the viewport, elementFromPoint at the
        # centre is the tile, then a real mouse click at that point (this used
        # to be click(force=True), which skips the actionability checks that
        # would have caught a tile hidden under another element).
        real_click(cashier, cashier.locator('.pos-pay-btn[data-method="credit"]'), "disabled Credit tile")
        cashier.wait_for_timeout(200)
        toasts = cashier.evaluate("() => window.__toasts")
        cashier.evaluate("() => window.__restoreToast()")
        assert toasts and toasts[0][0] == "Walk-in customer" and toasts[0][1] == "error", \
            f"expected a real mouse click on the disabled Credit tile to toast the reason, got {toasts!r}"
        assert cashier.evaluate("() => RetailSystem._paymentMethod") != "credit", \
            "the click must still refuse to actually select Credit"

    check("item3b F7: a real mouse click on the disabled Credit tile shows the same toast the F5 key does",
          item3b_f7_click_on_disabled_tile_shows_same_toast_as_f5)

    def item3b_mutation_credit_is_never_left_selectable_by_mistake():
        # Count the CHECK, not the outcome: a disabled native <button> already
        # refuses a real mouse click on its own, so this proves the SEPARATE
        # keyboard path (_setPayment's own defensive guard, reached by the F5
        # accelerator, which calls _setPayment directly and does NOT go
        # through a real click event) also refuses -- calling _setPayment
        # programmatically the exact way _onPOSShortcut's F5 branch does.
        go_pos(cashier)
        cashier.wait_for_selector('.pos-pay-btn[data-method="credit"]')
        result = cashier.evaluate("""
            () => {
                const btn = document.querySelector('.pos-pay-btn[data-method="credit"]');
                RetailSystem._setPayment('credit', btn);
                return RetailSystem._paymentMethod;
            }
        """)
        assert result != "credit", f"F5's own code path must not be able to select Credit while disabled, got {result!r}"

    check("item3b: the F5 keyboard path (bypassing native disabled) still refuses to select Credit while ineligible",
          item3b_mutation_credit_is_never_left_selectable_by_mistake)

    # FIX ROUND 3 (M6): the disabled Credit tile's reason is now 11px (was 9px)
    # AND showing it must not make the tender row taller than the resting row
    # -- the checkout footer has no spare pixels at 1366x768. Measured per tile
    # (all six) and for the whole row, walk-in (reason showing) vs a credit
    # customer (reason hidden), in EN and AR, at 1366 and 1024 wide.
    #
    # The Arabic reasons are the strings crew S/the catalog owner will land in
    # ar.json (they are pending in i18n-T.json). Until they do, t() would fall
    # back to the ENGLISH key and the AR pass would silently measure English, so
    # the check merges them into the live AR dictionary first -- the same
    # values, so the width measured is the width that ships.
    PENDING_AR = {
        "Walk-in customer": "زبون بدون حساب",
        "Not a credit customer": "ليس زبون آجل",
    }
    TILE_JS = """() => {
      const tiles = Array.from(document.querySelectorAll('#pos-pay-btns .pos-pay-btn'));
      const row = document.getElementById('pos-pay-btns').getBoundingClientRect();
      const rs = document.querySelector('.pos-pay-btn[data-method="credit"] .pos-pay-reason');
      const rr = rs ? rs.getBoundingClientRect() : null;
      return {rowH: row.height, reasonH: rr ? rr.height : 0, reasonW: rr ? rr.width : 0,
        tiles: tiles.map(b => ({m: b.dataset.method, h: b.getBoundingClientRect().height,
        w: b.getBoundingClientRect().width}))};
    }"""

    def m6_tile_heights(vw, lang):
        cashier.set_viewport_size({"width": vw, "height": 768})
        cashier.evaluate("(d) => { Object.assign(AuraI18n.dicts.ar, d); }", PENDING_AR)
        cashier.evaluate("(l) => { AuraI18n.current = l; localStorage.setItem('aura_lang', l); AuraI18n.apply(); }", lang)
        go_pos(cashier)
        # Belt and braces: the screen must really be in the language under test.
        assert cashier.evaluate("() => document.documentElement.getAttribute('lang')") == lang
        cashier.select_option("#pos-customer", "")
        cashier.wait_for_timeout(300)
        walk = cashier.evaluate(TILE_JS)
        reason_el = cashier.locator('.pos-pay-btn[data-method="credit"] .pos-pay-reason')
        shown = cashier.evaluate("() => document.querySelector('.pos-pay-btn[data-method=\"credit\"] .pos-pay-reason').innerText.trim()")
        font_px = cashier.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.pos-pay-btn[data-method=\"credit\"] .pos-pay-reason')).fontSize)")
        expect_text = PENDING_AR["Walk-in customer"] if lang == "ar" else "Walk-in customer"
        assert shown == expect_text, f"[{vw}/{lang}] the reason must render in {lang}: got {shown!r}"
        assert font_px >= 11, f"[{vw}/{lang}] reason text is {font_px}px, must be >= 11px"
        # rule 6b on the reason text: it is in the viewport and nothing covers it.
        reachable(cashier, reason_el, f"[{vw}/{lang}] Credit reason text")
        cashier.select_option("#pos-customer", OVERDUE_ID)  # credit_mode=unlimited -> reason hidden
        cashier.wait_for_timeout(300)
        cred = cashier.evaluate(TILE_JS)
        assert cashier.evaluate("() => document.querySelector('.pos-pay-btn[data-method=\"credit\"]').getAttribute('aria-disabled')") is None, \
            "fixture: the credit customer must un-disable the tile, else this compares two identical states"
        # The OTHER reason ("Not a credit customer", the longer string) must
        # fit the same footprint -- it wraps to more lines than "Walk-in customer".
        cashier.select_option("#pos-customer", NOCREDIT_CUST_ID)
        cashier.wait_for_timeout(300)
        assert cashier.evaluate("() => document.querySelector('.pos-pay-btn[data-method=\"credit\"]').getAttribute('aria-disabled')") == "true", \
            "fixture: the credit_mode=none customer must leave Credit disabled"
        nocred = cashier.evaluate(TILE_JS)
        cashier.select_option("#pos-customer", "")
        cashier.wait_for_timeout(300)
        return walk, cred, nocred

    def item3b_m6_tender_tiles_identical_height():
        problems = []
        for vw in (1366, 1024):
            for lang in ("en", "ar"):
                walk, cred, nocred = m6_tile_heights(vw, lang)
                if abs(nocred["rowH"] - cred["rowH"]) > 0.01:
                    problems.append(f"[{vw}/{lang}] 'Not a credit customer' ROW height {nocred['rowH']} vs {cred['rowH']} (credit); "
                                    f"reason box {nocred['reasonW']}x{nocred['reasonH']}")
                if abs(walk["rowH"] - cred["rowH"]) > 0.01:
                    problems.append(f"[{vw}/{lang}] tender ROW height {walk['rowH']} (walk-in) vs {cred['rowH']} (credit); "
                                    f"reason box {walk['reasonW']}x{walk['reasonH']}, credit tile w={walk['tiles'][0]['w'] if walk['tiles'] else '?'}")
                for a, b in zip(walk["tiles"], cred["tiles"]):
                    if abs(a["h"] - b["h"]) > 0.01:
                        problems.append(f"[{vw}/{lang}] {a['m']} tile {a['h']} (walk-in) vs {b['h']} (credit)")
        cashier.set_viewport_size({"width": 1366, "height": 768})
        cashier.evaluate("() => { AuraI18n.current = 'en'; localStorage.setItem('aura_lang', 'en'); AuraI18n.apply(); }")
        go_pos(cashier)
        assert not problems, "showing the Credit reason changed a tile/row height: " + "; ".join(problems)

    check("item3b M6: Credit reason is >= 11px, visible and unobstructed, and every tender tile keeps the IDENTICAL height "
          "walk-in vs credit customer (1366 + 1024 wide, EN + AR)", item3b_m6_tender_tiles_identical_height)

    # =========================================================================
    # ITEM #3c -- Products: read-only "Stock lookup" for a cashier, full CRUD for the owner
    # =========================================================================
    def item3c_products_both_directions():
        go(owner, "products")
        owner.wait_for_selector("#prod-table", timeout=8000)
        assert owner.query_selector('button:has-text("Add Product")') is not None, \
            "the owner must see Add Product"
        assert owner.query_selector('th:has-text("Actions")') is not None, \
            "the owner must see an Actions column"

        go(cashier, "products")
        cashier.wait_for_selector("#prod-table", timeout=8000)
        cashier.wait_for_timeout(300)
        assert cashier.query_selector('button:has-text("Add Product")') is None, \
            "a cashier without retail.stock.adjust must not see Add Product"
        assert cashier.query_selector('button:has-text("Import")') is None, \
            "a cashier without retail.stock.adjust must not see Import"
        assert cashier.query_selector('th:has-text("Actions")') is None, \
            "a cashier without retail.stock.adjust must see no Actions column at all"
        # COUNT THE CHECK: the table must still have rendered real rows
        # (proving the SCREEN worked), not merely be missing buttons because
        # nothing rendered.
        row_count = cashier.eval_on_selector_all("#prod-table tbody tr", "els => els.length")
        assert row_count >= 1, "the read-only Stock lookup table must still show real product rows"
        # FIX ROUND F9: the e2e never asserted the PER-ROW gate specifically
        # (Edit/Stock/Delete inside each <tr>, not just the header's Actions
        # column) -- a bug there could hide the column header while still
        # leaving row buttons reachable via direct DOM access. Count real
        # buttons inside data rows by their own onclick target, not by
        # visible text (Delete's own label may itself be gated by locale).
        cashier_row_btns = cashier.eval_on_selector_all(
            "#prod-table tbody tr",
            "rows => rows.reduce((n, r) => n + r.querySelectorAll('button').length, 0)")
        assert cashier_row_btns == 0, \
            f"a cashier without retail.stock.adjust must see ZERO buttons inside any product row, found {cashier_row_btns}"
        owner_row_btns = owner.eval_on_selector_all(
            "#prod-table tbody tr",
            "rows => rows.length ? rows[0].querySelectorAll('button').length : 0")
        assert owner_row_btns >= 3, \
            f"the owner must see Edit/Stock/Delete (>=3 buttons) on a real product row, found {owner_row_btns}"

    check("item3c: Products is read-only 'Stock lookup' (no Add/Import/Actions/per-row buttons) for a cashier, full CRUD for the owner -- BOTH directions",
          item3c_products_both_directions)

    def item3c_mutation_m3_per_row_gate_goes_red_when_broken():
        # FIX ROUND F9's own instruction: "Mutation M3 must go RED." Proves
        # the NEW per-row assertion above actually distinguishes gated from
        # ungated -- not merely "some buttons exist somewhere on the page".
        # Mutates the LIVE page's own _renderProductTable to always report
        # canAdjust=true (the exact shape of bug this guard exists to catch)
        # and reruns the render; the per-row-button count must then go
        # non-zero for the cashier, proving this check is not vacuous.
        go(cashier, "products")
        cashier.wait_for_selector("#prod-table", timeout=8000)
        cashier.evaluate("""
            () => {
                window.__origRenderProductTable = RetailSystem._renderProductTable;
                RetailSystem._renderProductTable = function (prods) {
                    const orig = window.SubsystemApp.hasCapability;
                    window.SubsystemApp.hasCapability = () => true;
                    try { window.__origRenderProductTable.call(this, prods); }
                    finally { window.SubsystemApp.hasCapability = orig; }
                };
                RetailSystem._renderProductTable(RetailSystem._products);
            }
        """)
        cashier.wait_for_timeout(300)
        mutated_row_btns = cashier.eval_on_selector_all(
            "#prod-table tbody tr",
            "rows => rows.length ? rows[0].querySelectorAll('button').length : 0")
        assert mutated_row_btns >= 3, (
            f"MUTATION PROOF FAILED TO GO RED: expected the per-row-button guard to catch a capability "
            f"gate forced to always-true (found {mutated_row_btns} buttons) -- the check above would not "
            f"have caught this bug")
        print(f"      MUTATION PROOF (F9/M3): canAdjust forced true -> {mutated_row_btns} row buttons for a cashier -- RED confirmed")
        # Restore.
        cashier.evaluate("() => { RetailSystem._renderProductTable = window.__origRenderProductTable; RetailSystem._renderProductTable(RetailSystem._products); }")
        cashier.wait_for_timeout(300)
        restored_row_btns = cashier.eval_on_selector_all(
            "#prod-table tbody tr",
            "rows => rows.length ? rows[0].querySelectorAll('button').length : 0")
        assert restored_row_btns == 0, f"restore failed: still {restored_row_btns} row buttons after undoing the mutation"
        print(f"      MUTATION PROOF (F9/M3): restored -> {restored_row_btns} row buttons for a cashier -- GREEN confirmed")

    check("item3c F9/M3: mutating the per-row capability gate to always-true makes the guard go RED, and restoring it goes GREEN",
          item3c_mutation_m3_per_row_gate_goes_red_when_broken)

    # =========================================================================
    # ITEM #6 -- "Send on WhatsApp" (receipt)
    # =========================================================================
    def item6_receipt_share_link_decoded():
        go_pos(owner)
        # Stub window.open BEFORE the sale, so nothing racy about wiring it
        # up after Send is already visible.
        owner.evaluate("() => { window.__lastOpenedUrl = null; window.open = (u) => { window.__lastOpenedUrl = u; }; }")
        owner.evaluate("(id) => RetailSystem._addToCart(id)", PID_WIDGET)
        owner.wait_for_timeout(300)
        owner.evaluate("(id) => RetailSystem._addToCart(id)", PID_GADGET)
        owner.wait_for_timeout(300)
        owner.click("#pos-checkout-btn")
        owner.wait_for_selector("#ret-receipt-dialog", timeout=10000)
        sale_number = owner.eval_on_selector("#ret-receipt-dialog", "el => RetailSystem._lastSaleData.sale_number")
        assert sale_number, "expected a real sale_number on the completed sale"

        owner.fill("#rcpt-wa-phone", "0791234567")
        # rule 6b: in the viewport, nothing on top of it, real mouse click.
        real_click(owner, owner.locator("#rcpt-wa-send-btn"), "receipt Send button")
        owner.wait_for_timeout(300)

        url = owner.evaluate("() => window.__lastOpenedUrl")
        assert url, "expected AuraShare.whatsapp to have called window.open with a wa.me link"
        assert url.startswith("https://wa.me/962791234567?text="), f"unexpected wa.me link shape: {url}"
        encoded = url.split("?text=", 1)[1]
        decoded = owner.evaluate("(e) => decodeURIComponent(e)", encoded)

        # FIX ROUND F11: the message carries the SHORT sale number
        # (RetailSystem._docNo(sale_number).main), not the long internal
        # form -- this assertion used to check for `str(sale_number)` (the
        # long form) and would now correctly FAIL against the fixed code,
        # since the long form is no longer in the message. Updated to match
        # the new, intended behaviour; retail_p1a_till_e2e.py's own
        # `f11_receipt_uses_short_sale_number_only` proves the long form is
        # actively ABSENT, which this test does not re-check.
        short_number = owner.evaluate("(n) => RetailSystem._docNo(n).main", sale_number)
        for needle in ("Aura Test Widget", "Aura Test Gadget", short_number):
            assert needle in decoded, f"expected {needle!r} in the DECODED WhatsApp text, got:\n{decoded}"
        assert "15.000" in decoded, f"expected the total (10 + 5 = 15.000 JD) in the decoded text, got:\n{decoded}"

        owner.click('#ret-receipt-dialog button:has-text("New Sale")')
        owner.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

    check("item6: receipt 'Send on WhatsApp' opens a wa.me link whose DECODED text carries every product name, the total and the sale number",
          item6_receipt_share_link_decoded)

    def item6_mutation_a_failed_share_log_never_blocks_the_share():
        # Mutation-style proof of AuraShare.logShare's own contract ("MUST
        # NEVER block or throw"): force fetch to reject and confirm
        # window.open still fires.
        #
        # SAVES the real fetch and restores it BY REFERENCE, never
        # `delete window.fetch` -- `fetch` is an own data property of
        # `window` in Chromium (no prototype to fall back to), so deleting it
        # after overwriting leaves `window.fetch` PERMANENTLY undefined for
        # the rest of this page's life, not restored. Found live: the very
        # next scenario's own _loadPOSData() call went through this same
        # `fetch`, got `undefined is not a function`, and the product grid
        # silently stayed on "Loading..." forever -- a verification-harness
        # bug with exactly the shape ENGINEERING.md warns about ("the
        # verification harness has the same shapes"), not a product bug.
        owner.evaluate("""
            () => {
                window.__realFetch = window.fetch;
                window.__lastOpenedUrl = null;
                window.open = (u) => { window.__lastOpenedUrl = u; };
                window.fetch = () => { throw new Error('simulated network failure'); };
            }
        """)
        ok = owner.evaluate("() => AuraShare.whatsapp('0791234567', 'test') && (AuraShare.logShare('receipt', 'X'), true)")
        assert ok is True, "AuraShare.whatsapp/logShare must not throw even when fetch is broken"
        url = owner.evaluate("() => window.__lastOpenedUrl")
        assert url, "the wa.me link must still have opened despite the audit POST failing"
        owner.evaluate("() => { window.fetch = window.__realFetch; delete window.__realFetch; }")

    check("item6 mutation: a broken share-log POST never blocks or throws during a WhatsApp send",
          item6_mutation_a_failed_share_log_never_blocks_the_share)

    # =========================================================================
    # ITEM #8 -- product tile monogram + category stripe
    # =========================================================================
    def item_tile_monogram_colours_differ_between_categories():
        # FIX ROUND 3 (M5): tones are creation-order now, so CAT_A (created
        # first) is tone 1 and CAT_B (created 1.1s later) is tone 2 -- literal
        # numbers, not "the two differ" (which is also true of a random hash
        # that happened not to collide, and was the old flaky 1-in-8 shape).
        go_pos(owner)
        tones = owner.evaluate(
            "(ids) => ids.map(id => RetailSystem._categoryToneIndex(id))", [CAT_A, CAT_B])
        assert tones == [1, 2], f"first two categories created must be tones 1 and 2, got {tones}"

        owner.wait_for_selector(".pos-card", timeout=8000)
        colours = owner.evaluate("""
            () => {
                const cards = Array.from(document.querySelectorAll('.pos-card'));
                const out = {};
                cards.forEach(c => {
                    const name = c.querySelector('.pos-card-name');
                    const mono = c.querySelector('.pos-card-monogram');
                    if (!name || !mono) return;
                    out[name.textContent.trim()] = getComputedStyle(mono).backgroundColor;
                });
                return out;
            }
        """)
        widget_colour = colours.get("Aura Test Widget")
        gadget_colour = colours.get("Aura Test Gadget")
        assert widget_colour and gadget_colour, f"expected monogram colours for both tiles, got: {colours}"
        assert widget_colour != gadget_colour, (
            f"two products in DIFFERENT-tone categories rendered the SAME monogram background "
            f"({widget_colour}) -- either the --cat-N-* tokens are not defined yet (crew S) or "
            f"the tone lookup is broken")

    check("item8: product tile monogram background colour differs between two different-tone categories (real computed style)",
          item_tile_monogram_colours_differ_between_categories)

    def item_tile_monogram_mutation_neutral_for_no_category():
        # Mutation-style proof that the NEUTRAL fallback is real, not an
        # accident of always hitting the same bucket: a product with NO
        # category must resolve tone index null, not 1-8.
        result = owner.evaluate("() => RetailSystem._categoryToneIndex(null)")
        assert result is None, f"expected null tone index for no category, got {result!r}"
        result2 = owner.evaluate("() => RetailSystem._categoryToneIndex(undefined)")
        assert result2 is None, f"expected null tone index for undefined category, got {result2!r}"

    check("item8 mutation: an uncategorised product resolves to the neutral tone (null), never a numbered bucket",
          item_tile_monogram_mutation_neutral_for_no_category)

    # =========================================================================
    # ITEM #9 -- chart-theme.js wired to Chart.defaults, re-themed on switch
    # =========================================================================
    def item9_chart_theme_applies_and_retheme_on_switch():
        go(owner, "dashboard")
        owner.wait_for_timeout(800)
        has_theme = owner.evaluate("() => typeof window.AuraChartTheme === 'object'")
        assert has_theme, "expected window.AuraChartTheme to be loaded"

        before = owner.evaluate("""
            () => ({
                tick: getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim(),
                chartColor: window.Chart && window.Chart.defaults && window.Chart.defaults.color,
            })
        """)
        assert before["chartColor"], "expected Chart.defaults.color to have been set by AuraChartTheme.apply()"

        # Real theme switch, not a manual attribute poke -- ThemeEngine.apply()
        # (app-shell.js's actual object name -- a top-level `const`, not
        # attached to `window`, but still a resolvable bare global in a
        # classic script's shared execution context, which is what
        # page.evaluate runs in) is what actually re-navigates the section
        # and re-runs the chart setup (see chart-theme.js's own comment on
        # why apply() needs no separate event listener of its own).
        owner.evaluate("() => ThemeEngine.apply('dark')")
        owner.wait_for_timeout(900)

        after = owner.evaluate("""
            () => ({
                tick: getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim(),
                chartColor: window.Chart && window.Chart.defaults && window.Chart.defaults.color,
            })
        """)
        assert after["tick"] != before["tick"], \
            "fixture problem: --text-secondary did not actually change between light and dark -- cannot prove re-theming"
        assert after["chartColor"] == after["tick"], (
            f"Chart.defaults.color ({after['chartColor']!r}) does not match the live --text-secondary "
            f"token ({after['tick']!r}) after a real theme switch -- chart-theme.js did not re-apply")

        owner.evaluate("() => ThemeEngine.apply('light')")
        owner.wait_for_timeout(500)

    check("item9: AuraChartTheme.apply() sets Chart.defaults from live tokens and re-applies after a real theme switch",
          item9_chart_theme_applies_and_retheme_on_switch)

    # =========================================================================
    # FIX ROUND 3 -- shared helpers for the Receivables / message checks.
    # Every "a person can click / see this" fact below goes through
    # reachable()/real_click() (contract.md rule 6b): bbox inside the viewport,
    # elementFromPoint(centre) is the element, then a REAL page.mouse.click.
    # =========================================================================
    import json
    import re

    def open_receivables(pg=None):
        pg = pg or owner
        go(pg, "receivables", wait_ms=200)
        pg.wait_for_selector("#pl-table-customer tbody tr[data-open-statement-id]", timeout=10000)
        pg.wait_for_timeout(700)

    REMIND_STATE_JS = """(id) => {
      const row = document.querySelector('#pl-table-customer tr[data-open-statement-id="' + id + '"]');
      if (!row) return {row: false};
      const tones = [...row.querySelectorAll('button[data-remind-id]')].map(b => b.dataset.remindTone);
      const off = row.querySelector('button[disabled][aria-describedby]');
      const why = row.querySelector('.ret-btn-reason[data-remind-reason]');
      let whyInfo = null;
      if (why) {
        const cs = getComputedStyle(why), r = why.getBoundingClientRect();
        whyInfo = {text: why.innerText.trim(), font: parseFloat(cs.fontSize),
                   visible: cs.visibility !== 'hidden' && cs.display !== 'none' && r.width > 0 && r.height > 0,
                   id: why.id, describedBy: off ? off.getAttribute('aria-describedby') : null};
      }
      return {row: true, tones, disabled: !!off, why: whyInfo};
    }"""

    def remind_state(cust_id, pg=None):
        return (pg or owner).evaluate(REMIND_STATE_JS, cust_id)

    def stub_wa(pg=None):
        (pg or owner).evaluate(
            "() => { window.__lastOpenedUrl = null; window.open = (u) => { window.__lastOpenedUrl = u; return null; }; }")

    def wa_text(pg=None):
        pg = pg or owner
        pg.wait_for_function("() => !!window.__lastOpenedUrl", timeout=5000)
        return pg.evaluate("() => decodeURIComponent(window.__lastOpenedUrl.split('?text=')[1])")

    def set_msg_lang(lang):
        st, _ = api(owner, "POST", "/api/sub/retail/settings/branding", {"branding_receipt_language": lang})
        assert st == 200, f"could not set branding_receipt_language={lang}: HTTP {st}"
        got = owner.evaluate("async () => (await RetailSystem._receiptBranding()).branding_receipt_language")
        assert got == lang, f"branding_receipt_language read back as {got!r}, wanted {lang!r}"

    def has_amt(text, amt):
        """`amt` appears as its own number: '4.000' must not match inside '14.000'."""
        return re.search(r"(?<![\d.])" + re.escape(f"{amt:.3f}") + r"(?!\d)", text) is not None

    def _fulfil(body, status=200):
        return lambda route: route.fulfill(status=status, content_type="application/json", body=body)

    # The "joined" stub answers /api/devices/me the way a computer that JOINED
    # another computer's hub does (not admin, cannot claim). The site-relay
    # stub in the same set says "no hub here" on purpose: if the joined branch
    # in _shopNetworkState stopped firing, the answer would fall through to
    # THIS response and Remind would come back ENABLED -- so the joined check
    # can only pass because the joined branch really fired.
    JOINED_STUBS = [
        ("**/api/devices/me", _fulfil('{"success":true,"device":{"is_admin_device":false},"can_claim_admin":false}')),
        ("**/api/site-relay/status", _fulfil('{"success":true,"enabled":false,"devices":[]}')),
    ]
    HUB_PEER_STUBS = [
        ("**/api/site-relay/status",
         _fulfil('{"success":true,"enabled":true,"devices":[{"installation_id":"dev-1","revoked":false}]}')),
    ]
    ERROR_ABORT_STUBS = [("**/api/site-relay/status", lambda route: route.abort("failed"))]
    ERROR_500_STUBS = [("**/api/site-relay/status", _fulfil('{"success":false}', status=500))]

    def with_stubs(stubs, fn, pg=None):
        pg = pg or owner
        for pat, handler in stubs:
            pg.route(pat, handler)
        try:
            return fn()
        finally:
            for pat, _h in stubs:
                pg.unroute(pat)
            pg.evaluate("() => { RetailSystem._shopNetworkStateCache = null; }")

    def close_statement(pg=None):
        (pg or owner).evaluate(
            "() => RetailSystem._closeModalOverlay(document.getElementById('ret-statement-modal'))")
        (pg or owner).wait_for_timeout(200)

    def open_statement_by_click(cust_id, pg=None):
        pg = pg or owner
        cell = pg.locator(f'#pl-table-customer tr[data-open-statement-id="{cust_id}"] td').first
        real_click(pg, cell, f"receivables row {cust_id}")
        pg.wait_for_selector("#ret-statement-dialog", timeout=8000)
        pg.wait_for_timeout(500)

    def assert_reason_visible(why, fragment, what):
        """M7: the reason is visible TEXT (not a tooltip), big enough to read."""
        assert why is not None, f"{what}: no visible .ret-btn-reason element"
        assert fragment in why["text"], f"{what}: reason text was {why['text']!r}, wanted it to contain {fragment!r}"
        assert why["visible"], f"{what}: the reason element is not rendered visibly"
        assert why["font"] >= 11, f"{what}: reason text is {why['font']}px, below the 11px floor"
        assert why["describedBy"] == why["id"], f"{what}: the disabled button is not aria-describedby its reason"

    NET_REASON = "shared between your computers"

    def assert_network_disabled(fragment, label):
        rows = (OVERDUE_ID, OLD_PAYER_ID, PREPAID_ID, THREE_ID, MANY_ID, NOPHONE_ID)
        for cid in rows:
            s = remind_state(cid)
            assert s["row"], f"{label}: receivables row {cid} is missing"
            assert s["tones"] == [], f"{label}: an ENABLED Remind/Firm button exists for {cid} while the shop network is active"
            assert s["disabled"], f"{label}: row {cid} shows no disabled Remind"
            assert_reason_visible(s["why"], fragment, f"{label} row {cid}")
        assert owner.query_selector("#pl-table-customer button[data-remind-id]") is None, \
            f"{label}: some enabled Remind button is still present"
        why_loc = owner.locator(
            f'#pl-table-customer tr[data-open-statement-id="{OVERDUE_ID}"] .ret-btn-reason[data-remind-reason]')
        reachable(owner, why_loc, f"{label}: reason text under Remind")

    # =========================================================================
    # F1 -- the real shop-network signal, all four cases + HIGH-3 + M1 + M7
    # =========================================================================
    def f1_single_computer_enabled():
        open_receivables()
        s = remind_state(OVERDUE_ID)
        assert s["row"] and s["tones"] == ["polite", "firm"] and not s["disabled"], \
            f"a single, unnetworked computer must show Remind and Firm on an overdue row: {s}"
        reachable(owner, owner.locator(
            f'button[data-remind-id="{OVERDUE_ID}"][data-remind-tone="polite"]'), "Remind on a single computer")
        reachable(owner, owner.locator(
            f'button[data-remind-id="{OVERDUE_ID}"][data-remind-tone="firm"]'), "Firm on a single computer")

    check("F1 case 1/4: single computer (real /api/devices/me, no hub peers) -> Remind and Firm ENABLED and reachable",
          f1_single_computer_enabled)

    def f1_joined_client_disabled():
        def run():
            open_receivables()
            assert_network_disabled(NET_REASON, "joined")
            # M7 on the Statement side too: the Send button is off, its reason is text.
            open_statement_by_click(OVERDUE_ID)
            assert owner.query_selector("#stmt-send-btn") is None, "joined computer still offers an enabled Send Statement"
            off = owner.query_selector("#stmt-send-btn-off")
            assert off is not None and off.is_disabled(), "joined computer must show Send Statement DISABLED"
            why = owner.evaluate("""() => { const e = document.getElementById('stmt-send-why');
                if (!e) return null; const cs = getComputedStyle(e), r = e.getBoundingClientRect();
                return {text: e.innerText.trim(), font: parseFloat(cs.fontSize),
                        visible: cs.visibility !== 'hidden' && cs.display !== 'none' && r.width > 0 && r.height > 0,
                        id: e.id, describedBy: document.getElementById('stmt-send-btn-off').getAttribute('aria-describedby')}; }""")
            assert_reason_visible(why, NET_REASON, "joined Statement")
            reachable(owner, owner.locator("#stmt-send-why"), "joined: reason text under Send Statement")
            close_statement()
        with_stubs(JOINED_STUBS, run)

    check("F1 case 2/4: this computer JOINED another computer (stubbed /api/devices/me) -> every Remind + Statement DISABLED, reason shown as text",
          f1_joined_client_disabled)

    def f1_hub_with_peer_disabled():
        def run():
            open_receivables()
            assert_network_disabled(NET_REASON, "hub-with-peer")
        with_stubs(HUB_PEER_STUBS, run)

    check("F1 case 3/4: this computer IS a hub with a paired device (stubbed site-relay/status) -> DISABLED, reason shown as text",
          f1_hub_with_peer_disabled)

    def f1_error_disabled_never_enabled():
        for label, stubs in (("aborted request", ERROR_ABORT_STUBS), ("HTTP 500", ERROR_500_STUBS)):
            def run():
                open_receivables()
                assert_network_disabled("check whether your computers share balances", f"error/{label}")
            with_stubs(stubs, run)

    check("F1 case 4/4: the site-relay check itself fails (aborted request AND HTTP 500) -> DISABLED with its own reason, never enabled",
          f1_error_disabled_never_enabled)

    def f1_unclaimed_single_computer_stays_enabled():
        # HIGH-3: a lone computer whose admin flag was never claimed is NOT
        # "joined" -- it can still claim the admin slot. The old code read
        # `is_admin_device === false` as "joined" and turned Remind off on a
        # shop with ONE computer. The real state is produced in the real
        # database (not stubbed): clear the flag, drop the device cache, and
        # prove /api/devices/me now reports the unclaimed shape.
        from commercial_runtime.identity import device_context
        conn = registry_conn()
        try:
            row = conn.execute("SELECT id FROM devices WHERE is_admin_device = 1").fetchone()
            assert row is not None, "fixture assumption broken: no admin device row to un-claim"
            dev_id = row["id"]
            n = conn.execute("UPDATE devices SET is_admin_device = 0 WHERE id = ?", (dev_id,)).rowcount
            conn.commit()
        finally:
            conn.close()
        assert n == 1, f"un-claim matched {n} rows"
        device_context.invalidate_cache()
        try:
            _, me = api(owner, "GET", "/api/devices/me")
            assert me["device"]["is_admin_device"] is False and me["can_claim_admin"] is True, \
                f"fixture did not produce the unclaimed shape: {me}"
            open_receivables()
            s = remind_state(OVERDUE_ID)
            assert s["tones"] == ["polite", "firm"] and not s["disabled"], \
                f"an UNCLAIMED single computer must keep Remind enabled (it is not joined): {s}"
            print(f"      HIGH-3: unclaimed single computer (is_admin_device False, can_claim_admin True) -> Remind tones {s['tones']}")
        finally:
            conn = registry_conn()
            try:
                conn.execute("UPDATE devices SET is_admin_device = 1 WHERE id = ?", (dev_id,))
                conn.commit()
            finally:
                conn.close()
            device_context.invalidate_cache()
        _, me2 = api(owner, "GET", "/api/devices/me")
        assert me2["device"]["is_admin_device"] is True, f"could not restore the admin device flag: {me2}"

    check("F1 HIGH-3: an UNCLAIMED single computer (real DB state, not joined) keeps Remind ENABLED",
          f1_unclaimed_single_computer_stays_enabled)

    def m1_pairing_mid_session_disables_without_reload():
        open_receivables()
        s0 = remind_state(OVERDUE_ID)
        assert s0["tones"] == ["polite", "firm"], f"precondition: Remind must start enabled: {s0}"
        owner.evaluate("() => { window.__m1_same_page = 'yes'; }")

        def joined_then_focus():
            owner.evaluate("() => window.dispatchEvent(new Event('focus'))")
            for _ in range(20):
                owner.wait_for_timeout(250)
                if remind_state(OVERDUE_ID)["disabled"]:
                    break
            s1 = remind_state(OVERDUE_ID)
            assert s1["disabled"] and s1["tones"] == [], \
                f"pairing mid-session did not disable Remind on the next focus (no reload): {s1}"
            assert owner.evaluate("() => window.__m1_same_page") == "yes", "the page reloaded; this proves nothing"
            assert_reason_visible(s1["why"], NET_REASON, "after mid-session pairing")
        with_stubs(JOINED_STUBS, joined_then_focus)

        # Allow direction: pairing undone -> the next focus enables it again.
        owner.evaluate("() => window.dispatchEvent(new Event('focus'))")
        for _ in range(20):
            owner.wait_for_timeout(250)
            if remind_state(OVERDUE_ID)["tones"] == ["polite", "firm"]:
                break
        s2 = remind_state(OVERDUE_ID)
        assert s2["tones"] == ["polite", "firm"] and not s2["disabled"], \
            f"after the pairing went away Remind must come back on the next focus: {s2}"

    check("F1 M1: pairing DURING the session disables Remind on the next window focus, no reload (and un-pairing re-enables it)",
          m1_pairing_mid_session_disables_without_reload)

    # =========================================================================
    # F2 -- stored XSS via inline onclick, now data-attrs + addEventListener
    # =========================================================================
    def f2_no_stored_xss_on_remind_or_statement():
        open_receivables()
        owner.evaluate("() => { window.__pwned = undefined; window.__pwned2 = undefined; }")
        stub_wa()
        # Located by id, never by visible text (both names are hostile to
        # text matching -- the whole point). A missing button is a FAILURE:
        # the earlier version silently `continue`d and could pass with zero
        # clicks having happened.
        for cust_id, tag in ((XSS1_ID, "quote-break name"), (XSS2_ID, "<img onerror> name")):
            loc = owner.locator(f'button[data-remind-id="{cust_id}"][data-remind-tone="polite"]')
            assert loc.count() == 1, f"expected exactly one enabled Remind for the {tag}, found {loc.count()}"
            owner.evaluate("() => { window.__lastOpenedUrl = null; }")
            real_click(owner, loc, f"Remind for the {tag}")
            assert wa_text(), f"the Remind click for the {tag} opened nothing (the handler never ran)"
        pwned = owner.evaluate("() => ({ p1: window.__pwned, p2: window.__pwned2 })")
        assert not pwned["p1"], "stored XSS fired via Remind (window.__pwned set)"
        assert not pwned["p2"], "stored XSS fired via Remind (window.__pwned2 set)"
        for cust_id, name in ((XSS1_ID, XSS_NAME_1), (XSS2_ID, XSS_NAME_2)):
            open_statement_by_click(cust_id)
            owner.evaluate("() => { window.__lastOpenedUrl = null; }")
            real_click(owner, owner.locator("#stmt-send-btn"), "Send Statement for a hostile-named customer")
            text = wa_text()
            assert name in text, f"the hostile name should reach the message as plain text, got:\n{text}"
            close_statement()
        pwned2 = owner.evaluate("() => ({ p1: window.__pwned, p2: window.__pwned2 })")
        assert not pwned2["p1"] and not pwned2["p2"], f"stored XSS fired via the Statement modal: {pwned2}"

    check("F2: customers named \"Z');window.__pwned=1;//\" and \"<img onerror>\" never execute via Remind/Statement (real clicks, buttons must exist)",
          f2_no_stored_xss_on_remind_or_statement)

    def f2_grep_no_remaining_onclick_with_share_interpolation():
        # "grep your own additions for any other onclick=\"...${ built from
        # user data." A source-level guard, cheap and permanent -- catches a
        # FUTURE regression, not just today's fixed instances.
        src = (REPO / "products" / "retail" / "frontend" / "subsystem-retail.js").read_text(encoding="utf-8")
        hits = re.findall(r'onclick="RetailSystem\.(_shareReminderOnWhatsApp|_shareStatementOnWhatsApp|_shareReceiptOnWhatsApp)\([^)]*\$\{', src)
        assert not hits, f"found inline onclick still interpolating data into a share function call: {hits}"

    check("F2 source guard: no onclick=\"...${ interpolation remains on any _share*OnWhatsApp call site",
          f2_grep_no_remaining_onclick_with_share_interpolation)

    # =========================================================================
    # F3 / HIGH-1 / HIGH-2 -- the numbers come from ONE server FIFO
    # =========================================================================
    def server_aging_by_id():
        st, body = api(owner, "GET", "/api/sub/retail/reports/aging?type=receivable")
        assert st == 200, f"aging report HTTP {st}"
        return {str(c["id"]): c for c in body["customers"]}

    def send_remind(cust_id, tone, lang):
        set_msg_lang(lang)
        open_receivables()
        stub_wa()
        loc = owner.locator(f'button[data-remind-id="{cust_id}"][data-remind-tone="{tone}"]')
        assert loc.count() == 1, f"no enabled {tone} Remind for {cust_id}"
        real_click(owner, loc, f"{tone} Remind for {cust_id}")
        return wa_text()

    def send_statement(cust_id, lang):
        set_msg_lang(lang)
        open_receivables()
        stub_wa()
        open_statement_by_click(cust_id)
        real_click(owner, owner.locator("#stmt-send-btn"), f"Send Statement for {cust_id}")
        text = wa_text()
        ctx = owner.evaluate("() => RetailSystem._lastStatementContext")
        close_statement()
        return text, ctx

    def f3_client_shows_what_the_server_computed():
        aging = server_aging_by_id()
        od = aging[str(OVERDUE_ID)]
        assert 44 <= od["days_overdue"] <= 46, f"expected ~45 days overdue from the SERVER, got {od['days_overdue']}"
        assert abs(od["overdue_amount"] - 14.5) < 0.001, f"expected 14.500 overdue from the SERVER, got {od['overdue_amount']}"
        open_receivables()
        client = owner.evaluate(
            "(id) => { const a = RetailSystem._partyAgingById.get(String(id)); "
            "return a ? {days: a.daysOverdue, amt: a.overdueAmount} : null; }", OVERDUE_ID)
        assert client, "the client kept no aging for the overdue customer"
        assert client["days"] == od["days_overdue"] and abs(client["amt"] - od["overdue_amount"]) < 0.001, \
            f"the client shows {client}, the server said {od['days_overdue']} days / {od['overdue_amount']}"

    check("F3: a real 45-day overdue debt -- the SERVER's answer (~45 days, 14.500) is exactly what the client holds",
          f3_client_shows_what_the_server_computed)

    def f3_old_payer_paid_debt_is_not_overdue():
        # THE VERIFIER'S SCENARIO: a 100-day-old charge fully paid by a LATER
        # account payment plus a fresh charge today. Nothing is overdue.
        aging = server_aging_by_id()
        op = aging[str(OLD_PAYER_ID)]
        assert (op.get("overdue_amount") or 0) == 0, f"server says Old Payer has overdue money: {op}"
        open_receivables()
        s = remind_state(OLD_PAYER_ID)
        assert s["row"] and s["tones"] == [] and not s["disabled"], \
            f"a customer with nothing overdue must have no Remind at all (not even a disabled one): {s}"

    check("F3(b): a 100-day-old charge paid by a later payment is not overdue on the server and has NO Remind on screen",
          f3_old_payer_paid_debt_is_not_overdue)

    def f3_case_d_prepaid_then_charged():
        # Verifier CASE D: 20 prepaid, then a 30 charge -> owes 10. The old
        # client FIFO dropped the prepayment and told the customer 30.
        aging = server_aging_by_id()
        pd = aging[str(PREPAID_ID)]
        assert abs(pd["overdue_amount"] - 10.0) < 0.001, f"case D overdue should be 10, server says {pd['overdue_amount']}"
        assert abs(pd["oldest_unpaid_amount"] - 10.0) < 0.001, f"case D oldest open should be 10, server says {pd['oldest_unpaid_amount']}"
        text = send_remind(PREPAID_ID, "firm", "en")
        assert has_amt(text, 10.0), f"the reminder must say 10.000:\n{text}"
        assert not has_amt(text, 30.0), f"the reminder must NOT say the raw 30.000 charge:\n{text}"
        stext, ctx = send_statement(PREPAID_ID, "en")
        m_bal = re.search(r"Current balance: (?:JD )?(\d+\.\d{3})", stext)
        m_old = re.search(r"Oldest unpaid: (?:JD )?(\d+\.\d{3})", stext)
        assert m_bal and m_old, f"statement lacks its balance / oldest-unpaid lines:\n{stext}"
        assert abs(float(m_bal.group(1)) - 10.0) < 0.001 and abs(float(m_old.group(1)) - 10.0) < 0.001, \
            f"case D statement must say balance 10 and oldest 10:\n{stext}"

    check("F3 HIGH-1 (case D): prepaid 20 then a 30 charge -> the reminder AND the statement both say 10, never 30",
          f3_case_d_prepaid_then_charged)

    def f3_case_c_three_charges_one_payment():
        aging = server_aging_by_id()
        tc = aging[str(THREE_ID)]
        assert abs(tc["overdue_amount"] - 15.0) < 0.001, f"case C overdue should be 15, server says {tc['overdue_amount']}"
        assert abs(tc["oldest_unpaid_amount"] - 5.0) < 0.001, f"case C oldest open should be 5, server says {tc['oldest_unpaid_amount']}"
        assert 39 <= tc["days_overdue"] <= 41, f"case C oldest open is ~40 days old, server says {tc['days_overdue']}"
        text = send_remind(THREE_ID, "firm", "en")
        assert has_amt(text, 15.0) and re.search(r"\b4[01] days\b|\b39 days\b", text), \
            f"case C reminder must say 15.000 and ~40 days:\n{text}"
        stext, ctx = send_statement(THREE_ID, "en")
        m_bal = re.search(r"Current balance: (?:JD )?(\d+\.\d{3})", stext)
        m_old = re.search(r"Oldest unpaid: (?:JD )?(\d+\.\d{3})", stext)
        assert m_bal and m_old, f"statement lacks its balance / oldest-unpaid lines:\n{stext}"
        bal, old = float(m_bal.group(1)), float(m_old.group(1))
        assert abs(bal - 15.0) < 0.001, f"case C balance should be 15.000:\n{stext}"
        assert abs(old - 5.0) < 0.001, f"case C oldest unpaid should be 5.000:\n{stext}"
        assert old <= bal + 0.0005, f"the statement contradicts itself: oldest unpaid {old} > balance {bal}"

    check("F3 HIGH-1 (case C): three 10 charges + one 15 payment -> overdue 15, oldest open 5 at ~40 days; the statement never contradicts its balance",
          f3_case_c_three_charges_one_payment)

    def f3_sender_picks_tone():
        open_receivables()
        stub_wa()
        for tone in ("polite", "firm"):
            owner.evaluate("() => { window.__lastOpenedUrl = null; }")
            loc = owner.locator(f'button[data-remind-id="{OVERDUE_ID}"][data-remind-tone="{tone}"]')
            assert loc.count() == 1, f"missing the {tone} control"
            real_click(owner, loc, f"{tone} control")
            text = wa_text()
            if tone == "polite":
                polite = text
            else:
                firm = text
        assert polite != firm, "polite and firm sent the same text"
        assert "reminder" in polite.lower() or "نود تذكيركم" in polite, f"polite text lost its wording:\n{polite}"
        assert has_amt(firm, 14.5) or "د.أ" in firm, f"firm text lacks the amount:\n{firm}"

    check("F3(d): Remind (polite) and Firm each send their own template on a real click",
          f3_sender_picks_tone)

    def m2_statement_carries_newest_five_not_oldest():
        text, ctx = send_statement(MANY_ID, "en")
        events = ctx["events"]
        assert len(events) == 8, f"fixture assumption broken: expected 8 statement events, got {len(events)}"
        newest = sorted(round(abs(e["amount"])) for e in events[-5:])
        assert newest == sorted([15, 6, 14, 13, 4]), f"the endpoint's last five are not the fixture's newest five: {newest}"
        for amt in (15, 6, 14, 13, 4):
            assert has_amt(text, float(amt)), f"NEWEST line {amt}.000 is missing from the message:\n{text}"
        for amt in (21, 22, 23):
            assert not has_amt(text, float(amt)), f"OLDEST line {amt}.000 leaked into a message that should carry the newest five:\n{text}"

    check("F3 M2: a statement with 8 events sends the NEWEST five (15,6,14,13,4) and none of the oldest three (21,22,23)",
          m2_statement_carries_newest_five_not_oldest)

    # =========================================================================
    # F16 -- no phone number on the customer (M7: reason is visible text)
    # =========================================================================
    def f16_no_phone_disables_with_reason():
        open_receivables()
        s = remind_state(NOPHONE_ID)
        assert s["row"] and s["tones"] == [] and s["disabled"], \
            f"No Phone At All has a real 10-day-old debt -- expected a DISABLED Remind, got {s}"
        assert_reason_visible(s["why"], "No WhatsApp number for this customer", "no-phone Remind")
        reachable(owner, owner.locator(
            f'#pl-table-customer tr[data-open-statement-id="{NOPHONE_ID}"] .ret-btn-reason[data-remind-reason]'),
            "no-phone: reason text under Remind")
        open_statement_by_click(NOPHONE_ID)
        assert owner.query_selector("#stmt-send-btn") is None, "no-phone customer still has an enabled Send Statement"
        why = owner.evaluate("""() => { const e = document.getElementById('stmt-send-why');
            if (!e) return null; const cs = getComputedStyle(e), r = e.getBoundingClientRect();
            return {text: e.innerText.trim(), font: parseFloat(cs.fontSize),
                    visible: cs.visibility !== 'hidden' && cs.display !== 'none' && r.width > 0 && r.height > 0,
                    id: e.id, describedBy: (document.getElementById('stmt-send-btn-off') || {getAttribute: () => null}).getAttribute('aria-describedby')}; }""")
        assert_reason_visible(why, "No WhatsApp number for this customer", "no-phone Statement")
        reachable(owner, owner.locator("#stmt-send-why"), "no-phone: reason text under Send Statement")
        close_statement()

    check("F16/M7: a customer with no phone gets Remind AND Send Statement disabled, the reason shown as visible text (>=11px)",
          f16_no_phone_disables_with_reason)

    # =========================================================================
    # F6 / LOW-3 -- receipt dialog: pauses while typing, resumes after
    # =========================================================================
    def _open_receipt():
        go_pos(owner)
        # A toast from the sale that was JUST closed (F11 -> M4) is fixed at
        # bottom-right and sits over the Charge button for a few seconds; a real
        # person waits for it to go, so the harness does too. (Found, not
        # fixed: that toast overlapping Charge is app-shell.js territory.)
        owner.wait_for_function(
            "() => !document.querySelector('body > div[aria-atomic=\"true\"][role=\"status\"], "
            "body > div[aria-atomic=\"true\"][role=\"alert\"]')", timeout=12000)
        owner.evaluate("(id) => RetailSystem._addToCart(id)", PID_WIDGET)
        owner.wait_for_timeout(300)
        real_click(owner, owner.locator("#pos-checkout-btn"), "checkout")
        owner.wait_for_selector("#ret-receipt-dialog", timeout=10000)

    def _blur_wa_field():
        """A real click on a neutral point of the dialog itself: moves focus
        off the field the way a cashier tapping elsewhere does."""
        box = owner.locator("#ret-receipt-dialog").first.bounding_box()
        owner.mouse.click(box["x"] + 6, box["y"] + 6)

    def _dialog_open():
        return owner.query_selector("#ret-receipt-dialog") is not None

    def _close_receipt():
        if _dialog_open():
            real_click(owner, owner.locator('#ret-receipt-dialog button:has-text("New Sale")'), "New Sale")
            owner.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

    def low3_receipt_pauses_while_typing_then_resumes():
        # (a) focus + typing for ~9.5s: the 8s auto-close must NOT fire.
        _open_receipt()
        real_click(owner, owner.locator("#rcpt-wa-phone"), "WhatsApp field")
        t0 = time.monotonic()
        for ch in "0791234567":
            owner.keyboard.type(ch, delay=0)
            owner.wait_for_timeout(950)
        assert time.monotonic() - t0 > 9.0
        assert _dialog_open(), "the receipt closed while the cashier was still typing"
        # (b) leave the field: it must close ~8s AFTER the blur, not before.
        _blur_wa_field()
        t_blur = time.monotonic()
        closed_at = None
        while time.monotonic() - t_blur < 12:
            owner.wait_for_timeout(250)
            if not _dialog_open():
                closed_at = time.monotonic() - t_blur
                break
        assert closed_at is not None, "the receipt never auto-closed after the cashier left the field (the old bug: open forever)"
        assert 7.5 <= closed_at <= 10.0, f"auto-close came {closed_at:.2f}s after the blur; expected ~8s"
        print(f"      LOW-3(b): closed {closed_at:.2f}s after the field lost focus")
        # (c) refocus at ~5s: the countdown pauses again and the dialog outlives the first 8s.
        _open_receipt()
        real_click(owner, owner.locator("#rcpt-wa-phone"), "WhatsApp field (round 2)")
        _blur_wa_field()
        t_blur2 = time.monotonic()
        owner.wait_for_timeout(5000)
        assert _dialog_open(), "closed before 5s"
        real_click(owner, owner.locator("#rcpt-wa-phone"), "WhatsApp field (refocus)")
        owner.keyboard.type("0", delay=0)
        while time.monotonic() - t_blur2 < 10.5:
            owner.wait_for_timeout(250)
        assert _dialog_open(), "re-focusing/typing at ~5s must pause the countdown, but the dialog still closed"
        _close_receipt()

    check("F6/LOW-3: the receipt stays open while typing (>9s), closes ~8s after leaving the field, and re-focus/typing pauses it again",
          low3_receipt_pauses_while_typing_then_resumes)

    # =========================================================================
    # F11 -- SHORT sale number in the receipt message, long form absent
    # =========================================================================
    def f11_receipt_uses_short_sale_number_only():
        _open_receipt()
        stub_wa()
        long_number = owner.evaluate("() => RetailSystem._lastSaleData.sale_number")
        short_number = owner.evaluate("(n) => RetailSystem._docNo(n).main", long_number)
        owner.fill("#rcpt-wa-phone", "0791234567")
        real_click(owner, owner.locator("#rcpt-wa-send-btn"), "receipt Send")
        decoded = wa_text()
        assert short_number in decoded, f"expected the SHORT sale number {short_number!r} in the message:\n{decoded}"
        if long_number != short_number:
            assert long_number not in decoded, \
                f"the LONG internal sale number {long_number!r} must be ABSENT from the message:\n{decoded}"
        _close_receipt()

    check("F11: the receipt WhatsApp message (real clicks) uses the SHORT sale number; the long internal form is absent",
          f11_receipt_uses_short_sale_number_only)

    # =========================================================================
    # M4 / LOW-1 -- the message follows branding_receipt_language: labels,
    # dates AND the currency mark, for the receipt, statement and reminder.
    # =========================================================================
    ARABIC_LETTERS = re.compile(r"[؀-ۿ]")
    EN_MONTH = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")

    def _receipt_text(lang):
        set_msg_lang(lang)
        _open_receipt()
        stub_wa()
        total = owner.evaluate("() => RetailSystem._lastSaleData.total")
        owner.fill("#rcpt-wa-phone", "0791234567")
        real_click(owner, owner.locator("#rcpt-wa-send-btn"), "receipt Send")
        text = wa_text()
        _close_receipt()
        return text, total

    def m4_english_messages():
        sym = owner.evaluate("() => RetailSystem._currencySymbol")
        assert sym == "JD", f"fixture assumption: the shop currency mark is 'JD', got {sym!r}"
        text, total = _receipt_text("en")
        assert "Your receipt" in text and "Total" in text, f"English receipt labels missing:\n{text}"
        assert f"JD {total:.3f}" in text, f"English receipt money must read 'JD {total:.3f}':\n{text}"
        assert not ARABIC_LETTERS.search(text), f"an English receipt contains Arabic:\n{text}"
        stext, _ = send_statement(THREE_ID, "en")
        # The activity labels are matched in their LINE form ("- Payment:"),
        # never as a bare substring: the fixture debtor is literally named
        # "Three Charges One Payment", so the statement's own title line would
        # satisfy a bare "Payment" for free. What this can no longer catch: an
        # English label rendered without the "- " bullet / ":" shape.
        for label in ("Statement", "Current balance", "Oldest unpaid", "Recent activity", "- Credit sale:", "- Payment:"):
            assert label in stext, f"English statement is missing {label!r}:\n{stext}"
        assert "JD 15.000" in stext, f"English statement money must read 'JD 15.000':\n{stext}"
        assert re.search(r"\((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4}\)", stext), \
            f"English statement dates must be 'Mon D, YYYY':\n{stext}"
        assert not ARABIC_LETTERS.search(stext), f"an English statement contains Arabic:\n{stext}"
        rtext = send_remind(OVERDUE_ID, "polite", "en")
        assert "JD 14.500" in rtext and re.search(r"\b4[456] days\b", rtext) and "reminder" in rtext.lower(), \
            f"English reminder wrong:\n{rtext}"
        assert not ARABIC_LETTERS.search(rtext), f"an English reminder contains Arabic:\n{rtext}"

    check("M4 (en): receipt, statement and reminder read fully in English -- labels, 'Mon D, YYYY' dates, 'JD' mark",
          m4_english_messages)

    def m4_arabic_messages():
        text, total = _receipt_text("ar")
        assert "فاتورتك" in text and "الإجمالي" in text, f"Arabic receipt labels missing:\n{text}"
        assert f"د.أ {total:.3f}" in text, f"Arabic receipt money must read 'د.أ {total:.3f}':\n{text}"
        assert "JD" not in text and "Your receipt" not in text and "Total" not in text, f"an Arabic receipt leaks English/JD:\n{text}"
        stext, _ = send_statement(THREE_ID, "ar")
        for label in ("كشف حساب", "الرصيد الحالي", "أقدم مبلغ غير مسدد", "آخر الحركات", "- بيع آجل:", "- الدفع:"):
            assert label in stext, f"Arabic statement is missing {label!r}:\n{stext}"
        assert "د.أ 15.000" in stext, f"Arabic statement money must read 'د.أ 15.000':\n{stext}"
        # Line-form again ("- Payment:"), because the customer NAME contains the
        # English word "Payment" and legitimately appears in the title line.
        assert "JD" not in stext and "- Credit sale:" not in stext and "- Payment:" not in stext, \
            f"an Arabic statement leaks English/JD:\n{stext}"
        assert not EN_MONTH.search(stext), f"an Arabic statement carries an English month name:\n{stext}"
        assert re.search(r"\(\d{1,2} \S+ \d{4}\)", stext), f"Arabic statement dates must be 'D month YYYY' with Western digits:\n{stext}"
        rtext = send_remind(OVERDUE_ID, "polite", "ar")
        assert "نود تذكيركم" in rtext and "د.أ 14.500" in rtext and re.search(r"4[456] يوماً", rtext), \
            f"Arabic reminder wrong:\n{rtext}"
        assert "JD" not in rtext and "days" not in rtext, f"an Arabic reminder leaks English/JD:\n{rtext}"
        set_msg_lang("ar")

    check("M4 (ar): receipt, statement and reminder read fully in Arabic -- labels, Arabic month dates, 'د.أ' mark, no 'JD'",
          m4_arabic_messages)

    # =========================================================================
    # FIX ROUND F4 -- chart palette: real Chart.js instances, no state-token collision
    # =========================================================================
    def f4_chart_colours_avoid_state_tokens_in_sand_and_night():
        results = {}
        for theme in ("sand", "night"):
            owner.evaluate("(th) => ThemeEngine.apply(th)", theme)
            owner.wait_for_timeout(400)
            go(owner, "dashboard")
            owner.wait_for_timeout(900)
            data = owner.evaluate("""
                () => {
                    const stateVars = ['--state-success-text','--state-danger-text','--state-warning-text','--state-info-text'];
                    const cs = getComputedStyle(document.documentElement);
                    const states = stateVars.map(v => cs.getPropertyValue(v).trim().toLowerCase());
                    const chart = window.Chart && Chart.getChart && Chart.getChart(document.getElementById('r-dash-pay'));
                    const colours = chart ? [].concat(...chart.data.datasets.map(d => Array.isArray(d.backgroundColor) ? d.backgroundColor : [d.backgroundColor])) : [];
                    return { states, colours: colours.map(c => String(c).toLowerCase()) };
                }
            """)
            results[theme] = data
            owner.screenshot(path=str(SCRATCH / f"f4_dashboard_{theme}.png"))
        for theme, data in results.items():
            assert data["colours"], f"expected real Chart.js dataset colours on r-dash-pay in {theme}"
            for c in data["colours"]:
                assert c not in data["states"], \
                    f"[{theme}] chart dataset colour {c} equals a state-meaning token {data['states']}"
        owner.evaluate("() => ThemeEngine.apply('sand')")

    check("F4: real Chart.js dataset colours (sand + night, screenshotted) never equal a state token",
          f4_chart_colours_avoid_state_tokens_in_sand_and_night)

    # =========================================================================
    # FIX ROUND F12 -- category stripe contrast >= 3:1, monogram outline visible
    # =========================================================================
    def f12_stripe_contrast_across_themes():
        def relative_luminance(rgb_str):
            import re as _re
            nums = [int(x) for x in _re.findall(r"[\d.]+", rgb_str)[:3]]
            def lin(c):
                v = c / 255
                return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
            r, g, b = (lin(c) for c in nums)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        def contrast(a, b):
            la, lb = relative_luminance(a), relative_luminance(b)
            hi, lo = max(la, lb), min(la, lb)
            return (hi + 0.05) / (lo + 0.05)

        go_pos(owner)
        owner.wait_for_selector(".pos-card", timeout=8000)
        failures = []
        for theme in ("light", "sand", "dark", "night", "dusk"):
            owner.evaluate("(th) => ThemeEngine.apply(th)", theme)
            owner.wait_for_timeout(350)
            data = owner.evaluate("""
                () => {
                    const card = document.querySelector('.pos-card');
                    if (!card) return null;
                    const cs = getComputedStyle(card);
                    return { stripe: cs.borderInlineStartColor || cs.borderLeftColor, bg: cs.backgroundColor };
                }
            """)
            if not data:
                continue
            r = contrast(data["stripe"], data["bg"])
            if r < 3.0:
                failures.append(f"{theme}: stripe {data['stripe']} vs tile {data['bg']} = {r:.2f}:1")
        owner.evaluate("() => ThemeEngine.apply('sand')")
        assert not failures, "category stripe below the 3:1 non-text-UI floor:\n  " + "\n  ".join(failures)

    check("F12: the category stripe clears >=3:1 against the tile background in all 5 themes (measured live)",
          f12_stripe_contrast_across_themes)

    # =========================================================================
    # REAL BUG found while writing F4+F12 above (not one of F1-F16, but a
    # live crash in this crew's own owned file, found and fixed on the spot
    # per ENGINEERING.md's root-cause standard): RetailSystem.render()'s
    # generation guard (_beginRender/_isStaleRender) was bumped ONLY inside
    # _renderDashboard, so it caught a second dashboard render superseding
    # the first, but NOT a navigation to a DIFFERENT section. A dashboard
    # stats fetch left in flight when the user navigated away (e.g. to POS)
    # would resolve later, sail through the (dashboard-only) staleness
    # check, and crash writing into r-k-* ids that no longer existed --
    # which _navigate's own catch then painted as a "Failed to load
    # dashboard" error panel OVER WHATEVER SCREEN THE USER HAD ALREADY
    # MOVED TO. Reproduced live (standalone repro, 2026-09-28): switch theme
    # twice on Dashboard, then navigate to POS before the resulting backlog
    # of /dashboard/stats fetches drains --
    #   [console.error] Retail dashboard error: Cannot set properties of
    #   null (setting 'textContent')
    #   at Object._renderDashboard (subsystem-retail.js:2967:56)
    # -- and the POS screen the test had already navigated to was replaced
    # by the dashboard error panel. Fixed by moving the ONE _beginRender()
    # bump to the top of render() itself, so EVERY navigation (not just a
    # second dashboard visit) invalidates an in-flight render -- which is
    # what the guard's own pre-existing comment already claimed it did.
    def bug_dashboard_late_fetch_does_not_clobber_a_different_screen():
        go(owner, "dashboard")
        owner.wait_for_timeout(600)
        for theme in ("sand", "night"):
            owner.evaluate("(th) => ThemeEngine.apply(th)", theme)
            owner.wait_for_timeout(350)
            go(owner, "dashboard", wait_ms=700)
        owner.evaluate("() => ThemeEngine.apply('sand')")
        go(owner, "pos", wait_ms=1000)
        state = owner.evaluate(
            "() => ({ found: !!document.getElementById('pos-product-grid'), "
            "errorPanel: !!document.querySelector('#sub-content h3') && "
            "document.querySelector('#sub-content h3').textContent.includes('Failed to load') })"
        )
        assert state["found"], (
            "POS did not render after a dashboard theme-switch backlog -- "
            f"state={state}"
        )
        assert not state["errorPanel"], "a late dashboard fetch clobbered the POS screen with its own error panel"

    check("BUG (found live, fixed): a late dashboard stats fetch no longer clobbers a screen the user already navigated to",
          bug_dashboard_late_fetch_does_not_clobber_a_different_screen)

    def bug_mutation_generation_guard_scoped_to_dashboard_only_reproduces_the_crash():
        # MUTATION PROOF: simulate the ORIGINAL (pre-fix) shape of the guard
        # -- "stale" only within dashboard's own renders -- by forcing
        # _isStaleRender to always answer false (never stale), which is
        # exactly what a dashboard-only counter looks like from a
        # different-section navigation's point of view (nothing dashboard-
        # specific ever ran to make it stale). This targets the GUARD the
        # fix depends on, not the one added call site, and is the same
        # "prove the guard is load-bearing" shape every other mutation proof
        # in this file uses.
        go(owner, "dashboard")
        owner.wait_for_timeout(600)
        owner.evaluate("() => { RetailSystem.__realIsStale = RetailSystem._isStaleRender; RetailSystem._isStaleRender = () => false; }")
        for theme in ("sand", "night"):
            owner.evaluate("(th) => ThemeEngine.apply(th)", theme)
            owner.wait_for_timeout(350)
            go(owner, "dashboard", wait_ms=700)
        owner.evaluate("() => ThemeEngine.apply('sand')")
        pageerrors = []
        owner.on("pageerror", lambda e: pageerrors.append(str(e)))
        go(owner, "pos", wait_ms=1200)
        red_state = owner.evaluate("() => !!document.getElementById('pos-product-grid')")
        print(f"      MUTATION PROOF (dashboard cross-navigation guard): _isStaleRender forced false -> POS grid present = {red_state}")
        owner.evaluate("() => { RetailSystem._isStaleRender = RetailSystem.__realIsStale; delete RetailSystem.__realIsStale; }")
        # Give any already-in-flight late fetches (queued before the restore)
        # a moment to land, then confirm the REAL guard recovers the screen.
        go(owner, "dashboard", wait_ms=500)
        for theme in ("sand", "night"):
            owner.evaluate("(th) => ThemeEngine.apply(th)", theme)
            owner.wait_for_timeout(350)
            go(owner, "dashboard", wait_ms=700)
        owner.evaluate("() => ThemeEngine.apply('sand')")
        go(owner, "pos", wait_ms=1200)
        green_state = owner.evaluate("() => !!document.getElementById('pos-product-grid')")
        print(f"      MUTATION PROOF (dashboard cross-navigation guard): restored -> POS grid present = {green_state}")
        assert green_state is True, "restoring the real guard should let POS render cleanly"

    check("BUG mutation: forcing _isStaleRender() to never report stale reproduces the crash pattern; restoring it recovers",
          bug_mutation_generation_guard_scoped_to_dashboard_only_reproduces_the_crash)

    # =========================================================================
    # UNBOOKED note -- the server says how much of the STORED balance no sale
    # or payment on this computer explains. The shopkeeper sees a small neutral
    # note on the Receivables row and in the statement; a customer never sees
    # it in a WhatsApp message. Both directions: shown when non-zero, absent
    # when zero. State is created by a fixture UPDATE of credit_balance (an
    # import / backfill / synced sale is exactly "balance with no sale behind it").
    # =========================================================================
    UNBOOKED_EXTRA = 7.25
    UNBOOKED_FRAGMENT = "isn't matched to a sale on this computer"

    UB = {}

    def unbooked_note_both_directions():
        ub_id = make_debtor("Unbooked Balance Customer", "0797778899")
        UB["id"] = ub_id
        credit_charge(ub_id, 10.0, 10)
        c = sqlite3.connect(str(RETAIL_DB))
        try:
            n = c.execute("UPDATE customers SET credit_balance = credit_balance + ? WHERE id = ?",
                          (UNBOOKED_EXTRA, ub_id)).rowcount
            c.commit()
        finally:
            c.close()
        assert n == 1, f"fixture UPDATE matched {n} customers"
        aging = server_aging_by_id()
        assert abs(aging[str(ub_id)]["unbooked_amount"] - UNBOOKED_EXTRA) < 0.001, \
            f"server should report unbooked {UNBOOKED_EXTRA}: {aging[str(ub_id)]}"
        assert abs(aging[str(OVERDUE_ID)]["unbooked_amount"]) < 0.001, \
            f"fixture assumption: a plain credit customer has unbooked 0: {aging[str(OVERDUE_ID)]}"
        set_msg_lang("en")

        # -- Receivables row: note SHOWN for the unbooked customer, ABSENT for the plain one
        open_receivables()
        row = '#pl-table-customer tr[data-open-statement-id="%s"]'
        note = owner.locator(row % ub_id + " [data-unbooked-note]")
        assert note.count() == 1, f"expected exactly one unbooked note on the unbooked row, found {note.count()}"
        reachable(owner, note, "unbooked note on the Receivables row")
        txt = note.inner_text()
        assert UNBOOKED_FRAGMENT in txt and "7.250" in txt, f"row note wrong: {txt!r}"
        fsz = note.evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
        assert fsz >= 11, f"note is {fsz}px, below the 11px floor"
        assert owner.locator(row % OVERDUE_ID + " [data-unbooked-note]").count() == 0, \
            "a customer whose balance is fully explained must show NO unbooked note"
        assert owner.locator("#pl-table-customer [data-unbooked-note]").count() == 1, \
            "only the one unbooked customer may carry a note"

    check("UNBOOKED (Receivables row): note shown when the server reports unbooked 7.250, absent for a fully-explained customer",
          unbooked_note_both_directions)

    # Separate check on purpose: the STATEMENT endpoint (retail_api.py,
    # customer_statement) must carry `unbooked_amount` for this to pass, and
    # that file is crew D's, not this crew's. Kept apart so a red here names
    # the missing server field instead of hiding a passing Receivables check.
    def unbooked_note_in_statement_both_directions():
        ub_id = UB["id"]
        set_msg_lang("en")
        open_statement_by_click(ub_id)
        snote = owner.locator("#ret-statement-dialog [data-unbooked-note]")
        assert snote.count() == 1, f"expected one unbooked note in the statement, found {snote.count()}"
        reachable(owner, snote, "unbooked note in the statement modal")
        stxt = snote.inner_text()
        assert UNBOOKED_FRAGMENT in stxt and "7.250" in stxt, f"statement note wrong: {stxt!r}"
        close_statement()
        open_statement_by_click(OVERDUE_ID)
        assert owner.locator("#ret-statement-dialog [data-unbooked-note]").count() == 0, \
            "a fully-explained customer's statement must show NO unbooked note"
        close_statement()

    check("UNBOOKED (statement modal): note shown when the statement reports unbooked 7.250, absent for a fully-explained customer",
          unbooked_note_in_statement_both_directions)

    def unbooked_never_in_a_customer_message():
        ub_id = UB["id"]
        # -- Never in a customer message (reminder AND statement, en and ar)
        for lang in ("en", "ar"):
            rtext = send_remind(ub_id, "polite", lang)
            assert "matched" not in rtext and not has_amt(rtext, UNBOOKED_EXTRA) and "غير مطابق" not in rtext, \
                f"the unbooked note leaked into the {lang} reminder:\n{rtext}"
            stext, _ = send_statement(ub_id, lang)
            assert "matched" not in stext and not has_amt(stext, UNBOOKED_EXTRA) and "غير مطابق" not in stext, \
                f"the unbooked note leaked into the {lang} statement message:\n{stext}"
        set_msg_lang("ar")

    check("UNBOOKED (messages): the note never appears in a WhatsApp reminder or statement, en and ar",
          unbooked_never_in_a_customer_message)

    # =========================================================================
    # FINAL polish round (whole-P1a acceptance check). Every check is named
    # "FINAL Fn:" so `P1A_ONLY="FINAL F1:"` runs exactly one of them, which is
    # how the mutation proofs drive them.
    # =========================================================================
    FINAL_AR = {
        # Pending in i18n-T.json (the catalog owner merges them into ar.json);
        # merged into the live dictionary here so the Arabic pass measures the
        # words that will ship, not the English fallback.
        "Send on WhatsApp": "إرسال عبر واتساب",
        "Can't load": "تعذّر التحميل",
        "WhatsApp number": "رقم واتساب",
    }

    def ui_lang(pg, lang):
        pg.evaluate("(d) => { Object.assign(AuraI18n.dicts.ar, d); }", FINAL_AR)
        pg.evaluate("(l) => { AuraI18n.current = l; localStorage.setItem('aura_lang', l); AuraI18n.apply(); }", lang)
        assert pg.evaluate("() => document.documentElement.getAttribute('lang')") == lang, \
            f"the page did not switch to {lang}"

    STOCK_VALUE_URL = "**/api/sub/retail/products/stock-value"

    def open_products_and_read_stock_value(pg):
        """Returns (text of #prod-stock-value or None if the card is absent)."""
        go(pg, "products")
        pg.wait_for_selector("#prod-table tbody tr", timeout=8000)
        if pg.locator("#prod-stock-value").count() == 0:
            pg.wait_for_timeout(600)
            return None
        pg.wait_for_function(
            "() => document.getElementById('prod-stock-value').textContent.trim() !== '—'", timeout=8000)
        return pg.locator("#prod-stock-value").inner_text().strip()

    def final_f1_cashier_never_sees_a_stock_value():
        # The route is gated retail.reports; a cashier does not hold it. The
        # old screen sent the request anyway, read the 403 body as a number,
        # and printed "JD 0.000" (real value 2,079.650).
        calls = []
        cashier.on("request", lambda r: calls.append(r.url) if "/products/stock-value" in r.url else None)
        go(cashier, "products")
        cashier.wait_for_selector("#prod-table tbody tr", timeout=8000)
        cashier.wait_for_timeout(700)
        assert cashier.locator("#prod-stock-value-kpi").count() == 0, "a cashier was given the stock-value card"
        assert cashier.locator("#prod-stock-value").count() == 0, "a cashier was given a stock-value figure element"
        body = cashier.locator("#sub-content").inner_text()
        assert "Stock value at cost" not in body, "the label 'Stock value at cost' is on a cashier's Products screen"
        assert not calls, f"a cashier's screen still asked for the stock value: {calls}"
        # Positive control (the allow half): the OWNER has the card and the route is really asked.
        owner_calls = []
        owner.on("request", lambda r: owner_calls.append(r.url) if "/products/stock-value" in r.url else None)
        shown = open_products_and_read_stock_value(owner)
        assert shown is not None, "the owner has no stock-value card"
        assert re.search(r"\d", shown) and shown != "Can't load", \
            f"the owner's real stock value did not load as a number: {shown!r}"
        assert owner_calls, "the owner's screen never asked for the stock value"

    check("FINAL F1: a cashier's Products screen has no stock-value card and never asks for it; the owner has it",
          final_f1_cashier_never_sees_a_stock_value)

    def final_f1_a_refusal_or_error_never_displays_as_a_number():
        cases = [
            ("403 refusal", 403, '{"status":"error","message":"forbidden"}'),
            ("500 error", 500, '{"status":"error","message":"boom"}'),
            ("200 without a value", 200, '{"status":"success","data":{}}'),
            ("200 with value null", 200, '{"value":null,"currency":"JOD"}'),
            ("200 with a string value", 200, '{"value":"lots","currency":"JOD"}'),
        ]
        for label, status, body in cases:
            owner.route(STOCK_VALUE_URL, _fulfil(body, status=status))
            try:
                shown = open_products_and_read_stock_value(owner)
            finally:
                owner.unroute(STOCK_VALUE_URL)
            assert shown is not None, f"[{label}] the owner's card vanished"
            assert not re.search(r"\d", shown), f"[{label}] a failure was displayed as a number: {shown!r}"
            assert shown == "Can't load", f"[{label}] expected \"Can't load\", got {shown!r}"
        # Arabic: the same failure says it in Arabic, still no digits.
        ui_lang(owner, "ar")
        try:
            owner.route(STOCK_VALUE_URL, _fulfil('{"status":"error"}', status=403))
            try:
                shown = open_products_and_read_stock_value(owner)
            finally:
                owner.unroute(STOCK_VALUE_URL)
            assert shown == FINAL_AR["Can't load"], f"[ar] expected the Arabic can't-load text, got {shown!r}"
        finally:
            ui_lang(owner, "en")
        # The allow half: a REAL zero is a number the server sent, and shows as 0.000; a real figure shows as itself.
        owner.route(STOCK_VALUE_URL, _fulfil('{"value":0,"currency":"JOD"}'))
        try:
            zero = open_products_and_read_stock_value(owner)
        finally:
            owner.unroute(STOCK_VALUE_URL)
        assert re.sub(r"[^\d.]", "", zero) == "0.000", f"a genuine zero must show as 0.000, got {zero!r}"
        owner.route(STOCK_VALUE_URL, _fulfil('{"value":1234.5,"currency":"JOD"}'))
        try:
            real = open_products_and_read_stock_value(owner)
        finally:
            owner.unroute(STOCK_VALUE_URL)
        assert re.sub(r"[^\d.]", "", real) == "1234.500", f"a real figure must show as itself, got {real!r}"

    check("FINAL F1: 403 / 500 / missing / null / non-number stock value shows \"Can't load\" (EN + AR), never a number; a real 0 and a real figure still show",
          final_f1_a_refusal_or_error_never_displays_as_a_number)

    def final_f2_no_cost_values_for_a_cashier_but_the_owner_has_them():
        go(cashier, "products")
        cashier.wait_for_selector("#prod-table tbody tr", timeout=8000)
        cashier.wait_for_timeout(500)
        heads = cashier.eval_on_selector_all("#prod-table thead th", "els => els.map(e => e.textContent.trim())")
        assert "Avg cost" not in heads, f"a cashier sees the Avg cost column header: {heads}"
        assert len(heads) == 7, f"the read-only Stock lookup should have 7 columns, has {len(heads)}: {heads}"
        assert cashier.locator("#prod-table [title*='cost' i]").count() == 0, \
            "a cashier's product table still carries a cost tooltip"
        assert cashier.locator("#sub-content").inner_text().count("Typed cost price") == 0
        rows = cashier.eval_on_selector_all(
            "#prod-table tbody tr", "els => els.map(e => ({t: e.innerText, n: e.children.length}))")
        widget = [r for r in rows if "Aura Test Widget" in r["t"]]
        gadget = [r for r in rows if "Aura Test Gadget" in r["t"]]
        assert widget and gadget, "positive control: the fixture products must be listed"
        assert "5.000" not in widget[0]["t"], f"the Widget's cost (5.000) is on a cashier's row: {widget[0]['t']!r}"
        assert "2.500" not in gadget[0]["t"], f"the Gadget's cost (2.500) is on a cashier's row: {gadget[0]['t']!r}"
        assert "10.000" in widget[0]["t"], f"positive control: the Widget's PRICE must still show: {widget[0]['t']!r}"
        assert all(r["n"] == 7 for r in rows), "a cashier's row has the wrong number of cells (header/row out of step)"

        go(owner, "products")
        owner.wait_for_selector("#prod-table tbody tr", timeout=8000)
        oheads = owner.eval_on_selector_all("#prod-table thead th", "els => els.map(e => e.textContent.trim())")
        assert "Avg cost" in oheads, f"the owner lost the Avg cost column: {oheads}"
        orows = owner.eval_on_selector_all(
            "#prod-table tbody tr", "els => els.map(e => ({t: e.innerText, n: e.children.length}))")
        ow = [r for r in orows if "Aura Test Widget" in r["t"]]
        assert ow and "5.000" in ow[0]["t"], f"the owner must see the Widget's cost 5.000: {ow}"
        assert owner.locator("#prod-table td[title*='Typed cost price']").count() >= 1, \
            "the owner's cost cells lost their tooltip"
        assert all(r["n"] == len(oheads) for r in orows), "an owner row is out of step with its header"

    check("FINAL F2: a cashier sees no cost value, header or tooltip anywhere on Products (7 columns); the owner has Avg cost",
          final_f2_no_cost_values_for_a_cashier_but_the_owner_has_them)

    RECEIPT_GEOM_JS = """() => {
      const dlg = document.getElementById('ret-receipt-dialog');
      const p = Array.from(dlg.querySelectorAll('p')).find(x => x.querySelector('bdi'));
      if (!p) return {found: false};
      const bdi = p.querySelector('bdi');
      const tn = Array.from(p.childNodes).find(n => n.nodeType === 3 && n.textContent.trim());
      const txt = tn.textContent;
      const lead = txt.length - txt.trimStart().length, end = txt.trimEnd().length;
      const rg = document.createRange(); rg.setStart(tn, lead); rg.setEnd(tn, end);
      const lr = rg.getBoundingClientRect(), br = bdi.getBoundingClientRect();
      return {found: true, label: txt.trim(), number: bdi.textContent, bdiDir: bdi.getAttribute('dir'),
              gap: Math.max(br.left - lr.right, lr.left - br.right), sameLine: Math.abs(lr.top - br.top) < 8,
              paraDir: getComputedStyle(p).direction, sendText: (document.getElementById('rcpt-wa-send-btn') || {}).textContent};
    }"""

    def final_f9_arabic_receipt_number_is_isolated_and_send_is_translated():
        ui_lang(owner, "ar")
        try:
            go_pos(owner)
            owner.evaluate("() => { window.__lastOpenedUrl = null; window.open = (u) => { window.__lastOpenedUrl = u; return null; }; }")
            owner.evaluate("(id) => RetailSystem._addToCart(id)", PID_WIDGET)
            owner.wait_for_timeout(300)
            owner.click("#pos-checkout-btn")
            owner.wait_for_selector("#ret-receipt-dialog", timeout=10000)
            owner.wait_for_timeout(300)
            g = owner.evaluate(RECEIPT_GEOM_JS)
            assert g["found"], "the receipt dialog has no document-number line with a <bdi>"
            assert g["paraDir"] == "rtl", f"positive control: the receipt must be rendering right-to-left, got {g['paraDir']}"
            assert g["label"] == "رقم الفاتورة", f"the label should be the Arabic 'Receipt #', got {g['label']!r}"
            assert g["bdiDir"] == "ltr", f"the document number is not pinned left-to-right: dir={g['bdiDir']!r}"
            assert re.match(r"^[A-Za-z0-9-]+", g["number"]), f"the number looks wrong: {g['number']!r}"
            assert g["sameLine"], "the label and the number are on different lines"
            assert g["gap"] >= 2, (f"the label and the number touch ({g['gap']:.1f}px apart): "
                                   f"the receipt would read 'رقم الفاتورة{g['number']}'")
            assert g["sendText"] == FINAL_AR["Send on WhatsApp"], \
                f"the Send button did not say the Arabic 'Send on WhatsApp': {g['sendText']!r}"
            owner.fill("#rcpt-wa-phone", "0791234567")
            real_click(owner, owner.locator("#rcpt-wa-send-btn"), "Arabic receipt Send button")
            owner.wait_for_timeout(300)
            url = owner.evaluate("() => window.__lastOpenedUrl")
            assert url and url.startswith("https://wa.me/962791234567?text="), f"the Send button did not open wa.me: {url!r}"
            print(f"      Arabic receipt: label={g['label']!r} number={g['number']!r} gap={g['gap']:.1f}px send={g['sendText']!r}")
        finally:
            owner.evaluate("() => document.querySelectorAll('.ret-modal-overlay').forEach(el => el.remove())")
            ui_lang(owner, "en")
            go_pos(owner)

    check("FINAL F9: Arabic receipt number sits in a left-to-right <bdi> with a visible gap from its label; Send says 'إرسال عبر واتساب' and is really clickable",
          final_f9_arabic_receipt_number_is_isolated_and_send_is_translated)

    PLACEHOLDER_FIT_JS = """() => {
      const inp = document.getElementById('rcpt-wa-phone');
      const btn = document.getElementById('rcpt-wa-send-btn');
      const cs = getComputedStyle(inp);
      const probe = document.createElement('span');
      probe.textContent = inp.placeholder;
      probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;left:-9999px;'
        + 'font:' + cs.font + ';letter-spacing:' + cs.letterSpacing;
      document.body.appendChild(probe);
      const need = probe.getBoundingClientRect().width;
      probe.remove();
      const avail = inp.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const ir = inp.getBoundingClientRect(), br = btn.getBoundingClientRect();
      return {placeholder: inp.placeholder, need, avail, sameRow: Math.abs(ir.top - br.top) < 12,
              inputW: inp.clientWidth};
    }"""

    def final_n3_receipt_whatsapp_hint_is_fully_readable():
        for lang in ("en", "ar"):
            ui_lang(owner, lang)
            try:
                go_pos(owner)
                owner.evaluate("(id) => RetailSystem._addToCart(id)", PID_WIDGET)
                owner.wait_for_timeout(300)
                owner.click("#pos-checkout-btn")
                owner.wait_for_selector("#ret-receipt-dialog", timeout=10000)
                owner.wait_for_timeout(300)
                g = owner.evaluate(PLACEHOLDER_FIT_JS)
                assert g["placeholder"].strip(), f"[{lang}] the WhatsApp box has no hint at all"
                assert g["sameRow"], f"[{lang}] the Send button wrapped away from the phone box"
                print(f"      [{lang}] hint {g['placeholder']!r}: needs {g['need']:.0f}px, has {g['avail']:.0f}px (box {g['inputW']}px)")
                assert g["need"] <= g["avail"], (
                    f"[{lang}] the hint {g['placeholder']!r} is cut off: it needs {g['need']:.1f}px "
                    f"but the box only has {g['avail']:.1f}px")
            finally:
                owner.evaluate("() => document.querySelectorAll('.ret-modal-overlay').forEach(el => el.remove())")
                ui_lang(owner, "en")
                go_pos(owner)

    check("FINAL N3: the receipt's WhatsApp number hint fits its box in English and Arabic, and Send stays on the same row",
          final_n3_receipt_whatsapp_hint_is_fully_readable)

    CUST_SELECT_JS = """(arrow) => {
      const s = document.getElementById('pos-customer');
      const cs = getComputedStyle(s);
      const opt = s.options[s.selectedIndex];
      const cv = document.createElement('canvas').getContext('2d');
      cv.font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
      const full = opt.textContent;
      const name = full.replace(/\\s*\\(\\+?[\\d ]+\\)\\s*$/, '');
      const avail = s.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - arrow;
      const rc = (el) => { const r = el.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; };
      const hold = document.querySelector('.pos-hold-btn'), voidb = document.querySelector('.pos-clear-btn');
      const title = document.getElementById('pos-pane-title'), hdr = document.querySelector('.pos-pane-hdr');
      return {full, name, nameW: cv.measureText(name).width, avail, selW: s.clientWidth,
              sel: rc(s), hold: rc(hold), voidb: rc(voidb), hdr: rc(hdr),
              titleW: title.clientWidth, titleClipped: title.scrollWidth > title.clientWidth + 1};
    }"""

    def final_f10_the_chosen_customer_is_readable_at_1366():
        problems, seen = [], []
        try:
            for lang in ("en", "ar"):
                ui_lang(cashier, lang)
                cashier.set_viewport_size({"width": 1366, "height": 768})
                go_pos(cashier)
                for label, val in (("Walk-in", ""), ("Old Payer", OLD_PAYER_ID), ("No Phone At All", NOPHONE_ID)):
                    cashier.select_option("#pos-customer", val)
                    cashier.wait_for_timeout(250)
                    g = cashier.evaluate(CUST_SELECT_JS, 24)
                    seen.append(f"[{lang}] {label!r}: name {g['nameW']:.0f}px in {g['avail']:.0f}px (select {g['selW']}px, title {g['titleW']}px)")
                    if g["nameW"] > g["avail"]:
                        problems.append(f"[{lang}] {g['name']!r} needs {g['nameW']:.0f}px but the select shows only {g['avail']:.0f}px")
                    ctr = lambda b: (b["t"] + b["b"]) / 2
                    if max(abs(ctr(g["sel"]) - ctr(g["hold"])), abs(ctr(g["sel"]) - ctr(g["voidb"]))) > 8:
                        problems.append(f"[{lang}] {label}: the header row wrapped (select/Hold/Void not on one line)")
                    if g["hdr"]["r"] > 1366 or g["voidb"]["r"] > 1366 or g["voidb"]["l"] < 0:
                        problems.append(f"[{lang}] {label}: the header row spills off the screen")
                reachable(cashier, cashier.locator("#pos-customer"), f"[{lang}] customer select")
            cashier.select_option("#pos-customer", "")
        finally:
            ui_lang(cashier, "en")
            go_pos(cashier)
        for s in seen:
            print("      " + s)
        assert not problems, "; ".join(problems)

    check("FINAL F10: at 1366x768, EN and AR, the chosen customer's name fits its select and Hold/Void stay on the same row",
          final_f10_the_chosen_customer_is_readable_at_1366)

    STATEMENT_LINES_JS = """() => {
      const dlg = document.getElementById('ret-statement-dialog');
      const lines = (el) => { const r = document.createRange(); r.selectNodeContents(el);
        const tops = new Set(Array.from(r.getClientRects()).filter(x => x.width > 0).map(x => Math.round(x.top / 4)));
        return tops.size; };
      const rows = Array.from(dlg.querySelectorAll('tbody tr')).map(tr => { const td = tr.querySelectorAll('td');
        return {kind: td[2].innerText.trim(), kindLines: lines(td[2]), amt: td[3].innerText.trim(), amtLines: lines(td[3]),
                bal: td[4].innerText.trim(), balLines: lines(td[4]),
                amtWs: getComputedStyle(td[3]).whiteSpace, balWs: getComputedStyle(td[4]).whiteSpace,
                kindMin: parseFloat(getComputedStyle(td[2]).minWidth) || 0}; });
      const tb = dlg.querySelector('table').getBoundingClientRect(), d = dlg.getBoundingClientRect();
      return {rows, overflowX: dlg.scrollWidth - dlg.clientWidth, tableL: tb.left, tableR: tb.right, dlgL: d.left, dlgR: d.right};
    }"""

    def final_f11_arabic_statement_money_and_kind_stay_on_one_line():
        problems = []
        try:
            ui_lang(owner, "ar")
            for vw in (1440, 1024, 768, 600):
                owner.set_viewport_size({"width": vw, "height": 900})
                owner.evaluate("(id) => RetailSystem._openPartyStatement('customer', id)", MANY_ID)
                owner.wait_for_selector("#ret-statement-dialog", timeout=8000)
                owner.wait_for_timeout(500)
                g = owner.evaluate(STATEMENT_LINES_JS)
                assert len(g["rows"]) == 8, f"positive control: the fixture statement should have 8 events, has {len(g['rows'])}"
                # The line counts above only bite when the table is squeezed;
                # the convention itself (money never breaks, the kind has a
                # floor) is asserted directly so it cannot quietly go missing
                # at a width nobody measured.
                r0 = g["rows"][0]
                if r0["amtWs"] != "nowrap" or r0["balWs"] != "nowrap":
                    problems.append(f"[{vw}] money cells may wrap (white-space: {r0['amtWs']}/{r0['balWs']})")
                if r0["kindMin"] < 100:
                    problems.append(f"[{vw}] the kind column has no floor (min-width {r0['kindMin']}px)")
                for r in g["rows"]:
                    for k in ("kind", "amt", "bal"):
                        if r[k + "Lines"] != 1:
                            problems.append(f"[{vw}] {k} {r[k]!r} is on {r[k + 'Lines']} lines")
                if g["overflowX"] > 1 or g["tableR"] > g["dlgR"] + 1 or g["tableL"] < g["dlgL"] - 1:
                    problems.append(f"[{vw}] the statement table spills out of its modal (scroll overflow {g['overflowX']}px)")
                print(f"      [{vw}] kinds={sorted({r['kind'] for r in g['rows']})} sample amount={g['rows'][0]['amt']!r}")
                close_statement()
        finally:
            owner.set_viewport_size({"width": 1440, "height": 900})
            owner.evaluate("() => document.querySelectorAll('.ret-modal-overlay').forEach(el => el.remove())")
            ui_lang(owner, "en")
        assert not problems, "; ".join(problems[:6])

    check("FINAL F11: in Arabic the statement's money and kind cells are one line each, and the table fits its modal (1440 / 1024 / 768 / 600)",
          final_f11_arabic_statement_money_and_kind_stay_on_one_line)

    def final_f12_a_seller_who_lands_on_the_dashboard_is_handed_the_till():
        REFUSAL = "limited to managers and the store owner"
        # "Never show a refusal" means not even for one frame: the navigation
        # replaces the landing within ~50 ms, so a check taken afterwards
        # cannot see a refusal that flashed and left. Record every DOM change.
        cashier.evaluate("""(needle) => {
          window.__sawRefusal = false;
          if (window.__refusalObs) window.__refusalObs.disconnect();
          // textContent, not innerText: a refusal painted for one frame inside
          // a not-yet-laid-out container has no innerText but is still text.
          const look = () => { if ((document.body.textContent || '').includes(needle)) window.__sawRefusal = true; };
          window.__refusalObs = new MutationObserver(look);
          window.__refusalObs.observe(document.body, {childList: true, subtree: true, characterData: true});
        }""", REFUSAL)
        go(cashier, "dashboard", wait_ms=1500)
        saw = cashier.evaluate("() => { window.__refusalObs.disconnect(); return window.__sawRefusal; }")
        assert not saw, "the refusal sentence was on the cashier's screen at some moment on the way to the till"
        assert cashier.evaluate("() => SubsystemApp.currentSection") == "pos", \
            "a cashier sent to the dashboard was not handed to the till"
        assert cashier.locator("#pos-product-grid").count() == 1, "the till did not render for the cashier"
        assert REFUSAL not in cashier.locator("#sub-content").inner_text(), "the cashier was shown the refusal sentence"
        # Allow half / positive control: the owner still gets the real dashboard.
        go(owner, "dashboard", wait_ms=1500)
        assert owner.evaluate("() => SubsystemApp.currentSection") == "dashboard", "the owner was moved off the dashboard"
        assert owner.locator("#pos-product-grid").count() == 0, "the owner was handed the till instead of the dashboard"
        assert REFUSAL not in owner.locator("#sub-content").inner_text()

    check("FINAL F12: a cashier sent to the dashboard ends on the till with no refusal sentence; the owner still gets the dashboard",
          final_f12_a_seller_who_lands_on_the_dashboard_is_handed_the_till)

    # =========================================================================
    # F15 / M5 -- a category's tone is its CREATION ORDER, so adding a category
    # never repaints an existing one.
    # =========================================================================
    ALL_TONES_JS = """() => { const o = {}; RetailSystem._categories.forEach(c => { o[c.name] = RetailSystem._categoryToneIndex(c.id); }); return o; }"""

    def f15_creation_order_tones_and_new_category_repaints_nothing():
        go_pos(owner)
        owner.wait_for_timeout(500)
        before = owner.evaluate(ALL_TONES_JS)
        # Literal numbers, not a mirror of the product's own sort: the first
        # three categories of this fixture were created in this order.
        assert [before["Tone Fixture 0"], before["Tone Fixture 1"], before["Tone Fixture 2"]] == [1, 2, 3], \
            f"the first three categories must be tones 1, 2, 3 in creation order, got {before}"
        n_before = len(before)
        time.sleep(1.1)  # created_at has second resolution; never tie
        _, body = api(owner, "POST", "/api/sub/retail/categories", {"name": "Accessories"})
        assert ((body or {}).get("data") or {}).get("id"), f"could not create Accessories: {body}"
        go_pos(owner)
        owner.wait_for_timeout(600)
        after = owner.evaluate(ALL_TONES_JS)
        changed = {k: (before[k], after[k]) for k in before if before[k] != after.get(k)}
        assert not changed, f"adding 'Accessories' repainted {len(changed)} existing categories: {changed}"
        assert after["Accessories"] == (n_before % 8) + 1, \
            f"the new category should take tone {(n_before % 8) + 1} (next in creation order), got {after['Accessories']}"
        # Stable across a full reload too (a pure function of the stored order).
        owner.reload()
        dismiss_intro_overlay(owner)
        owner.wait_for_selector("#sub-header-section", timeout=20000)
        go_pos(owner)
        owner.wait_for_timeout(600)
        again = owner.evaluate(ALL_TONES_JS)
        assert again == after, f"tones changed across a reload: {after} -> {again}"

    check("F15/M5: tones follow creation order (1,2,3); adding 'Accessories' changes 0 existing tones; stable across reload",
          f15_creation_order_tones_and_new_category_repaints_nothing)

    # =========================================================================
    # Pre-existing money bug -- "today" must be the shop's LOCAL date.
    # A context in Asia/Amman (UTC+3) whose clock stands at 01:00 on 30 Sep
    # local = 22:00 UTC on 29 Sep. The UTC date (29th) differs from the local
    # date (30th) -- exactly the window in which the old code was wrong.
    # =========================================================================
    FAKE_NOW = "2026-09-30T01:00:00+03:00"

    def _late_night_page():
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, timezone_id="Asia/Amman",
                                  storage_state=owner_ctx.storage_state())
        pg = ctx.new_page()
        pg.clock.set_fixed_time(FAKE_NOW)
        pg.goto(BASE)
        dismiss_intro_overlay(pg)
        pg.wait_for_selector("#sub-header-section", timeout=20000)
        pg.wait_for_timeout(800)
        clock = pg.evaluate("() => ({iso: new Date().toISOString(), local: new Date().toLocaleDateString('en-CA')})")
        assert clock["iso"].startswith("2026-09-29T22:00"), f"fake clock not applied: {clock}"
        assert clock["local"] == "2026-09-30", f"timezone not applied: {clock}"
        return ctx, pg

    def money_balance_sheet_defaults_to_local_business_date():
        ctx, pg = _late_night_page()
        try:
            go(pg, "balance-sheet", wait_ms=1200)
            pg.wait_for_selector("#bs-as-of", timeout=10000)
            v = pg.input_value("#bs-as-of")
            assert v == "2026-09-30", \
                f"at 01:00 local on 30 Sep the Balance Sheet 'as of' must default to 2026-09-30, got {v!r} (UTC date leak)"
        finally:
            ctx.close()

    check("MONEY: Balance Sheet 'as of' defaults to the LOCAL date at 01:00 (fake clock, Asia/Amman) -- 2026-09-30, not the UTC 29th",
          money_balance_sheet_defaults_to_local_business_date)

    def money_promotion_bare_dates_cover_the_whole_local_day():
        ctx, pg = _late_night_page()
        posted = []

        def capture(route):
            req = route.request
            if req.method == "POST":
                posted.append(req.post_data)
            route.continue_()

        try:
            go(pg, "promotions", wait_ms=1200)
            pg.route("**/api/sub/retail/promotions", capture)
            pg.evaluate("() => RetailSystem._openAddPromotion()")
            pg.wait_for_selector("#prm-save-btn", timeout=8000)
            pg.fill("#prm-name", "One Day Late Night")
            pg.fill("#prm-disc", "10")
            pg.select_option("#prm-product", str(PID_WIDGET))
            pg.fill("#prm-start", "2026-09-30")
            pg.fill("#prm-end", "2026-09-30")
            real_click(pg, pg.locator("#prm-save-btn"), "Save promotion")
            for _ in range(40):
                if posted:
                    break
                pg.wait_for_timeout(150)
            assert posted, "the promotion save never POSTed"
            body = json.loads(posted[0])
            assert body["starts_at"] == "2026-09-30 00:00:00", f"start must be local midnight: {body}"
            assert body["ends_at"] == "2026-09-30 23:59:59", f"a bare end date must run through 23:59:59: {body}"
            # The business consequence, compared the way the server compares (strings):
            assert body["ends_at"] >= "2026-09-30 10:00:00", \
                "a promotion ending 30 Sep would already be over by 10:00 on 30 Sep (bare date sorts below any time that day)"
        finally:
            ctx.close()

    check("MONEY: a promotion with start=end=30 Sep is saved 00:00:00 -> 23:59:59 (still live at 10:00), tested at 01:00 local",
          money_promotion_bare_dates_cover_the_whole_local_day)

    # =========================================================================
    cashier_ctx.close()
    manager_ctx.close()
    owner_ctx.close()
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
