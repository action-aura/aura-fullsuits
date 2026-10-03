"""A "day in the shop" for Aura Retail, driven through the REAL running app.

Boots the retail backend on port 5071 against a FRESH data dir, then plays one
shop day -- morning set-up, a dozen sales at the till (KEYBOARD ONLY, in a real
Chromium), returns, a customer payment, a paid-out, a cashier hand-over, an
evening close with a 1.000 shortfall, and the reports -- and compares what the
app shows/returns with arithmetic done HERE, from the scenario's own inputs.
It never derives an expected number from the app's own output.

This FINDS and MEASURES; it does not fix product code. Every deviation lands in
result.md/result.json as a row (PASS/FAIL) and a severity-tagged FINDING.

Run with the SYSTEM Python (has `playwright`), same as lab_e2e.py:
    C:\\Users\\MSI\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe scripts/lab/day_in_shop.py

Output (default C:\\Users\\MSI\\AuraLab\\proof\\day): result.json, result.md, screens/NN-name.png,
data_<timestamp>/ (the shop's database + backend log).

Facts this script encodes, each learned by running the app (not assumed):
  * An install with no licence is READ-ONLY (403 LICENSE_INACTIVE on every
    mutation), NOT "unlocked". The same trick the smoke suite uses is used here:
    sign up FIRST (unlicensed), THEN seed an active licence into licensing.db
    (commercial_runtime.licensing_contracts.test_support.seed_active_license).
    No AURA_* / OWNER_* variable reaches the backend process.
  * The till has no split-tender: one `payment_method` per sale (S3 is therefore
    a FINDING, not a step that can be performed).
  * A cashier holds no discount capability (POST /sales with discount_pct is
    403), so S4 (discount at F8) is rung by the owner in a second browser.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import http.cookiejar
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VENV_PY = r"C:\Users\MSI\Desktop\aura-fullsuits\.venv\Scripts\python.exe"
PORT = 5071
BASE = f"http://127.0.0.1:{PORT}"
OUT = Path(r"C:\Users\MSI\AuraLab\proof\day")
PWD = "DayTest123!"
OWNER_EMAIL = "owner@day.test"
R = "/api/sub/retail"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# Independent arithmetic (JOD, 3 decimals). Nothing here reads the app.
# --------------------------------------------------------------------------
def q3(x) -> D:
    return D(x).quantize(D("0.001"), rounding=ROUND_HALF_UP)


# sku -> (name, barcode, sell price, cost, tax %)
CATALOG = {
    "P1": ("Milk 1L", "1000000000011", D("1.250"), D("0.900"), D("16")),
    "P2": ("Bread", "1000000000028", D("0.500"), D("0.300"), D("16")),
    "P3": ("Soap", "1000000000035", D("2.300"), D("1.500"), D("16")),
    "P4": ("Rice 5kg", "1000000000042", D("7.900"), D("6.000"), D("16")),
    "P5": ("Books", "1000000000059", D("3.000"), D("2.000"), D("0")),
    "P6": ("Gum", "1000000000066", D("0.875"), D("0.500"), D("16")),
}
OPENING_QTY = {"P1": 20, "P2": 30, "P3": 10, "P4": 10, "P5": 10, "P6": 40}
PURCHASE_TOTAL = sum(D(OPENING_QTY[k]) * CATALOG[k][3] for k in CATALOG)  # 142.000


def calc(lines, disc_pct=0):
    """lines = [(sku, qty)]. Tax is computed on the net AFTER discount (the
    shop's default tax_calculation_mode). Returns subtotal/discount and the
    total under BOTH tax-rounding conventions (once per sale / once per line)
    so a legitimate fils-level convention difference is not called a bug."""
    sub = D(0)
    disc = D(0)
    tax_raw = D(0)
    tax_line = D(0)
    for sku, qty in lines:
        price, rate = CATALOG[sku][2], CATALOG[sku][4]
        gross = price * D(qty)
        d = gross * D(disc_pct) / D(100)
        net = gross - d
        sub += gross
        disc += d
        tax_raw += net * rate / D(100)
        tax_line += q3(net * rate / D(100))
    sub, disc = q3(sub), q3(disc)
    tax_a, tax_b = q3(tax_raw), q3(tax_line)
    return {
        "subtotal": sub, "discount": disc,
        "tax": tax_a, "tax_alt": tax_b,
        "total": sub - disc + tax_a, "total_alt": sub - disc + tax_b,
    }


def money_in(text: str):
    """First decimal number in a text blob (ASCII digits), or None."""
    m = re.search(r"-?\d[\d,]*\.\d+", (text or "").replace("\u066b", "."))
    return D(m.group(0).replace(",", "")) if m else None


# --------------------------------------------------------------------------
# Result bookkeeping
# --------------------------------------------------------------------------
class Rec:
    def __init__(self, out: Path):
        self.out = out
        self.screens = out / "screens"
        self.screens.mkdir(parents=True, exist_ok=True)
        for old in self.screens.glob("*.png"):     # a re-run must not leave a previous run's pictures behind
            old.unlink()
        self.steps: list[dict] = []
        self.findings: list[dict] = []
        self.console: list[str] = []
        self.shot_n = 0
        self.meta: dict = {}

    def shot(self, page, name: str) -> str:
        self.shot_n += 1
        fn = f"{self.shot_n:02d}-{name}.png"
        try:
            page.screenshot(path=str(self.screens / fn), full_page=False)
        except Exception as e:  # a failed screenshot must not fail the step
            print(f"   (screenshot {fn} failed: {e})")
        return fn

    def step(self, sid: str, name: str, ok, expected="", actual="", shot: str = "", note: str = ""):
        status = "PASS" if ok is True else ("FAIL" if ok is False else str(ok))
        row = dict(id=sid, name=name, status=status, expected=str(expected), actual=str(actual), screenshot=shot, note=note)
        self.steps.append(row)
        print(f"[{status}] {sid} {name} | exp={expected} act={actual} {note}")
        return ok is True

    def finding(self, sev, title, did, expected, happened, shot="", loc=""):
        f = dict(severity=sev, title=title, did=did, expected=expected, happened=happened, screenshot=shot, location=loc)
        self.findings.append(f)
        print(f"   FINDING [{sev}] {title}")

    def flush(self):
        passed = sum(1 for s in self.steps if s["status"] == "PASS")
        failed = sum(1 for s in self.steps if s["status"] == "FAIL")
        data = dict(meta=self.meta, passed=passed, failed=failed, other=len(self.steps) - passed - failed,
                    steps=self.steps, findings=self.findings, console_errors=self.console[:200])
        (self.out / "result.json").write_bytes(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        lines = ["# Aura Retail - a day in the shop", "",
                 f"PASS {passed} / FAIL {failed} / other {len(self.steps) - passed - failed}", "",
                 "| step | what | status | expected | actual | screenshot |", "|---|---|---|---|---|---|"]
        for s in self.steps:
            esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {s['id']} | {esc(s['name'])} | {s['status']} | {esc(s['expected'])} | {esc(s['actual'])} | {s['screenshot']} |")
        lines += ["", "## FINDINGS", ""]
        order = {"MONEY-WRONG": 0, "BLOCKS-CASHIER": 1, "SLOW": 2, "LOOKS-BROKEN": 3, "MINOR": 4}
        for i, f in enumerate(sorted(self.findings, key=lambda f: order.get(f["severity"], 9)), 1):
            lines += [f"### {i}. [{f['severity']}] {f['title']}",
                      f"- did: {f['did']}", f"- expected: {f['expected']}", f"- happened: {f['happened']}",
                      f"- screenshot: {f['screenshot']}", f"- cause location: {f['location'] or 'not located'}", ""]
        (self.out / "result.md").write_bytes("\n".join(lines).encode("utf-8"))


# --------------------------------------------------------------------------
# Backend + HTTP
# --------------------------------------------------------------------------
class Backend:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.proc = None
        self.log = None

    def start(self):
        env = {k: v for k, v in os.environ.items() if not (k.startswith("AURA_") or k.startswith("OWNER_"))}
        env.update(AURA_STANDALONE="1", PORT=str(PORT), AURA_APP_DATA=str(self.data_dir))
        leaked = [k for k in env if k.startswith("AURA_") or k.startswith("OWNER_")]
        assert sorted(leaked) == ["AURA_APP_DATA", "AURA_STANDALONE"], leaked
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log = open(self.data_dir / "backend_stdout.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen([VENV_PY, "app.py"], cwd=str(REPO / "products" / "retail" / "backend"),
                                     env=env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                urllib.request.urlopen(BASE + "/api/health", timeout=2)
                return
            except Exception:
                if self.proc.poll() is not None:
                    raise RuntimeError("backend exited early; see backend_stdout.log")
                time.sleep(0.5)
        raise RuntimeError("backend never became healthy")

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except Exception:
                p.kill()
        if self.log:
            self.log.close()


class Api:
    def __init__(self):
        self.o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with self.o.open(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw[:300].decode("utf-8", "replace")}

    def login(self, email, password=PWD):
        s, b = self.call("POST", "/api/auth/login", {"email": email, "username": email, "password": password})
        assert s == 200, (email, s, b)
        return self

    def data(self, method, path, body=None):
        s, b = self.call(method, path, body)
        assert s == 200, (method, path, s, b)
        return b


# --------------------------------------------------------------------------
# Browser helpers
# --------------------------------------------------------------------------
NAV_WAIT = 20000


def dismiss_intro(page):
    try:
        ov = page.locator("#aura-intro-overlay")
        ov.wait_for(state="visible", timeout=2500)
        ov.click()
        ov.wait_for(state="detached", timeout=3000)
    except Exception:
        pass


def ui_login(page, email, password=PWD):
    page.locator("#rl-email").wait_for(state="visible", timeout=NAV_WAIT)
    page.fill("#rl-email", email)
    page.fill("#rl-pass", password)
    page.press("#rl-pass", "Enter")
    page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=NAV_WAIT)


def open_till(page):
    page.evaluate("() => SubsystemApp._navigate('pos')")
    page.wait_for_selector("#pos-search", timeout=NAV_WAIT)
    page.wait_for_function("() => window.RetailSystem && RetailSystem._section === 'pos' && !!document.querySelector('#pos-product-grid')", timeout=NAV_WAIT)
    page.wait_for_timeout(600)


def qty_in_cart(page) -> float:
    return page.evaluate("() => (RetailSystem._cart||[]).reduce((s,i)=>s+Number(i.quantity||0),0)")


def lines_in_cart(page) -> int:
    return page.evaluate("() => (RetailSystem._cart||[]).length")


def focus_scan(page):
    """Keyboard-only: '/' is the documented key for 'back to the scan box'."""
    if page.evaluate("() => document.activeElement && document.activeElement.id") != "pos-search":
        page.keyboard.press("Slash")
        page.wait_for_timeout(80)


def scan(page, barcode: str, delay=8, timeout=8000):
    """What a barcode scanner does: type the code, then Enter."""
    focus_scan(page)
    before = qty_in_cart(page)
    page.keyboard.type(barcode, delay=delay)
    page.keyboard.press("Enter")
    page.wait_for_function("(b) => (RetailSystem._cart||[]).reduce((s,i)=>s+Number(i.quantity||0),0) > b", arg=before, timeout=timeout)


def read_totals(page) -> dict:
    g = lambda sel: page.evaluate("(s) => { const e = document.querySelector(s); return e ? e.innerText : null }", sel)
    return {k: money_in(g(sel)) for k, sel in dict(sub="#pos-sub", tax="#pos-tax", total="#pos-total", change="#pos-change").items()}


def wait_receipt(page, timeout=10000):
    page.wait_for_selector("#ret-receipt-dialog", state="visible", timeout=timeout)


SEEN_TEXTS: list = []   # (where, text) of dialogs read; scanned for '$'/USD/2-decimals at the end


def receipt_text(page) -> str:
    t = page.evaluate("() => { const e = document.querySelector('#ret-receipt-dialog'); return e ? e.innerText : '' }")
    SEEN_TEXTS.append(("sale-complete receipt", t))
    return t


def receipt_change(text: str):
    m = re.search(r"Change due\s*\n?\s*(?:JD|JOD)?\s*([\d.,]+)", text)
    return D(m.group(1).replace(",", "")) if m else None


def new_sale(page):
    page.keyboard.press("Enter")        # on the receipt: Enter = New Sale
    page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
    page.wait_for_timeout(150)


def clear_cart(page):
    page.evaluate("() => RetailSystem._clearCart()")


# JS that measures reachability / overflow / currency on the till.
MEASURE_JS = r"""
() => {
  const vw = innerWidth, vh = innerHeight;
  const reach = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return {sel, exists: false, ok: false, why: 'missing'};
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const inView = r.width > 0 && r.height > 0 && cx >= 0 && cy >= 0 && cx <= vw && cy <= vh;
    const top = inView ? document.elementFromPoint(cx, cy) : null;
    const ok = !!(inView && top && (top === el || el.contains(top)));
    return {sel, exists: true, ok, inView, why: ok ? '' : (inView ? ('covered by ' + (top ? (top.tagName + '#' + top.id + '.' + String(top.className).slice(0,40)) : 'nothing')) : 'off-screen'),
            rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]};
  };
  const controls = ['cash','card','mobile','transfer','credit','voucher'].map(m => reach('.pos-pay-btn[data-method="' + m + '"]'))
      .concat([reach('#pos-total'), reach('#pos-checkout-btn'), reach('#pos-tendered'), reach('#pos-search')]);
  const clipped = [];
  document.querySelectorAll('body *').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    if (el.closest('[hidden], .sub-identity-menu, #aura-intro-overlay')) return;
    const txt = (el.innerText || '').trim();
    if (!txt) return;
    const hOver = el.scrollWidth > el.clientWidth + 1;
    const ox = cs.overflowX;
    const clips = ox === 'hidden' || ox === 'clip' || cs.textOverflow === 'ellipsis';
    if (hOver && clips && el.clientWidth > 0) {
      clipped.push({tag: el.tagName, id: el.id, cls: String(el.className).slice(0, 50), text: txt.slice(0, 40), sw: el.scrollWidth, cw: el.clientWidth});
    }
  });
  const body = document.body.innerText || '';
  const dollars = (body.match(/\$|USD/g) || []).length;
  const two = [];
  document.querySelectorAll('.money, [class*="money"], #pos-sub, #pos-tax, #pos-total, #pos-change, #pos-tendered-hint, .pos-cart-row, .pos-card, .pos-pay-btn').forEach(el => {
    const t = (el.innerText || '');
    (t.match(/(?<![\d.])\d[\d,]*\.\d{2}(?!\d)/g) || []).forEach(x => two.push(x + '  in ' + (el.id || String(el.className).slice(0, 30))));
  });
  return {vw, vh, pageHScroll: document.documentElement.scrollWidth > vw + 1, controls, clipped: clipped.slice(0, 20), dollars, twoDecimals: two.slice(0, 20)};
}
"""


TOAST_JS = r"""
() => {
  const isToast = (el) => { for (let n = el; n; n = n.parentElement) { const r = n.getAttribute && n.getAttribute('role'); if ((r === 'status' || r === 'alert') && getComputedStyle(n).position === 'fixed') return true; } return false; };
  const out = [];
  for (const sel of ['#pos-total', '#pos-checkout-btn', '.pos-pay-btn[data-method="credit"]', '.pos-pay-btn[data-method="voucher"]', '#pos-tendered']) {
    const el = document.querySelector(sel); if (!el) continue;
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (top && isToast(top)) out.push(sel);
  }
  return {covered: out, toasts: Array.from(document.querySelectorAll('[role=status],[role=alert]')).filter(isToast).length};
}
"""


def wait_no_toasts(page, timeout=12000):
    try:
        page.wait_for_function("""() => !Array.from(document.querySelectorAll('[role=status],[role=alert]')).some(n => getComputedStyle(n).position === 'fixed' && n.innerText.trim())""", timeout=timeout)
    except Exception:
        pass


# --------------------------------------------------------------------------
# The day
# --------------------------------------------------------------------------
class Day:
    def __init__(self, rec: Rec, be: Backend, pw, browser):
        self.rec, self.be, self.pw, self.browser = rec, be, pw, browser
        self.owner = Api()
        self.sales: list[dict] = []     # every sale the day made, with independent expectations
        self.stock = dict(OPENING_QTY)  # expected on-hand, adjusted as the day goes
        self.pid: dict[str, str] = {}
        self.cust_id = None
        self.supp_id = None
        self.cash_expected = D(0)       # drawer cash, built from first principles
        self.refund_total = D(0)
        self.refund_tax = D(0)
        self.refund_net = D(0)
        self.cust_balance = D(0)
        self.session_id = None
        self.ctx_opts = dict(viewport={"width": 1366, "height": 768})

    # ---- plumbing ---------------------------------------------------------
    def new_page(self, viewport=None):
        ctx = self.browser.new_context(viewport=viewport or {"width": 1366, "height": 768})
        page = ctx.new_page()
        page.on("console", lambda m: self.rec.console.append(f"{m.type}: {m.text}"[:300]) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.rec.console.append(f"pageerror: {e}"[:300]))
        page.on("dialog", lambda d: d.dismiss())
        return page

    def note_progress(self, text):
        p = Path(r"C:\Users\MSI\.claude\jobs\f8252b48\tmp\day\progress.md")
        try:
            with open(p, "ab") as f:
                f.write(("\n" + text + "\n").encode("utf-8"))
        except OSError:
            pass

    # ---- MORNING ----------------------------------------------------------
    def morning(self):
        rec = self.rec
        page = self.new_page({"width": 1440, "height": 900})
        self.owner_page = page
        page.goto(BASE, wait_until="domcontentloaded")
        dismiss_intro(page)
        page.locator("#su-name").wait_for(state="visible", timeout=NAV_WAIT)
        page.fill("#su-name", "Owner Test")
        page.fill("#su-company", "Day Test Shop")
        page.fill("#su-email", OWNER_EMAIL)
        page.fill("#su-pass", PWD)
        page.fill("#su-pass2", PWD)
        s0 = rec.shot(page, "M1-first-run-form")
        page.click("#su-btn")
        page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=NAV_WAIT)

        # Licence: seeded AFTER signup (see module docstring).
        sys.path.insert(0, str(REPO))
        from commercial_runtime.licensing_contracts.test_support import seed_active_license
        seed_active_license(str(self.be.data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
        page.reload(wait_until="domcontentloaded")
        page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=NAV_WAIT)
        s1 = rec.shot(page, "M1-dashboard-after-signup")

        self.owner.login(OWNER_EMAIL)
        tax = self.owner.data("GET", R + "/settings/tax")["data"]
        rec.step("M1a", "shop currency is JOD with 3 decimals", tax["base_currency"] == "JOD" and tax["currency_decimals"] == 3,
                 "JOD / 3", f"{tax['base_currency']} / {tax['currency_decimals']} (symbol {tax['currency_symbol']})", s1)
        s, b = self.owner.call("POST", R + "/settings/tax", {"default_tax_rate": 16})
        rec.step("M1b", "default tax rate set to 16%", s == 200, "200", s)
        company = self.owner.data("GET", "/api/auth/session") if False else None
        rec.meta["tax_mode"] = tax["tax_calculation_mode"]
        rec.finding("MINOR", "The brief's premise 'licensing unset = unlocked' is false: the app is READ-ONLY",
                    "Started the backend with every AURA_*/OWNER_* variable stripped and tried to create an employee/product.",
                    "Everything unlocked, per the task brief and the CLAUDE.md claim.",
                    "Every mutation answered 403 LICENSE_INACTIVE until a licence row was seeded after signup (same trick the smoke suite uses).",
                    s1, "products/retail/tests/retail_smoke_e2e.py:1595-1622 (documents the sign-up-then-seed order); CLAUDE.md claim is stale")

        # M2 staff
        creds = {}
        for nm, email, extra in [("Rana", "rana@day.test", {"role": "cashier"}),
                                 ("Omar", "omar@day.test", {"role": "cashier"}),
                                 ("Bookkeeper", "book@day.test", {"preset": "accountant"})]:
            s, b = self.owner.call("POST", "/api/admin/employees", dict(email=email, name=nm, **extra))
            ok = s == 200 and "setup_link" in (b or {})
            if ok:
                tok = b["setup_link"].split("#setup/")[1]
                s2, _ = Api().call("POST", "/api/auth/employee/setup", {"token": tok, "password": PWD})
                ok = s2 == 200
            rec.step("M2-" + nm, f"staff account {nm} created and activated", ok, "created+set password", f"{s}")
        # the bookkeeper can read the books but cannot sell
        bk = Api().login("book@day.test")
        s_r, _ = bk.call("GET", R + "/reports/trial-balance")
        s_w, _ = bk.call("POST", R + "/sales", {"items": [], "payment_method": "cash", "amount_paid": 0})
        rec.step("M2-role", "Accountant: reports 200, selling refused", s_r == 200 and s_w in (400, 403),
                 "GET reports 200 / POST sale 400|403", f"{s_r} / {s_w}")

        # M3 products, customer, supplier
        for sku, (name, bc, price, cost, taxr) in CATALOG.items():
            b = self.owner.data("POST", R + "/products", dict(name=name, sku=sku, barcode=bc, sell_price=float(price),
                                                                cost_price=float(cost), tax_rate=float(taxr), unit="pc"))
            self.pid[sku] = b["data"]["id"]
        rec.step("M3a", "six products created (4x16%, 1x0%, one at 0.875)", len(self.pid) == 6, 6, len(self.pid))
        c = self.owner.data("POST", R + "/customers", {"name": "Abu Khaled", "phone": "0791234567"})
        self.cust_id = c["data"]["id"]
        s, _ = self.owner.call("PATCH", R + f"/customers/{self.cust_id}", {"credit_mode": "limited", "credit_limit": 100})
        rec.step("M3b", "customer Abu Khaled allowed on credit (limit 100.000)", s == 200, 200, s)
        sp = self.owner.data("POST", R + "/suppliers", {"name": "Main Supplier", "phone": "065000000"})
        self.supp_id = sp["data"]["id"]
        rec.step("M3c", "supplier created", bool(self.supp_id), "id", self.supp_id)

        # M4 opening stock bought ON CREDIT
        items = [{"product_id": self.pid[k], "quantity": OPENING_QTY[k], "unit_cost": float(CATALOG[k][3])} for k in CATALOG]
        po = self.owner.data("POST", R + "/purchase-orders", {"supplier_id": self.supp_id, "items": items, "amount_paid": 0, "due_date": "2026-11-01"})
        po_id = po["data"]["id"]
        self.owner.data("POST", R + f"/purchase-orders/{po_id}/receive", {})
        pay = self.owner.data("GET", R + "/suppliers/payables")
        stocks = {p["sku"]: D(str(p["total_stock"])) for p in self.owner.data("GET", R + "/products")["data"]}
        ok_stock = all(stocks[k] == D(OPENING_QTY[k]) for k in CATALOG)
        rec.step("M4a", "opening stock received equals the purchase", ok_stock, OPENING_QTY, {k: str(v) for k, v in stocks.items()})
        rec.step("M4b", "purchase on credit opens AP = 142.000", D(str(pay["total_payable"])) == PURCHASE_TOTAL,
                 PURCHASE_TOTAL, pay["total_payable"])
        self.note_progress("- MORNING done (M1-M4). Next: M5 + selling.")
        rec.flush()

    # ---- SELLING ----------------------------------------------------------
    def login_rana(self):
        rec = self.rec
        self.rana = self.new_page()
        p = self.rana
        p.goto(BASE, wait_until="domcontentloaded")
        dismiss_intro(p)
        ui_login(p, "rana@day.test")
        open_till(p)
        s = rec.shot(p, "M5-rana-signed-in-till")
        # M5 open the drawer from the keyboard: Shift+F1
        p.keyboard.press("Shift+F1")
        p.wait_for_selector("#cd-opening-float", state="visible", timeout=8000)
        p.keyboard.press("Control+A")
        p.keyboard.type("50", delay=30)
        s2 = rec.shot(p, "M5-open-shift-modal")
        p.keyboard.press("Enter")
        p.wait_for_selector("#cd-open-modal", state="detached", timeout=8000)
        cur = Api().login("rana@day.test").data("GET", R + "/cash-sessions/current")["data"]
        ok = bool(cur) and D(str(cur["opening_float"])) == D("50") and cur["status"] == "open"
        self.session_id = cur["id"] if cur else None
        rec.step("M5", "Rana opens the drawer with float 50.000 (Shift+F1, keyboard)", ok, "open, float 50.000",
                 f"{cur and cur['status']} {cur and cur['opening_float']}", s2)
        self.cash_expected = D("50")

    def pay_cash(self, page, tendered: str):
        page.keyboard.press("F1")
        page.wait_for_function("() => RetailSystem._paymentMethod === 'cash'", timeout=3000)
        page.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-tendered'", timeout=3000)
        page.keyboard.press("Control+A")
        page.keyboard.type(tendered, delay=20)

    def record_sale(self, sid, lines, method, by, tendered=None, disc=0, cust=None, note=""):
        e = calc(lines, disc)
        self.sales.append(dict(id=sid, lines=lines, method=method, by=by, tendered=tendered, disc=disc, cust=cust, exp=e, note=note))
        for sku, q in lines:
            self.stock[sku] -= q
        if method == "cash":
            self.cash_expected += e["total"]       # net of change
        if method == "credit":
            self.cust_balance += e["total"]
        return e

    def check_totals(self, sid, page, e, shot):
        t = read_totals(page)
        ok = (t["sub"] == e["subtotal"] and t["total"] in (e["total"], e["total_alt"]) and t["tax"] in (e["tax"], e["tax_alt"]))
        self.rec.step(sid + "-totals", f"{sid}: till shows subtotal/tax/total before charging", ok,
                      f"sub {e['subtotal']} tax {e['tax']} total {e['total']}", f"sub {t['sub']} tax {t['tax']} total {t['total']}", shot)
        return t

    def selling(self):
        rec, p = self.rec, self.rana
        # ---- S1 cash sale, keyboard only, TIMED -----------------------------
        focus_scan(p)
        pauses = 0.0
        t0 = time.perf_counter()
        p.keyboard.press("Digit2")
        time.sleep(0.12); pauses += 0.12
        p.keyboard.press("Shift+Digit8")                  # '*' -> "next item x2"
        time.sleep(0.12); pauses += 0.12
        scan(p, CATALOG["P1"][1])
        scan(p, CATALOG["P2"][1])
        scan(p, CATALOG["P6"][1])
        e1 = calc([("P1", 2), ("P2", 1), ("P6", 1)])
        qty_ok = qty_in_cart(p) == 4
        s_cart = rec.shot(p, "S1-cart-three-items")
        p.keyboard.press("Enter")                          # empty search -> go to pay
        p.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-tendered'", timeout=4000)
        p.keyboard.press("Control+A")
        p.keyboard.type("20", delay=15)
        chg = read_totals(p)["change"]
        s_pay = rec.shot(p, "S1-tendered-20-change-shown")
        p.keyboard.press("Enter")                          # charge
        wait_receipt(p)
        t_receipt = time.perf_counter() - t0
        rtxt = receipt_text(p)
        s_rcpt = rec.shot(p, "S1-receipt")
        rec.meta["S1_seconds_wall"] = round(t_receipt, 2)
        rec.meta["S1_seconds_scripted_pauses"] = round(pauses, 2)
        rec.meta["S1_keystrokes"] = 2 + 13 * 3 + 3 + 1 + 2 + 1
        self.record_sale("S1", [("P1", 2), ("P2", 1), ("P6", 1)], "cash", "rana", tendered=D("20"))
        exp_change = D("20") - e1["total"]
        rcpt_chg = receipt_change(rtxt)
        rec.step("S1-multiplier", "S1: '2*' then scan gives quantity 2 (4 units in cart)", qty_ok, 4, qty_in_cart(p) if False else "see cart shot", s_cart)
        rec.step("S1-change-live", "S1: change shown while typing 20.000", chg in (exp_change,), exp_change, chg, s_pay)
        rec.step("S1", "S1 cash sale: receipt change is 20.000 - total", rcpt_chg == exp_change, exp_change, rcpt_chg, s_rcpt)
        rec.step("S1-time", "S1 keyboard-only: first key to receipt shown", True, "n/a (measured)",
                 f"{t_receipt:.2f}s wall incl {pauses:.2f}s scripted pauses", s_rcpt)
        self.sale1_receipt = rtxt
        new_sale(p)

        # ---- S2 card sale ---------------------------------------------------
        scan(p, CATALOG["P3"][1]); scan(p, CATALOG["P4"][1])
        tc = p.evaluate(TOAST_JS)
        s_toast = rec.shot(p, "S2-toasts-over-total")
        rec.step("S2-toast", "S2 while scanning, the 'Added: ...' toasts do not cover the total / Charge / tender tiles", not tc["covered"], "nothing covered", f"covered={tc['covered']} toasts={tc['toasts']}", s_toast)
        if tc["covered"]:
            rec.finding("LOOKS-BROKEN", "The 'Added: <item>' toasts pile up over the TOTAL, the Credit/Voucher tiles and the Charge button at 1366x768",
                        "Scanned two items and looked at what is under the total / tender tiles / Charge centre points (elementFromPoint).",
                        "The running total stays visible while scanning.", f"Covered by toasts: {tc['covered']} ({tc['toasts']} toasts stacked bottom-right, each lingering for seconds). The cashier cannot read the total or reach Credit/Voucher until they fade.",
                        s_toast, "products/retail/frontend/app-shell.js:6117 (showToast, position:fixed bottom/inline-end); called per scan from subsystem-retail.js _activateProduct (`Added: ...`)")
        wait_no_toasts(p)
        p.keyboard.press("F2")
        p.wait_for_function("() => RetailSystem._paymentMethod === 'card'", timeout=3000)
        e2 = self.record_sale("S2", [("P3", 1), ("P4", 1)], "card", "rana")
        s = rec.shot(p, "S2-card-selected")
        self.check_totals("S2", p, e2, s)
        p.keyboard.press("F10")
        wait_receipt(p)
        rec.step("S2", "S2 card sale completes with F2 then F10", True, "receipt", "receipt", rec.shot(p, "S2-receipt"))
        new_sale(p)

        # ---- S3 split tender -------------------------------------------------
        has_split = p.evaluate("() => /split/i.test(document.querySelector('#pos-product-grid')?.closest('body')?.innerText || '') || !!document.querySelector('[data-method=\"split\"], .pos-split')")
        s = rec.shot(p, "S3-no-split-tender")
        rec.step("S3", "S3 split cash+card tender", "SKIPPED", "split tender available", f"split control present: {has_split}", s,
                 "till takes ONE payment_method per sale (POS_PAYMENT_METHODS + _checkout payload)")
        rec.finding("BLOCKS-CASHIER", "No split tender: a customer cannot pay part cash, part card",
                    "Looked at the till's six tender buttons F1-F6 and the sale payload.",
                    "A way to pay one sale with cash + card (S3).",
                    "Only one tender per sale exists; the sale payload carries a single payment_method and amount_paid. A customer paying half and half has to be rung as two sales or all on one tender.",
                    s, "products/retail/frontend/subsystem-retail.js:7032 (POS_PAYMENT_METHODS), :8425 (_checkout payload)")

        # ---- S4 discount (owner, in a second browser) ------------------------
        self.s4_owner()

        # ---- S5 hold, S6 cash, resume S5 ------------------------------------
        scan(p, CATALOG["P4"][1]); scan(p, CATALOG["P1"][1])
        e5 = calc([("P4", 1), ("P1", 1)])
        p.keyboard.press("F9")
        p.wait_for_selector("#hold-label", state="visible", timeout=4000)
        p.keyboard.type("Table 4", delay=20)
        s = rec.shot(p, "S5-hold-modal")
        p.keyboard.press("Enter")
        p.wait_for_selector("#ret-hold-modal", state="detached", timeout=5000)
        rec.step("S5a", "S5 sale put on hold (F9): cart is empty afterwards", lines_in_cart(p) == 0, 0, lines_in_cart(p), s)
        scan(p, CATALOG["P2"][1]); scan(p, CATALOG["P2"][1])
        e6 = self.record_sale("S6", [("P2", 2)], "cash", "rana", tendered=D("2"))
        p.keyboard.press("Enter")
        p.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-tendered'", timeout=4000)
        p.keyboard.press("Control+A"); p.keyboard.type("2", delay=15)
        p.keyboard.press("Enter")
        wait_receipt(p)
        rt6 = receipt_text(p)
        rec.step("S6", "S6 cash sale rung while S5 is held", receipt_change(rt6) == D("2") - e6["total"], D("2") - e6["total"], receipt_change(rt6), rec.shot(p, "S6-receipt"))
        new_sale(p)
        p.keyboard.press("Shift+F9")
        p.wait_for_selector(".pk-held-row", state="visible", timeout=6000)
        s = rec.shot(p, "S5-held-list")
        p.keyboard.press("Enter")
        p.wait_for_function("() => (RetailSystem._cart||[]).length === 2", timeout=6000)
        self.record_sale("S5", [("P4", 1), ("P1", 1)], "card", "rana")
        p.keyboard.press("F2")
        s = rec.shot(p, "S5-resumed")
        self.check_totals("S5", p, e5, s)
        p.keyboard.press("F10")
        wait_receipt(p)
        rec.step("S5", "S5 resumed with Shift+F9 + Enter, finished by card", True, "receipt", "receipt", rec.shot(p, "S5-receipt"))
        new_sale(p)

        # ---- S7 void a line, then charge ------------------------------------
        scan(p, CATALOG["P1"][1]); scan(p, CATALOG["P2"][1]); scan(p, CATALOG["P3"][1])
        p.keyboard.press("Delete")
        removed_ok = lines_in_cart(p) == 2
        names = p.evaluate("() => RetailSystem._cart.map(i => i.name)")
        e7 = self.record_sale("S7", [("P1", 1), ("P2", 1)], "cash", "rana", tendered=D("5"))
        s = rec.shot(p, "S7-after-delete")
        rec.step("S7a", "S7 Delete removes the LAST line (Soap), Milk+Bread stay", removed_ok and "Soap" not in names, "[Milk 1L, Bread]", names, s)
        p.keyboard.press("Enter")
        p.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-tendered'", timeout=4000)
        p.keyboard.press("Control+A"); p.keyboard.type("5", delay=15)
        p.keyboard.press("Enter")
        wait_receipt(p)
        rec.step("S7", "S7 charged after removing a line", receipt_change(receipt_text(p)) == D("5") - e7["total"], D("5") - e7["total"], receipt_change(receipt_text(p)), rec.shot(p, "S7-receipt"))
        new_sale(p)

        # ---- S8 credit sale to Abu Khaled -----------------------------------
        scan(p, CATALOG["P4"][1])
        p.keyboard.press("F7")
        p.wait_for_selector("#cp-search", state="visible", timeout=4000)
        p.keyboard.type("abu", delay=30)
        s = rec.shot(p, "S8-customer-picker")
        p.keyboard.press("Enter")
        p.wait_for_selector("#ret-cust-pick", state="detached", timeout=4000)
        p.keyboard.press("F5")
        p.wait_for_function("() => RetailSystem._paymentMethod === 'credit'", timeout=3000)
        e8 = self.record_sale("S8", [("P4", 1)], "credit", "rana", cust="Abu Khaled")
        s = rec.shot(p, "S8-credit-selected")
        self.check_totals("S8", p, e8, s)
        p.keyboard.press("F10")
        wait_receipt(p)
        rec.step("S8", "S8 credit sale on account to Abu Khaled", True, "receipt", "receipt", rec.shot(p, "S8-receipt"))
        new_sale(p)

        # ---- S9 price check ---------------------------------------------------
        stock_before = self.owner.data("GET", R + "/products")["data"]
        p.keyboard.press("F11")
        p.wait_for_selector("#pc-search", state="visible", timeout=4000)
        p.keyboard.type(CATALOG["P3"][1], delay=8)
        p.keyboard.press("Enter")
        p.wait_for_function("() => /Soap/.test(document.querySelector('#pc-result')?.innerText || '')", timeout=6000)
        shown = p.evaluate("() => document.querySelector('#pc-result').innerText")
        s = rec.shot(p, "S9-price-check")
        p.keyboard.press("Escape")
        p.wait_for_selector("#ret-pricecheck", state="detached", timeout=4000)
        price_shown = money_in(shown)
        rec.step("S9", "S9 price check shows Soap 2.300 and does not add it to the cart",
                 price_shown == CATALOG["P3"][2] and lines_in_cart(p) == 0, f"2.300, cart 0", f"{price_shown}, cart {lines_in_cart(p)}", s)
        focus_scan(p)
        rec.flush()
        self.note_progress("- SELLING S1-S9 done (see result.md).")

    def s4_owner(self):
        rec = self.rec
        op = self.new_page()
        self.owner_till = op
        op.goto(BASE, wait_until="domcontentloaded")
        dismiss_intro(op)
        ui_login(op, OWNER_EMAIL)
        open_till(op)
        # Rana first: does F8 let a cashier discount?
        p = self.rana
        scan(p, CATALOG["P3"][1])
        p.keyboard.press("F8")
        p.wait_for_timeout(500)
        focused = p.evaluate("() => document.activeElement && document.activeElement.id")
        toast = p.evaluate("() => Array.from(document.querySelectorAll('.toast, [class*=toast]')).map(e => e.innerText).join(' | ')")
        s = rec.shot(p, "S4-rana-F8")
        rec.step("S4a", "S4 Rana (cashier) presses F8: no discount field gets focus", focused != "pos-disc", "refused/explained", f"focus={focused!r} toast={toast[:80]!r}", s)
        rec.finding("MINOR", "A cashier cannot give any discount at all (F8 refused) - S4 had to be rung by the owner",
                    "Rana pressed F8 on a one-item cart.", "Cashier applies a discount (S4 as written).",
                    f"No discount field; message: {toast[:100]!r}. API agrees: POST /sales with discount_pct as a cashier -> 403 'You do not have permission to apply a discount.' Probably policy (CAP_DISCOUNT), but the plan's S4 is impossible for Rana.",
                    s, "products/retail/backend/api/retail_api.py (create_sale CAP_DISCOUNT), subsystem-retail.js _explainDiscountNeedsManager")
        clear_cart(p)

        scan(op, CATALOG["P3"][1]); scan(op, CATALOG["P5"][1])
        op.keyboard.press("F8")
        op.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-disc'", timeout=4000)
        op.keyboard.press("Control+A"); op.keyboard.type("10", delay=30)
        op.keyboard.press("Enter")
        op.wait_for_timeout(300)
        e4 = self.record_sale("S4", [("P3", 1), ("P5", 1)], "cash", "owner", tendered=D("6"), disc=10)
        s = rec.shot(op, "S4-discount-10pct")
        t = read_totals(op)
        ok = t["total"] in (e4["total"], e4["total_alt"])
        rec.step("S4-totals", "S4 owner: 10% discount at F8 -> till total", ok, f"total {e4['total']} (discount {e4['discount']})", f"total {t['total']}", s)
        op.keyboard.press("F1")
        op.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-tendered'", timeout=3000)
        op.keyboard.press("Control+A"); op.keyboard.type("6", delay=15)
        op.keyboard.press("Enter")
        wait_receipt(op)
        rt = receipt_text(op)
        rec.step("S4", "S4 discounted cash sale: change", receipt_change(rt) == D("6") - e4["total"], D("6") - e4["total"], receipt_change(rt), rec.shot(op, "S4-receipt"))
        new_sale(op)

    def returns_and_payments(self):
        rec = self.rec
        rana_api = Api().login("rana@day.test")
        sales = self.owner.data("GET", R + "/sales/recent?limit=100")["data"]
        s1 = sorted(sales, key=lambda r: r["id"])[0]
        det = self.owner.data("GET", R + f"/sales/{s1['id']}")["data"]
        milk = next(i for i in det["items"] if i["sku"] == "P1")
        # S10 return ONE milk from S1, cash refund. Expected refund = price + its tax, own arithmetic.
        exp_net = CATALOG["P1"][2]
        exp_tax = q3(exp_net * D("0.16"))
        exp_refund = exp_net + exp_tax
        s, b = rana_api.call("POST", R + "/returns", {"sale_id": s1["id"], "items": [{"product_id": self.pid["P1"], "quantity": 1, "sale_item_id": milk["id"]}],
                                                      "refund_method": "cash", "reason": "day-in-shop", "idempotency_key": "day-ret-1"})
        got = D(str(b["data"]["refund_amount"])) if s == 200 else None
        rec.step("S10", "S10 return one Milk from S1, cash refund = price + tax", s == 200 and got == exp_refund, exp_refund, got)
        if s == 200:
            self.refund_total, self.refund_tax, self.refund_net = exp_refund, exp_tax, exp_net
            self.stock["P1"] += 1
            self.cash_expected -= exp_refund
        # S8's effect on the books, checked BEFORE the payment hides it
        bal = D(str(self.owner.data("GET", R + f"/customers/{self.cust_id}/statement")["data"]["balance"]))
        ok_b = bal == self.cust_balance
        rec.step("S8-balance", "S8 credit sale put 9.164 on Abu Khaled's account (receivable opened)", ok_b, self.cust_balance, bal)
        if not ok_b:
            rec.finding("MONEY-WRONG", "A credit sale rung at the till does NOT charge the customer's account",
                        "S8: picked Abu Khaled with F7, chose Credit with F5, charged with F10 (JD 9.164 on screen and on the receipt).",
                        f"Abu Khaled owes {self.cust_balance}; statement shows a charge of 9.164.",
                        f"His balance is {bal} and his statement has no charge line. The sale is stored as payment_method=credit with amount_paid=9.164 (paid in full), so balance_due=0 and no receivable is ever created. The same sale posted over the API with amount_paid=0 DOES charge the account correctly, so the fault is the till's payload. Goods handed over, never owed.",
                        "23-S8-receipt.png", "products/retail/frontend/subsystem-retail.js:8483 (amount_paid = amountDue for every non-cash tender, including credit); server derives balance_due = total - amount_paid at products/retail/backend/api/retail_api.py:7085-7087")
        # S11 Abu Khaled pays 3.000 in cash
        s, b = rana_api.call("POST", R + f"/customers/{self.cust_id}/payments", {"amount": 3, "method": "cash"})
        newbal = D(str(b["data"]["new_balance"])) if s == 200 else None
        exp_bal = self.cust_balance - D("3")
        rec.step("S11", "S11 Abu Khaled pays 3.000 cash: balance = S8 total - 3.000", s == 200 and newbal == exp_bal, exp_bal, newbal)
        if s == 200:
            self.cust_balance = exp_bal
            self.cash_expected += D("3")
            self.cust_paid_cash = D("3")

    def paid_out(self):
        rec, p = self.rec, self.rana
        focus_scan(p)
        p.keyboard.press("Shift+F2")
        p.wait_for_selector("#cd-mv-amount", state="visible", timeout=6000)
        # the Type select sits before Amount in tab order; ArrowDown x3: float_in -> paid_out
        p.keyboard.press("Shift+Tab")
        for _ in range(3):
            p.keyboard.press("ArrowDown")
        typ = p.evaluate("() => document.querySelector('#cd-mv-type').value")
        p.keyboard.press("Tab")
        p.keyboard.type("3.5", delay=30)
        p.keyboard.press("Tab")
        p.keyboard.type("petty cash - cleaning", delay=10)
        s = rec.shot(p, "S12-paid-out-modal")
        p.keyboard.press("Enter")
        p.wait_for_selector("#cd-mv-modal", state="detached", timeout=6000)
        x = Api().login("rana@day.test").data("GET", R + f"/cash-sessions/{self.session_id}/x-report")["data"]
        got = D(str(x["movements"]["paid_out"]))
        rec.step("S12", "S12 paid-out 3.500 recorded from drawer keys (Shift+F2)", typ == "paid_out" and got == D("3.5"), "paid_out 3.500", f"type={typ} paid_out={got}", s)
        if typ == "paid_out" and got == D("3.5"):
            self.cash_expected -= D("3.5")
        focus_scan(p)

    def handover(self):
        rec, p = self.rec, self.rana
        # Rana leaves an item in the cart and signs out.
        scan(p, CATALOG["P6"][1])
        s = rec.shot(p, "H-rana-cart-before-signout")
        chip = p.locator("#sub-identity-chip-btn")
        chip.click()
        p.locator(".sub-identity-menu-item.is-danger").click()
        p.get_by_text("Sign out and clear sale").wait_for(state="visible", timeout=5000)
        s2 = rec.shot(p, "H-signout-confirm")
        # Cancel is focused by default (safe); Tab to the danger button, Enter.
        p.keyboard.press("Tab")
        p.keyboard.press("Enter")
        ui_login(p, "omar@day.test")
        open_till(p)
        cart_n = lines_in_cart(p)
        dom_rows = p.locator("#pos-cart .pos-cart-row").count()
        total_txt = p.evaluate("() => document.querySelector('#pos-total')?.innerText")
        s3 = rec.shot(p, "H-omar-till-empty")
        rec.step("H1", "Rana signs out with an item in the cart, Omar signs in: till is EMPTY",
                 cart_n == 0 and dom_rows == 0 and (money_in(total_txt) or D(0)) == 0, "0 lines, total 0.000", f"{cart_n} lines, {dom_rows} rows, total {total_txt!r}", s3)
        rec.finding("MINOR", "Signing out has no keyboard shortcut - it needs the mouse (account chip -> Log Out)",
                    "Looked for a till key for sign-out; used the account chip.", "A key (or at least a reachable tab order from the till).",
                    "Mouse click on #sub-identity-chip-btn then the Log Out item; the confirm dialog IS keyboard-friendly (Cancel focused, Tab, Enter).",
                    s2, "products/retail/frontend/app-shell.js:3770-3792 (identity menu); no F-key in subsystem-retail.js _onFunctionKey")
        # S13 Omar rings one cash sale
        scan(p, CATALOG["P5"][1])
        e13 = self.record_sale("S13", [("P5", 1)], "cash", "omar", tendered=D("10"))
        p.keyboard.press("Enter")
        p.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-tendered'", timeout=4000)
        p.keyboard.press("Control+A"); p.keyboard.type("10", delay=15)
        p.keyboard.press("Enter")
        wait_receipt(p)
        rec.step("S13", "S13 Omar one cash sale (Books, 0% tax)", receipt_change(receipt_text(p)) == D("10") - e13["total"], D("10") - e13["total"], receipt_change(receipt_text(p)), rec.shot(p, "S13-receipt"))
        new_sale(p)
        rec.flush()
        self.note_progress("- S10-S13 + handover done.")

    # ---- LOOKS ------------------------------------------------------------
    def looks(self):
        rec, p = self.rec, self.rana      # Rana's page now holds Omar's session
        for sku in ("P1", "P2", "P3", "P4"):
            scan(p, CATALOG[sku][1])
        combos = [((1366, 768), "large"), ((1366, 768), "normal"), ((1366, 768), "xlarge"),
                  ((1920, 1080), "large"), ((1920, 1080), "normal"), ((1920, 1080), "xlarge")]
        any_dollar = 0
        for (w, h), scale in combos:
            p.set_viewport_size({"width": w, "height": h})
            p.evaluate("(s) => AuraUiScale.set(s)", scale)
            wait_no_toasts(p)
            p.wait_for_timeout(500)
            m = p.evaluate(MEASURE_JS)
            tag = f"{scale}-{w}x{h}"
            s = rec.shot(p, f"L-till-{tag}")
            bad = [c for c in m["controls"] if not c["ok"]]
            rec.step(f"L-reach-{tag}", f"till {scale} {w}x{h}: tenders, total, Charge, tendered field reachable (elementFromPoint)", not bad,
                     "all 10 reachable", "ok" if not bad else "; ".join(f"{c['sel']} {c['why']}" for c in bad), s)
            if bad:
                rec.finding("BLOCKS-CASHIER" if any(c["sel"] in ("#pos-checkout-btn", "#pos-total") for c in bad) else "LOOKS-BROKEN",
                            f"Till controls not reachable at {scale} {w}x{h}", "Rang 4 lines and checked elementFromPoint at each control's centre.",
                            "Every tender, the total and Charge clickable.", "; ".join(f"{c['sel']} {c['why']} rect={c.get('rect')}" for c in bad), s,
                            "products/retail/frontend/subsystem-retail.js (till layout, Large/XL overrides); see retail_p1c_size_e2e.py")
            rec.step(f"L-clip-{tag}", f"till {scale} {w}x{h}: no clipped/overflowing text, no sideways page scroll",
                     not m["clipped"] and not m["pageHScroll"], "none", f"clipped={m['clipped'][:5]} hscroll={m['pageHScroll']}", s)
            if m["clipped"] or m["pageHScroll"]:
                rec.finding("LOOKS-BROKEN", f"Clipped text / sideways scroll at {scale} {w}x{h}", "Scanned every visible element for scrollWidth>clientWidth under overflow hidden/ellipsis.",
                            "Nothing clipped.", f"{json.dumps(m['clipped'][:6])} pageHScroll={m['pageHScroll']}", s, "products/retail/frontend/subsystem-retail.js till CSS")
            any_dollar += m["dollars"]
            rec.step(f"L-cur-{tag}", f"till {scale} {w}x{h}: no '$'/'USD', every amount 3 decimals", m["dollars"] == 0 and not m["twoDecimals"],
                     "0 and none", f"dollars={m['dollars']} two-decimal amounts={m['twoDecimals'][:5]}", s)
            if m["dollars"] or m["twoDecimals"]:
                rec.finding("MONEY-WRONG" if m["twoDecimals"] else "LOOKS-BROKEN", "Dollar sign or 2-decimal amount on a JOD till",
                            f"Scanned the till at {scale} {w}x{h}.", "JD amounts with 3 decimals.", f"dollars={m['dollars']} two-decimal={m['twoDecimals'][:6]}", s)
        p.set_viewport_size({"width": 1366, "height": 768})
        p.evaluate("(s) => AuraUiScale.set(s)", "large")
        clear_cart(p)
        rec.flush()
        self.note_progress("- LOOKS (sizes) done.")

    def arabic(self):
        rec, p = self.rec, self.rana
        p.evaluate("() => AuraI18n.toggle()")
        p.wait_for_function("document.documentElement.getAttribute('dir') === 'rtl'", timeout=8000)
        p.wait_for_timeout(800)
        open_till(p)
        for sku in ("P2", "P1"):
            scan(p, CATALOG[sku][1])
        p.wait_for_timeout(300)
        s = rec.shot(p, "AR-till-rtl")
        latin = p.evaluate(r"""() => {
          const out = new Set();
          const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          while (w.nextNode()) {
            const n = w.currentNode, el = n.parentElement;
            if (!el || el.closest('script,style,[hidden]')) continue;
            const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            const m = n.nodeValue.match(/[A-Za-z]{3,}/g); if (m) out.add(n.nodeValue.trim().slice(0, 50) + ' @@ ' + el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + '.' + String(el.className).slice(0, 40));
          }
          return Array.from(out);
        }""")
        names = {v[0] for v in CATALOG.values()}
        data_words = ("Day Test Shop", "Rana", "Omar", "Abu Khaled", "Ctrl K", "Owner Test")
        left = [t for t in latin if not any(n.lower() in t.split(" @@ ")[0].lower() for n in names) and t.split(" @@ ")[0] not in ("Aura", "Aura Retail")
                and not any(d.lower() in t.split(" @@ ")[0].lower() for d in data_words)]
        units = [t for t in (p.evaluate(r"""() => Array.from(document.querySelectorAll('.pos-card, .pos-card *')).filter(e =>  /\bpc\b/.test(e.textContent)).map(e => e.textContent.trim())""") or [])]
        rec.step("AR1", "Arabic till: no English text left (product names excepted)", not left, "none", left[:12], s)
        if left:
            rec.finding("LOOKS-BROKEN", "English text left on the Arabic till", "Switched to Arabic (RTL), rang 2 items, listed every visible Latin-letter text node.",
                        "All labels Arabic.", f"{left[:12]}", s, "products/retail/frontend/locales (missing keys) / hard-coded strings in subsystem-retail.js")
        if units:
            rec.finding("MINOR", "The unit 'pc' stays English on every product tile in Arabic", "Looked at the product tiles with the UI in Arabic.",
                        "Unit shown in Arabic (or neutral).", f"{len(units)} tile(s) read 'المخزون: N pc'", s, "products/retail/frontend/subsystem-retail.js (product tile stock line prints p.unit raw)")
        dir_ok = p.evaluate("() => document.documentElement.getAttribute('dir')") == "rtl"
        tot_dir = p.evaluate("() => { const t = document.querySelector('#pos-total'); return t ? getComputedStyle(t).direction : null }")
        # a cash sale in Arabic (S14) so the receipt can be looked at; counted in the day's books
        e14 = self.record_sale("S14", [("P2", 1), ("P1", 1)], "cash", "omar", tendered=D("5"))
        p.keyboard.press("Enter")
        p.wait_for_function("() => document.activeElement && document.activeElement.id === 'pos-tendered'", timeout=4000)
        p.keyboard.press("Control+A"); p.keyboard.type("5", delay=15)
        p.keyboard.press("Enter")
        wait_receipt(p)
        s2 = rec.shot(p, "AR-receipt-rtl")
        rtxt = receipt_text(p)
        latin_r = re.findall(r"[A-Za-z]{3,}", rtxt)
        left_r = [w for w in latin_r if w not in ("Aura", "SALE", "JD", "JOD") and not any(w.lower() in n.lower() for n in names)]
        rec.step("AR2", "Arabic receipt dialog: no English words left", not left_r, "none", sorted(set(left_r))[:12], s2)
        if left_r:
            rec.finding("LOOKS-BROKEN", "English words left in the Arabic sale-complete receipt", "Rang a cash sale with the UI in Arabic.",
                        "Receipt dialog fully Arabic.", f"{sorted(set(left_r))[:12]}", s2, "products/retail/frontend/subsystem-retail.js:8822 (_showReceipt)")
        rec.step("AR3", "Arabic: page is RTL", dir_ok, "rtl", f"dir={dir_ok} total-direction={tot_dir}", s2)
        # Held sale / hold modal in Arabic: known hard-coded English
        new_sale(p)
        scan(p, CATALOG["P2"][1])
        p.keyboard.press("F9")
        p.wait_for_selector("#hold-label", state="visible", timeout=4000)
        hold_txt = p.evaluate("() => document.querySelector('#ret-hold-dialog').innerText")
        s3 = rec.shot(p, "AR-hold-modal")
        hold_latin = re.findall(r"[A-Za-z]{3,}", hold_txt)
        rec.step("AR4", "Arabic: Hold-sale dialog fully Arabic", not hold_latin, "none", sorted(set(hold_latin))[:10], s3)
        if hold_latin:
            rec.finding("LOOKS-BROKEN", "Hold-sale (F9) dialog is English in an Arabic shop", "Opened F9 with UI in Arabic.", "Arabic title/body/buttons.",
                        f"English words: {sorted(set(hold_latin))[:10]}", s3, "products/retail/frontend/subsystem-retail.js:6246-6262 (_holdSale: untranslated h3, <p>, labels, buttons)")
        p.keyboard.press("Escape")
        p.wait_for_timeout(300)
        clear_cart(p)
        p.evaluate("() => AuraI18n.toggle()")
        p.wait_for_function("document.documentElement.getAttribute('dir') === 'ltr'", timeout=8000)
        p.wait_for_timeout(500)
        open_till(p)
        rec.flush()
        self.note_progress("- ARABIC done (S14 extra Arabic sale rung).")

    # ---- EVENING ----------------------------------------------------------
    def evening(self):
        rec, p = self.rec, self.rana
        om = Api().login("omar@day.test")
        x = om.data("GET", R + f"/cash-sessions/{self.session_id}/x-report")["data"]
        got = D(str(x["expected_cash"]))
        # E1 via the real X-report modal, keyboard (Shift+F3)
        focus_scan(p)
        p.keyboard.press("Shift+F3")
        p.wait_for_selector("#cd-x-dialog", state="visible", timeout=8000)
        p.wait_for_timeout(500)
        xtxt = p.evaluate("() => document.querySelector('#cd-x-dialog').innerText")
        s = rec.shot(p, "E1-x-report")
        p.keyboard.press("Escape")
        p.wait_for_selector("#cd-x-dialog", state="detached", timeout=4000)
        shown = None
        SEEN_TEXTS.append(("X report", xtxt))
        m = re.search(r"Expected cash in drawer\s*\n?\s*(?:JD|JOD)?\s*([\-\d.,]+)", xtxt)
        if m:
            shown = D(m.group(1).replace(",", ""))
        rec.step("E1", "E1 X report expected cash = float + cash sales - refund + customer cash payment - paid-out (own sum)",
                 got == self.cash_expected, self.cash_expected, f"API {got}; screen {shown}", s)
        rec.step("E1-screen", "E1 the X-report on screen agrees with the API", shown == got, got, shown, s)
        own_cash_sales = sum((x_["exp"]["total"] for x_ in self.sales if x_["method"] == "cash"), D(0))
        phantom = D(str(x["cash_sales"])) - own_cash_sales
        s8_total = next(x_["exp"]["total"] for x_ in self.sales if x_["id"] == "S8")
        rec.step("E1-cash-sales", "E1 X-report 'cash sales' = the cash sales actually rung (own sum, net of change)", phantom == 0, own_cash_sales, x["cash_sales"], s)
        if phantom != 0:
            rec.finding("MONEY-WRONG", f"The X/Z report counts {phantom} of CREDIT-sale value as cash in the drawer",
                        "Compared the X report's 'Cash sales' with the sum of the cash sales rung (net of change).",
                        f"Cash sales {own_cash_sales}", f"Cash sales {x['cash_sales']}: the extra {phantom} is the S8 credit sale ({s8_total}). Because the till sends amount_paid=total for a credit sale, the server records a 'cash' payment for it and the drawer expects cash nobody handed over - a cashier counting the real drawer is told they are {phantom} short.",
                        s, "same root cause as the S8 finding: subsystem-retail.js:8483; the report sums payments(method='cash') at products/retail/backend/api/retail_api.py:10214-10220")
        cust_cash = getattr(self, "cust_paid_cash", D(0))
        rec.step("E1-cust-cash", "E1 X-report expected cash includes the 3.000 Abu Khaled paid in cash (it is physically in the drawer)",
                 False if cust_cash and (got - self.cash_expected - phantom) == -cust_cash else (got - self.cash_expected - phantom) == 0,
                 f"+{cust_cash} counted", f"expected_cash {got}; after removing the S8 phantom the report is {got - self.cash_expected - phantom} off", s)
        if cust_cash and (got - self.cash_expected - phantom) == -cust_cash:
            rec.finding("MONEY-WRONG", f"Cash a customer pays on account ({cust_cash}) never reaches the drawer's expected cash",
                        "S11: Abu Khaled paid 3.000 in cash at the till (POST /customers/<id>/payments method=cash), then read the X report.",
                        f"Expected cash includes +{cust_cash}; the money is in the drawer (and the ledger's Cash account has it).",
                        f"X report omits it ('Cash sales' only sums payments with related_type='sale'); the cashier counts {cust_cash} MORE than expected and the close shows a false overage. The ledger Cash account and the drawer disagree by the same amount.",
                        s, "products/retail/backend/api/retail_api.py:10214-10220 (_cash_session_report: related_type='sale' only; receivable payments are written with related_type='customer' at :17224)")
        # E2 close 1.000 short of the REAL cash in the drawer (own sum), keyboard
        counted = self.cash_expected - D("1")
        p.keyboard.press("Shift+F4")
        p.wait_for_selector("#cd-close-counted", state="visible", timeout=8000)
        p.keyboard.type(str(counted), delay=30)
        p.wait_for_timeout(300)
        s = rec.shot(p, "E2-close-counted-short")
        p.keyboard.press("Tab")
        p.keyboard.press("Tab") if p.evaluate("() => document.activeElement && document.activeElement.id") != "cd-close-btn" else None
        focused = p.evaluate("() => document.activeElement && document.activeElement.id")
        p.keyboard.press("Enter")
        p.wait_for_selector("#cd-close-dialog", state="detached", timeout=8000)
        p.wait_for_timeout(800)
        s2 = rec.shot(p, "E2-after-close")
        ses = self.owner.data("GET", R + f"/cash-sessions/{self.session_id}")["data"]
        sess = ses.get("session", ses) if isinstance(ses, dict) else ses
        var = D(str(sess["variance"])) if sess.get("variance") is not None else None
        rec.step("E2a", "E2 drawer closed 1.000 short of the REAL cash: variance = -1.000, awaiting approval", var == D("-1.000") and sess["variance_status"] != "approved",
                 "variance -1.000, not approved", f"variance {var}, status {sess['variance_status']}, focus-on-close-btn={focused}", s2)
        s_c, b_c = om.call("POST", R + f"/cash-sessions/{self.session_id}/approve", {})
        rec.step("E2b", "E2 the cashier cannot approve their own shortfall", s_c == 403, 403, s_c)
        s_o, b_o = self.owner.call("POST", R + f"/cash-sessions/{self.session_id}/approve", {})
        sess2 = self.owner.data("GET", R + f"/cash-sessions/{self.session_id}")["data"]
        sess2 = sess2.get("session", sess2)
        rec.step("E2c", "E2 the owner approves the shortfall", s_o == 200 and sess2["variance_status"] == "approved", "approved", f"{s_o} {sess2['variance_status']}")
        self.note_progress("- E1/E2 done.")
        rec.flush()

    def reports(self):
        rec = self.rec
        A = self.owner
        S = self.sales
        gross = sum((s["exp"]["total"] for s in S), D(0))
        gross_alt = sum((s["exp"]["total_alt"] for s in S), D(0))
        tax_sum = sum((s["exp"]["tax"] for s in S), D(0))
        sub_sum = sum((s["exp"]["subtotal"] - s["exp"]["discount"] for s in S), D(0))
        by_m = {}
        for s in S:
            by_m[s["method"]] = by_m.get(s["method"], D(0)) + s["exp"]["total"]
        near = lambda a, b: abs(D(str(a)) - D(str(b))) <= D("0.002")
        # actual sales rows
        recent = A.data("GET", R + "/sales/recent?limit=200")["data"]
        rec.step("E3-count", "E3 number of sales recorded", len(recent) == len(S), len(S), len(recent))
        app_total = sum((D(str(r["total"])) for r in recent), D(0))
        rec.step("E3-sales-total", "E3 sum of every sale total (incl tax) vs own sum", near(app_total, gross) or near(app_total, gross_alt), f"{gross} (alt {gross_alt})", app_total)
        summ = A.data("GET", R + "/reports/summary?days=1")["data"] if False else A.data("GET", R + "/reports/summary")["data"]
        net_rev = gross - self.refund_total
        okrev = near(summ["revenue"], gross) or near(summ["revenue"], net_rev) or near(summ["revenue"], gross_alt) or near(summ["revenue"], gross_alt - self.refund_total)
        rec.step("E3-summary-revenue", "E3 report summary revenue = sales total (gross, or net of the refund)", okrev,
                 f"{gross} gross or {net_rev} net of refund", f"{summ['revenue']} (transactions {summ.get('transactions')})")
        okt = near(summ["tax_collected"], tax_sum) or near(summ["tax_collected"], tax_sum - self.refund_tax)
        rec.step("E3-summary-tax", "E3 tax total vs own sum (gross, or net of the refunded tax)", okt,
                 f"{tax_sum} gross or {tax_sum - self.refund_tax} net", summ["tax_collected"])
        pm = {r["payment_method"]: D(str(r["revenue"])) for r in A.data("GET", R + "/reports/payment-methods")["data"]}
        for m, exp in by_m.items():
            ok = m in pm and (near(pm[m], exp) or (m == "cash" and near(pm[m], exp - self.refund_total)))
            rec.step(f"E3-tender-{m}", f"E3 sales by tender: {m}", ok, f"{exp}" + (f" (or {exp - self.refund_total} net of refund)" if m == "cash" else ""), pm.get(m))
        rets = A.data("GET", R + "/returns")["data"]
        got_ref = sum((D(str(r.get("refund_amount", 0))) for r in rets), D(0))
        rec.step("E3-refunds", "E3 refunds total", near(got_ref, self.refund_total), self.refund_total, got_ref)
        # stock on hand
        stocks = {p["sku"]: D(str(p["total_stock"])) for p in A.data("GET", R + "/products")["data"]}
        bad = {k: (str(self.stock[k]), str(stocks.get(k))) for k in CATALOG if stocks.get(k) != D(self.stock[k])}
        rec.step("E3-stock", "E3 stock on hand per product = opening - sold + returned", not bad, {k: self.stock[k] for k in CATALOG}, {k: str(v) for k, v in stocks.items()} if bad else "all match")
        # customer + statement
        st = A.data("GET", R + f"/customers/{self.cust_id}/statement")["data"]
        ev = [(e["kind"], D(str(e["amount"]))) for e in st["events"]]
        s8 = next(s for s in S if s["id"] == "S8")["exp"]["total"]
        rec.step("E3-cust-balance", "E3 Abu Khaled balance = S8 - S11", D(str(st["balance"])) == self.cust_balance, self.cust_balance, st["balance"])
        rec.step("E3-cust-statement", "E3 Abu Khaled statement lists the charge S8 and the payment S11 and ends at the balance",
                 ev == [("charge", s8), ("payment", D("3"))] and D(str(st["events"][-1]["running_balance"])) == self.cust_balance, f"charge {s8}, payment 3.000", ev)
        # P&L
        today = time.strftime("%Y-%m-%d")
        pnl = A.data("GET", R + f"/reports/profit-and-loss?start_date={today}&end_date={today}")["data"]
        exp_rev_ex_tax = sub_sum - self.refund_net
        s_pl = ""
        rec.step("E3-pnl-gross", "E3 P&L gross revenue (ex-tax) = sum of sale subtotals after discount", near(pnl["gross_revenue"], sub_sum), sub_sum, pnl["gross_revenue"])
        ok_rev = near(pnl["revenue"], exp_rev_ex_tax)
        rec.step("E3-pnl-revenue", "E3 P&L revenue (net, ex-tax) = sales ex-tax - refunded ex-tax", ok_rev, exp_rev_ex_tax,
                 f"{pnl['revenue']} (sales_returns shown {pnl.get('sales_returns')})")
        cogs_exp = sum((CATALOG[k][3] * (D(OPENING_QTY[k]) - D(self.stock[k])) for k in CATALOG), D(0))
        rec.step("E3-pnl-cogs", "E3 P&L cost of goods sold = units sold net of returns x unit cost", near(pnl["cost_of_goods_sold"], cogs_exp), cogs_exp, pnl["cost_of_goods_sold"])
        # trial balance / ledger
        tb = A.data("GET", R + "/reports/trial-balance")["data"]
        accts = {a["name"]: a for a in tb["accounts"]}
        deb = sum((D(str(a["debit"])) for a in tb["accounts"]), D(0))
        cre = sum((D(str(a["credit"])) for a in tb["accounts"]), D(0))
        rec.step("E3-tb-balanced", "E3 trial balance: total debits == total credits", deb == cre, "equal", f"debits {deb} credits {cre}")
        import sqlite3, glob
        dbs = glob.glob(str(self.be.data_dir / "**" / "retail.db"), recursive=True)
        gl_d = gl_c = None
        if dbs:
            c = sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)
            try:
                gl_d, gl_c = c.execute("SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0) FROM gl_journal_lines").fetchone()
            finally:
                c.close()
        rec.step("E3-gl-balanced", "E3 general ledger lines (database): total debits == total credits", gl_d is not None and abs(gl_d - gl_c) < 0.0005, "equal", f"debits {gl_d} credits {gl_c}")
        inv_exp = sum((CATALOG[k][3] * D(self.stock[k]) for k in CATALOG), D(0))
        tax_pay_exp = tax_sum - self.refund_tax
        ar = accts.get("Accounts Receivable"); ap = accts.get("Accounts Payable"); tp = accts.get("Tax Payable"); inv = accts.get("Inventory")
        rec.step("E3-tb-ar", "E3 ledger Accounts Receivable = Abu Khaled's balance", ar and near(ar["balance"], self.cust_balance), self.cust_balance, ar and ar["balance"])
        rec.step("E3-tb-inv", "E3 ledger Inventory = stock on hand x cost", inv and near(inv["balance"], inv_exp), inv_exp, inv and inv["balance"])
        rec.step("E3-tb-tax", "E3 ledger Tax Payable = tax charged - tax refunded", tp and near(tp["balance"], tax_pay_exp), tax_pay_exp, tp and tp["balance"])
        if not ok_rev or not (tp and near(tp["balance"], tax_pay_exp)):
            rec.finding("MONEY-WRONG", "A return never reduces Tax Payable: the whole refund (tax included) is booked to Sales Returns",
                        "Sold on a 16% shop, refunded one Milk (1.250 + 0.200 tax = 1.450, correct at the till), then read the profit-and-loss report and the trial balance.",
                        f"P&L revenue (ex-tax) = {sub_sum} - {self.refund_net} = {exp_rev_ex_tax}; Tax Payable = tax charged {tax_sum} - tax refunded {self.refund_tax} = {tax_pay_exp} (the 'tax collected' report already says {summ['tax_collected']}).",
                        f"P&L revenue = {pnl['revenue']} (gross_revenue {pnl['gross_revenue']} minus sales_returns {pnl.get('sales_returns')}, the tax-inclusive 1.450); Tax Payable in the ledger = {tp and tp['balance']}. Net profit is understated by the refunded tax and the tax owed to the authority is overstated by it - and the 'tax collected' report disagrees with the ledger.",
                        "", "products/retail/backend/core/retail/general_ledger.py:1791-1838 (post_return: Cr Cash/AR, Dr Sales Returns for the settled total, no Dr Tax Payable); surfaces in core/retail/financial_statements.py:467-497 (profit_and_loss)")
        rec.step("E3-tb-ap", "E3 ledger Accounts Payable = supplier owed (before E4 payment)", ap and near(ap["balance"], PURCHASE_TOTAL), PURCHASE_TOTAL, ap and ap["balance"])
        # supplier E4
        pay = A.data("GET", R + "/suppliers/payables")
        rec.step("E3-supplier", "E3 supplier balance = the credit purchase", near(pay["total_payable"], PURCHASE_TOTAL), PURCHASE_TOTAL, pay["total_payable"])
        s, b = A.call("POST", R + f"/suppliers/{self.supp_id}/payments", {"amount": 50, "method": "bank"})
        newb = D(str(b["data"]["new_balance"])) if s == 200 else None
        rec.step("E4a", "E4 pay supplier 50.000: AP = 142.000 - 50.000", s == 200 and newb == PURCHASE_TOTAL - D(50), PURCHASE_TOTAL - D(50), newb if s == 200 else f"{s} {b}")
        pay2 = A.data("GET", R + "/suppliers/payables")
        rec.step("E4b", "E4 payables list updates", near(pay2["total_payable"], PURCHASE_TOTAL - 50), PURCHASE_TOTAL - 50, pay2["total_payable"])
        ss = A.data("GET", R + f"/suppliers/{self.supp_id}/statement")["data"]
        ev = [(e["kind"], D(str(e["amount"]))) for e in ss["events"]]
        rec.step("E4c", "E4 supplier statement: charge 142.000 then payment 50.000", ev == [("charge", PURCHASE_TOTAL), ("payment", D(50))], "[charge 142, payment 50]", ev)
        tb2 = A.data("GET", R + "/reports/trial-balance")["data"]
        ap2 = next(a for a in tb2["accounts"] if a["name"] == "Accounts Payable")
        rec.step("E4d", "E4 ledger Accounts Payable follows the payment", near(ap2["balance"], PURCHASE_TOTAL - 50), PURCHASE_TOTAL - 50, ap2["balance"])
        # receipt of what each till shows in money format
        self.sales_check_actuals(recent)
        # every receipt / X-report text read during the day: no '$', no USD, no 2-decimal money
        dollar_hits = [(w, m) for w, t in SEEN_TEXTS for m in re.findall(r"\$|USD", t)]
        two_dec = [(w, m) for w, t in SEEN_TEXTS if w != "Arabic" for m in re.findall(r"(?<![\d.])\d[\d,]*\.\d{2}(?!\d)", t)]
        rec.step("L-receipts-currency", f"every receipt/X-report read today ({len(SEEN_TEXTS)} texts): no '$'/USD and no 2-decimal amounts",
                 not dollar_hits and not two_dec, "none", f"dollar={dollar_hits[:3]} two-decimal={two_dec[:5]}")
        if dollar_hits or two_dec:
            rec.finding("MONEY-WRONG" if two_dec else "LOOKS-BROKEN", "Dollar sign or 2-decimal amount on a JOD receipt/report",
                        "Scanned every sale-complete dialog and the X report.", "JD amounts, 3 decimals.", f"{dollar_hits[:3]} {two_dec[:5]}")
        rec.flush()

    def sales_check_actuals(self, recent):
        """Per-sale: the app's stored total/tax against the independent expectation."""
        rec = self.rec
        rows = sorted(recent, key=lambda r: r["id"])
        # the sale ids are not in ring order for S4 (second browser) etc.; match by multiset of totals per tender
        exp = sorted(((s["method"], s["exp"]["total"], s["exp"]["total_alt"]) for s in self.sales), key=lambda t: (t[0], t[1]))
        act = sorted(((r["payment_method"], D(str(r["total"]))) for r in rows), key=lambda t: (t[0], t[1]))
        ok = len(exp) == len(act) and all(a[0] == e[0] and a[1] in (e[1], e[2]) for a, e in zip(act, exp))
        rec.step("E3-each-sale", "E3 every sale total (per tender, sorted) matches the independent arithmetic", ok, [(m, str(t)) for m, t, _ in exp], [(m, str(t)) for m, t in act])
        # tax-convention note
        diff = [s["id"] for s in self.sales if s["exp"]["total"] != s["exp"]["total_alt"]]
        if diff:
            rec.meta["tax_rounding_ambiguous_sales"] = diff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--keep-data", action="store_true", help="(kept anyway: the data dir is evidence)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    data_dir = out / f"data_{stamp}"
    rec = Rec(out)
    rec.meta.update(started=stamp, port=PORT, data_dir=str(data_dir))
    rec.meta["scope_notes"] = [
        "Selling S1-S9, S12-drawer keys, S13, the sign-out hand-over, the X report and the close (E1/E2) were driven in the real UI with the keyboard.",
        "Done over the HTTP API, NOT through screens: M2-M4 (staff, catalogue, purchase), S10 (return), S11 (customer payment), the owner's approve (E2), E3 report reads and E4 supplier payment. Their screens were not looked at.",
        "S3 (split tender) cannot be performed: the till has no split tender.",
    ]
    be = Backend(data_dir)
    rc = 0
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is missing - run with the SYSTEM Python (see module docstring)")
        return 2
    try:
        be.start()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                day = Day(rec, be, pw, browser)
                phases = [day.morning, day.login_rana, day.selling, day.returns_and_payments, day.paid_out,
                          day.handover, day.looks, day.arabic, day.evening, day.reports]
                for ph in phases:
                    try:
                        ph()
                    except Exception as e:
                        tb = traceback.format_exc()
                        print(tb)
                        rec.step("PHASE-" + ph.__name__, f"phase {ph.__name__} aborted", False, "completes", f"{type(e).__name__}: {e}"[:300])
                        try:
                            pg = getattr(day, "rana", None) or getattr(day, "owner_page", None)
                            if pg:
                                rec.shot(pg, "ABORT-" + ph.__name__)
                        except Exception:
                            pass
                        rc = 1
                        # a failed phase must not hide the later ones that don't depend on it
                    rec.flush()
            finally:
                browser.close()
    finally:
        be.stop()
        ce = sorted({c[:170] for c in rec.console if "401" not in c})
        if ce:
            rec.finding("MINOR", "Browser console errors during the day (other than the expected 401s)",
                        "Collected every console error and uncaught page error from every browser page.", "None.",
                        " || ".join(ce), "",
                        "products/retail/frontend/subsystem-retail.js:3055 (_renderDashboard writes textContent on a node that is gone when the screen is replaced mid-render). The 'null/pos: The null module is not available' entry is probably this script calling SubsystemApp._navigate('pos') in the instant after a sign-in, before the shell has set its active module.")
        rec.meta["finished"] = time.strftime("%Y%m%d_%H%M%S")
        rec.flush()
    p = sum(1 for s in rec.steps if s["status"] == "PASS")
    f = sum(1 for s in rec.steps if s["status"] == "FAIL")
    print(f"\nDONE: {p} PASS, {f} FAIL, {len(rec.findings)} findings -> {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
