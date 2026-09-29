"""
retail_p1b_sync_review_e2e.py -- P1b crew Q: the "Sales from other computers that
could not be added here" card, proved in a REAL browser against a REAL backend.
STANDALONE SCRIPT, NOT A PYTEST FILE (same reason as retail_p1a_shell_e2e.py).

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1b_sync_review_e2e.py

What is real: the Flask backend (`app.py`, ports 6230+), the owner's account made
through the first-run form, the retail.db `sync_apply_quarantine` rows (written
straight into the file the backend reads -- the same table the sync engine parks
into; retail_p1b_sync_review_test.py proves those rows come out of the engine),
the shell, the theme, the language switch, and `window.t` from the real catalogue.
What is stubbed: nothing on the happy path. The error state stubs the ROUTE with a
500 (a dead server cannot be produced on demand) and then removes the stub to prove
Try again really refetches. The 403 state is REAL (a cashier signs in).

The Arabic catalogue is not merged yet (the coordinator merges i18n-Q.json), so
the served ar.json is overlaid with i18n-Q.json in a route handler -- the card
goes through the real t(), not a test-side wrapper.

Reachability rule (contract 6b): "can a person see / click it" goes through
assert_reachable / human_click -- box inside the viewport, elementFromPoint at the
centre is the element, then a raw mouse click.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    DEFAULT_WAIT_MS, Backend, _api_login, _owner_company_id, _seed_cashier, assert_reachable, human_click,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = 6230  # crew Q owns 6230-6249
OWNER_EMAIL = "owner@syncreview.test"
OWNER_PASSWORD = "SyncReviewOwner_123!"
CASHIER_EMAIL = f"cash-{uuid.uuid4().hex[:6]}@syncreview.test"
CASHIER_PASSWORD = "SyncReviewCashier_123!"
I18N_Q = Path("C:/Users/MSI/.claude/jobs/c5a4f4c4/tmp/p1b/i18n-Q.json")
SHOTS = Path("C:/Users/MSI/.claude/jobs/c5a4f4c4/tmp/p1b/Q/shots")
VIEWPORT = {"width": 1366, "height": 768}
PHONE = {"width": 375, "height": 812}

TITLE_EN = "Sales from other computers that could not be added here"
EMPTY_EN = "Nothing is waiting. Every sale from your other computers was added."
NEWEST_SALE = "INV-1003"
# 3 sales (12.300 + 45.500 + 100.125) + 3 more parked rows + 50 older fillers.
SALES_TOTAL = 157.925
SALES_COUNT = 3
FILLERS = 50
WAITING = SALES_COUNT + 3 + FILLERS  # 56 -> page 1 is 50 rows, "Show more" loads 6

RESULTS: list[tuple[str, bool, str]] = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}\n       {e}")


# ── data ────────────────────────────────────────────────────────────────────

def _retail_db(data_dir: Path) -> Path:
    return data_dir / "database" / "subsystems" / "retail.db"


def _park(conn, entity_type, reason, payload, ts):
    conn.execute(
        "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail, quarantined_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (f"e2e-{uuid.uuid4().hex[:10]}", entity_type, "create", json.dumps(payload), reason, "raw-detail-never-shown", ts))


def seed_rows(data_dir: Path) -> None:
    conn = sqlite3.connect(_retail_db(data_dir), timeout=30)
    try:
        conn.execute("DELETE FROM sync_apply_quarantine")
        src = "till-9F3A1C7B"
        # newest first: 1003, then a sale item, then a numbering book, ...
        _park(conn, "sale", "duplicate_document_number", {"sale_number": "INV-1003", "total": 100.125, "terminal_id": src}, "2026-09-29 14:05:00")
        _park(conn, "sale_item", "missing_parent:sale", {"line_total": 5.5, "terminal_id": src}, "2026-09-29 14:04:00")
        _park(conn, "sale", "duplicate_document_number", {"sale_number": "INV-1002", "total": 45.5, "terminal_id": src}, "2026-09-29 14:03:00")
        _park(conn, "doc_series", "duplicate_series_code", {"code": "AAA1-P", "terminal_id": src}, "2026-09-29 14:02:00")
        _park(conn, "sale", "duplicate_document_number", {"sale_number": "INV-1001", "total": 12.3, "terminal_id": src}, "2026-09-29 14:01:00")
        _park(conn, "payment", "missing_parent:sale", {"reference": "PAY-1", "amount": 7, "terminal_id": src}, "2026-09-29 14:00:00")
        for i in range(FILLERS):
            _park(conn, "payment", "missing_parent:sale", {"reference": f"PAY-F{i}", "amount": 1, "terminal_id": src},
                  f"2026-09-28 10:{i:02d}:00")
        conn.commit()
    finally:
        conn.close()


def clear_rows(data_dir: Path) -> None:
    conn = sqlite3.connect(_retail_db(data_dir), timeout=30)
    try:
        conn.execute("DELETE FROM sync_apply_quarantine")
        conn.commit()
    finally:
        conn.close()


# ── browser ─────────────────────────────────────────────────────────────────

def open_page(browser, base, *, lang="en", theme="sand", viewport=VIEWPORT, email=OWNER_EMAIL, password=OWNER_PASSWORD):
    ctx = browser.new_context(viewport=viewport, reduced_motion="reduce")
    page = ctx.new_page()
    table = json.loads(I18N_Q.read_text(encoding="utf-8"))

    def _ar(route, _req=None):
        resp = route.fetch()
        body = resp.json()
        body.update(table)
        route.fulfill(response=resp, body=json.dumps(body, ensure_ascii=False),
                      content_type="application/json; charset=utf-8")
    page.route("**/static/locales/ar.json", _ar)
    page.goto(base, wait_until="domcontentloaded")
    _api_login(page, email, password)
    page.evaluate("([l, t]) => { localStorage.setItem('aura_lang', l); localStorage.setItem('aura_theme_v2', t); }", [lang, theme])
    page.goto(base, wait_until="domcontentloaded")
    # attached, not visible: the phone layout hides the sidebar items behind a menu
    page.locator(".sub-nav-item").first.wait_for(state="attached", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(600)
    if page.evaluate("() => AuraI18n.current") != lang:
        # The app resets localStorage's language on boot, so switch the way the
        # language toggle does (same technique as retail_p1a_shell_polish_e2e.py).
        page.evaluate("(l) => AuraI18n.setLang(l)", lang)
        page.wait_for_function("(l) => AuraI18n.current === l", arg=lang, timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(600)
    if lang == "ar":
        page.wait_for_function("document.documentElement.getAttribute('dir') === 'rtl'", timeout=DEFAULT_WAIT_MS)
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == theme, "theme did not take"
    # index.html does not carry the script tag yet (crew P adds it), so load it
    # the way the page will: a plain script tag against the real static route.
    page.add_script_tag(url=f"{base}/static/sync-review.js")
    return ctx, page


MOUNT_JS = """async () => {
  const old = document.getElementById('sr-host'); if (old) old.remove();
  const h = document.createElement('div'); h.id = 'sr-host';
  h.style.cssText = 'position:fixed;inset:0;overflow:auto;z-index:5000;background:var(--surface-app);padding:16px';
  document.body.appendChild(h);
  await window.SyncReview.render(h);
  return true;
}"""

RERENDER_JS = """async () => { await window.SyncReview.render(document.getElementById('sr-host')); return true; }"""


def mount(page):
    page.evaluate(MOUNT_JS)
    page.wait_for_selector("#sr-host .sr-card", timeout=DEFAULT_WAIT_MS)
    # loading -> loaded: wait until the loading line is gone
    page.wait_for_function("() => !document.querySelector('#sr-host .sr-card .sr-muted') || "
                           "document.querySelector('#sr-host .sr-card').innerText.indexOf('…') === -1",
                           timeout=DEFAULT_WAIT_MS)


def card_text(page) -> str:
    return page.evaluate("() => document.querySelector('#sr-host .sr-card').innerText")


def no_sideways(page, what):
    got = page.evaluate("() => { const h = document.getElementById('sr-host');"
                        " return {sw: h.scrollWidth, cw: h.clientWidth, dsw: document.documentElement.scrollWidth,"
                        " dcw: document.documentElement.clientWidth}; }")
    assert got["sw"] <= got["cw"] + 1 and got["dsw"] <= got["dcw"] + 1, f"{what}: sideways scroll {got}"


LATIN_JS = """() => {
  const w = document.createTreeWalker(document.querySelector('#sr-host .sr-card'), NodeFilter.SHOW_TEXT);
  const bad = []; let n;
  while ((n = w.nextNode())) {
    const m = n.nodeValue.match(/[A-Za-z]{3,}/g);
    if (m) bad.push(...m);
  }
  return bad;
}"""
LATIN_ALLOWED = {"INV", "AAA", "PAY", "JD", "Aura", "Action"}


def arabic_table() -> dict:
    """The served Arabic catalogue plus i18n-Q.json (a KeyError here is a real
    finding: a string the card shows has no Arabic anywhere)."""
    base = json.loads((Path(__file__).resolve().parent.parent / "frontend" / "locales" / "ar.json")
                      .read_text(encoding="utf-8"))
    base.update(json.loads(I18N_Q.read_text(encoding="utf-8")))
    return base


def scenario_full(browser, base, lang, theme):
    ctx, page = open_page(browser, base, lang=lang, theme=theme)
    try:
        mount(page)
        table = arabic_table()
        want = lambda s: table[s] if lang == "ar" else s  # noqa: E731
        text = card_text(page)
        tag = f"{lang}/{theme}"

        assert want(TITLE_EN) in text, f"[{tag}] title missing: {text[:200]!r}"
        # count, sales count, total: visible on screen, not merely in the DOM
        for sel, label in ((".sr-stat:nth-child(1) .sr-stat-n", "waiting count"),
                           (".sr-stat:nth-child(2) .sr-stat-n", "sales count"),
                           (".sr-stat:nth-child(3) .sr-stat-n", "total at risk")):
            assert_reachable(page, "#sr-host " + sel, f"{tag} {label}")
        got = page.evaluate("() => [...document.querySelectorAll('#sr-host .sr-stat-n')].map(e => e.textContent)")
        assert got[0] == str(WAITING), f"[{tag}] waiting count {got[0]!r} != {WAITING}"
        assert got[1] == str(SALES_COUNT), f"[{tag}] sales count {got[1]!r} != {SALES_COUNT}"
        expect_money = page.evaluate("(n) => RetailSystem._fmt(n)", SALES_TOTAL)
        assert got[2] == expect_money and "157.925" in got[2], f"[{tag}] total {got[2]!r} != app formatter {expect_money!r}"

        # newest row first, reachable, with number + amount + plain reason
        assert_reachable(page, "#sr-host .sr-item", f"{tag} first row")
        first = page.evaluate("() => document.querySelector('#sr-host .sr-item').innerText")
        assert NEWEST_SALE in first, f"[{tag}] first row is not the newest sale: {first!r}"
        assert "100.125" in first, f"[{tag}] first row has no amount: {first!r}"
        assert want("the same receipt number already exists here") in first, f"[{tag}] reason not in plain words: {first!r}"
        assert want("Needs a person") in first, f"[{tag}] duplicate must say it needs a person: {first!r}"
        second = page.evaluate("() => document.querySelectorAll('#sr-host .sr-item')[1].innerText")
        assert want("May fix itself") in second, f"[{tag}] missing-parent row must say it may fix itself: {second!r}"
        assert want("its sale is missing here") in second, f"[{tag}] {second!r}"
        assert "raw-detail-never-shown" not in text and "duplicate_document_number" not in text, f"[{tag}] raw engine text leaked"

        # guidance: what the owner can do today
        assert want("What you can do today") in text and "Action Aura" in text, f"[{tag}] guidance missing"

        # read-only: the only buttons are paging / retry
        btns = page.evaluate("() => [...document.querySelectorAll('#sr-host .sr-card button')].map(b => b.textContent)")
        assert set(btns) <= {want("Show more"), want("Try again")}, f"[{tag}] card has non-read-only buttons {btns}"

        if lang == "ar":
            assert page.evaluate("() => getComputedStyle(document.getElementById('sr-host')).direction") == "rtl"
            # the stat blocks start at the RIGHT edge under RTL
            box = page.evaluate("() => { const r = document.querySelector('#sr-host .sr-card').getBoundingClientRect();"
                                " const s = document.querySelector('#sr-host .sr-stat').getBoundingClientRect();"
                                " return {cardLeft: r.left, cardRight: r.right, statLeft: s.left, statRight: s.right}; }")
            assert box["cardRight"] - box["statRight"] < box["statLeft"] - box["cardLeft"], f"[{tag}] stats not anchored to the RTL start {box}"
            stray = [w for w in page.evaluate(LATIN_JS) if w not in LATIN_ALLOWED]
            assert not stray, f"[{tag}] untranslated English left on the Arabic card: {sorted(set(stray))}"
        no_sideways(page, tag)

        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"card-{lang}-{theme}.png"))

        # paging: 50 rows, then a real click on Show more brings the last 6
        n1 = page.evaluate("() => document.querySelectorAll('#sr-host .sr-item').length")
        assert n1 == 50, f"[{tag}] page one shows {n1} rows, want 50"
        human_click(page, "#sr-host .sr-btn", f"{tag} Show more", text=want("Show more"), scroll_list=True)
        page.wait_for_function("() => document.querySelectorAll('#sr-host .sr-item').length === 56", timeout=DEFAULT_WAIT_MS)
        assert page.evaluate("() => [...document.querySelectorAll('#sr-host .sr-btn')].length") == 0, f"[{tag}] Show more still there after last page"
        full = card_text(page)
        for num in ("INV-1001", "INV-1002", "INV-1003", "AAA1-P"):
            assert full.count(num) == 1, f"[{tag}] {num} appears {full.count(num)} times across the two pages"
        assert full.count("PAY-F") == FILLERS, f"[{tag}] filler payments lost or repeated across pages"
    finally:
        ctx.close()


def scenario_empty(browser, base, data_dir, lang):
    clear_rows(data_dir)
    ctx, page = open_page(browser, base, lang=lang, theme="sand")
    try:
        mount(page)
        table = arabic_table()
        want = table[EMPTY_EN] if lang == "ar" else EMPTY_EN
        text = card_text(page)
        assert want in text, f"[empty/{lang}] honest empty sentence missing: {text!r}"
        assert page.evaluate("() => document.querySelectorAll('#sr-host .sr-item, #sr-host .sr-stat').length") == 0, "empty state still draws stats/rows"
        assert_reachable(page, "#sr-host .sr-ok", f"empty/{lang} sentence")
        assert page.locator("#sr-host [role=status]").count() == 1
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"empty-{lang}-sand.png"))
    finally:
        ctx.close()
        seed_rows(data_dir)


def scenario_error_then_retry(browser, base):
    ctx, page = open_page(browser, base, lang="en", theme="night")
    try:
        handler = lambda route, _r=None: route.fulfill(status=500, content_type="application/json", body='{"status":"error"}')  # noqa: E731
        page.route("**/api/sub/retail/sync-review*", handler)
        mount(page)
        text = card_text(page)
        assert "could not be loaded" in text, f"[error] honest error sentence missing: {text!r}"
        assert "Nothing is waiting" not in text, "[error] a failed load must NEVER read as 'nothing is waiting'"
        assert page.locator("#sr-host [role=alert]").count() == 1
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "error-en-night.png"))
        page.unroute("**/api/sub/retail/sync-review*", handler)
        human_click(page, "#sr-host .sr-btn", "Try again", text="Try again")
        page.wait_for_function("() => document.querySelectorAll('#sr-host .sr-stat').length === 3", timeout=DEFAULT_WAIT_MS)
        assert "157.925" in card_text(page), "[error] Try again did not load the real data"
    finally:
        ctx.close()


def scenario_cashier_refused(browser, base):
    ctx, page = open_page(browser, base, lang="en", theme="sand", email=CASHIER_EMAIL, password=CASHIER_PASSWORD)
    try:
        mount(page)
        text = card_text(page)
        assert "Only the shop owner can open this." in text, f"[cashier] owner-only sentence missing: {text!r}"
        assert NEWEST_SALE not in text and "157.925" not in text, "[cashier] a cashier saw the owner's money"
        assert page.evaluate("() => document.querySelectorAll('#sr-host .sr-item, #sr-host .sr-stat').length") == 0
        assert page.locator("#sr-host .sr-btn").count() == 0, "[cashier] a refusal should not offer Try again"
    finally:
        ctx.close()


def scenario_phone(browser, base):
    ctx, page = open_page(browser, base, lang="en", theme="sand", viewport=PHONE)
    try:
        mount(page)
        assert_reachable(page, "#sr-host .sr-stat:nth-child(3) .sr-stat-n", "phone total at risk")
        assert_reachable(page, "#sr-host .sr-item", "phone first row")
        no_sideways(page, "phone 375")
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "card-en-sand-phone.png"))
    finally:
        ctx.close()


def main() -> int:
    if SHOTS.exists():
        shutil.rmtree(SHOTS, ignore_errors=True)
    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_syncreview_e2e_"))
    backend = Backend(data_dir)
    backend.port = PORT
    backend.base_url = f"http://127.0.0.1:{PORT}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            c0 = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            p0 = c0.new_page()
            p0.goto(backend.base_url, wait_until="domcontentloaded")
            p0.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            p0.fill("#su-name", "Tareq")
            p0.fill("#su-company", "Sync Review Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()
            _seed_cashier(data_dir, _owner_company_id(data_dir), CASHIER_EMAIL, CASHIER_PASSWORD)
            seed_rows(data_dir)

            for lang in ("en", "ar"):
                for theme in ("sand", "night"):
                    check(f"card: count, sales, JD total, newest-first rows, paging ({lang}, {theme})",
                          lambda l=lang, t=theme: scenario_full(browser, backend.base_url, l, t))
            check("empty state is honest (en)", lambda: scenario_empty(browser, backend.base_url, data_dir, "en"))
            check("empty state is honest (ar)", lambda: scenario_empty(browser, backend.base_url, data_dir, "ar"))
            check("error state is honest and Try again really refetches", lambda: scenario_error_then_retry(browser, backend.base_url))
            check("a cashier gets the owner-only sentence and no money", lambda: scenario_cashier_refused(browser, backend.base_url))
            check("phone 375: total and first row on screen, no sideways scroll", lambda: scenario_phone(browser, backend.base_url))
        finally:
            browser.close()
            backend.stop()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed. Screenshots: {SHOTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
