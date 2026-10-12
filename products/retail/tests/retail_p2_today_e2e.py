"""P2 Track B -- Aura Today (the owner's home), in a real browser.

Boots the REAL Flask backend in-process on a fresh temp AURA_APP_DATA (the same
recipe retail_p1c_e2e_boot.py proved), signs up through the real form, seeds
the shop THROUGH THE PRODUCT'S OWN ROUTES (a few direct sqlite writes only
where a route cannot express the state: back-dating a debt, and putting a closed
drawer into "waiting for approval"), then drives a real headless Chromium at
1366x768 -- the smallest screen this product ships to.

What it asserts, and why each is not vacuous:

  * The server really answered with every card kind the screen is built for
    (cheques, collect, restock, drawers, tax). A missing kind fails BY NAME
    before any screen check, so "the first card is visible" can never be
    satisfied by a screen that quietly drew nothing.
  * For each of English/Arabic x Sand/Night: no page error, no console error,
    no 5xx; the hero figure, the FIRST card's primary button and (when the
    server sent any) the "What Aura did today" disclosure are visible AND
    reachable -- scrolled into the main column and not covered by anything
    (document.elementFromPoint at the element's own centre lands on it);
    nothing overflows horizontally; the page is dir=rtl in Arabic; the chart's
    first day sits on the RIGHT of today in Arabic and on the LEFT in English.
  * Every card's verb goes where it says (receivables, cheques, purchases, the
    drawer history, the e-invoicing page).
  * A cashier gets the cashier landing: Today is not drawn and /reports/today
    is never requested.
  * The nav and header say "Today".

Screenshots (viewport, then full page) go to SHOTS for a human to LOOK at:
a green run does not prove the screen looks right.

Mutation: TODAY_MUTATE=no_delegation serves subsystem-retail.js without the
hand-off to AuraToday (in memory only; the file on disk is never touched). The
run must then go RED.

Run with the SYSTEM Python (Playwright lives there):
    py -3.14 products/retail/tests/retail_p2_today_e2e.py
"""
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SHOTS = Path(os.environ.get("TODAY_SHOTS", r"C:\Users\MSI\.claude\jobs\f8252b48\tmp\today\shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

import retail_p1c_e2e_boot as B  # noqa: E402  (sets AURA_* and boots the backend: import FIRST)

if os.environ.get("TODAY_MUTATE") == "no_delegation":
    B.MUTANTS["today_no_delegation"] = ("return window.AuraToday.render(c, renderToken);", "")
    os.environ["P1C_MUTATE"] = "today_no_delegation"

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

api = B.api
check = B.check
ROUTE = "/api/sub/retail/reports/today"
EXPECTED_KINDS = ["cheques", "collect", "restock", "drawers", "tax"]
PROBLEMS = []   # page errors / console errors / 5xx seen by the owner page


def seed(page):
    ids = B.seed_catalogue(page)   # products, a customer, an open cash shift

    def mk_product(name, sku, price, **extra):
        body = {"name": name, "sku": sku, "sell_price": price, "cost_price": round(price / 2, 3), "unit": "pc"}
        body.update(extra)
        _, r = api(page, "POST", "/api/sub/retail/products", body)
        pid = ((r or {}).get("data") or {}).get("id")
        assert pid, f"product {name} not created: {r}"
        return pid

    # Low stock: 3 on hand, reorder level 10.
    mk_product("Nido Milk Powder", "P2-NIDO", 8, reorder_level=10, initial_stock=3)

    def sale(customer_id, product_id, price, method, qty=1):
        total = price * qty
        _, r = api(page, "POST", "/api/sub/retail/sales", {
            "idempotency_key": f"p2-today-{uuid.uuid4().hex}", "customer_id": customer_id,
            "items": [{"product_id": product_id, "quantity": qty, "unit_price": price, "discount_pct": 0}],
            "subtotal": total, "discount_amount": 0, "tax_amount": 0, "total": total,
            "payment_method": method, "amount_paid": 0 if method == "credit" else total,
        })
        assert r.get("status") == "success", f"sale failed: {r}"
        return r["data"]["sale_number"]

    # Takings today.
    sale(None, ids["widget"], 10, "cash", qty=2)
    sale(None, ids["gadget"], 5, "card", qty=3)

    # Debts older than 30 days (back-dated: no route can date a sale in the past).
    def debtor(name, price, days_ago):
        _, b = api(page, "POST", "/api/sub/retail/customers", {"name": name, "phone": "0791" + str(uuid.uuid4().int)[:6]})
        cid = b["data"]["id"]
        api(page, "PATCH", f"/api/sub/retail/customers/{cid}", {"credit_mode": "unlimited"})
        number = sale(cid, ids["widget"], price, "credit")
        conn = sqlite3.connect(str(B.RETAIL_DB))
        try:
            conn.execute("UPDATE sales SET created_at = datetime('now', ?) WHERE sale_number = ?", (f"-{days_ago} days", number))
            conn.commit()
        finally:
            conn.close()
        return cid

    hala = debtor("Hala Nasser", 70, 40)
    debtor("Rania Khoury", 15, 45)

    # A cheque from a customer, due in two days.
    due = time.strftime("%Y-%m-%d", time.localtime(time.time() + 2 * 86400))
    st, r = api(page, "POST", "/api/sub/retail/cheques", {
        "direction": "in", "party_id": hala, "amount": 120, "cheque_number": "004512",
        "bank_name": "Arab Bank", "due_date": due})
    assert r.get("status") == "success", f"cheque not recorded: {r}"

    # A drawer closed short and waiting for approval. The owner's own close is
    # auto-approved, so the status is put back to 'ended' (= unverified).
    _, cur = api(page, "GET", "/api/sub/retail/cash-sessions/current")
    sid = ((cur or {}).get("data") or {}).get("id")
    assert sid, f"no open cash session to close: {cur}"
    st, r = api(page, "POST", f"/api/sub/retail/cash-sessions/{sid}/close", {"closing_float_counted": 60})
    assert st == 200, f"drawer close failed: {st} {r}"
    conn = sqlite3.connect(str(B.RETAIL_DB))
    try:
        conn.execute("UPDATE cash_sessions SET status = 'ended' WHERE id = ?", (sid,))
        conn.commit()
    finally:
        conn.close()
    return ids


def kinds_from_server(page):
    st, r = api(page, "GET", ROUTE)
    assert st == 200 and r.get("status") == "success", f"{ROUTE} answered {st}: {r}"
    return r["data"]


def reachable(page, locator, what):
    """Visible, in the viewport after scrolling, and nothing painted over it."""
    assert locator.count() >= 1, f"{what}: not in the page at all"
    el = locator.first
    el.scroll_into_view_if_needed(timeout=5000)
    page.wait_for_timeout(250)
    assert el.is_visible(), f"{what}: not visible"
    info = page.evaluate(
        """(node) => {
            const r = node.getBoundingClientRect();
            const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
            const hit = document.elementFromPoint(cx, cy);
            const cs = getComputedStyle(node);
            return { w: r.width, h: r.height, inView: cy >= 0 && cy <= innerHeight && cx >= 0 && cx <= innerWidth,
                     hitsIt: !!hit && (node === hit || node.contains(hit)), display: cs.display, vis: cs.visibility };
        }""", el.element_handle())
    assert info["w"] > 0 and info["h"] > 0, f"{what}: zero size ({info})"
    assert info["display"] != "none" and info["vis"] == "visible", f"{what}: hidden ({info})"
    assert info["inView"], f"{what}: not inside the viewport after scrolling ({info})"
    assert info["hitsIt"], f"{what}: something is painted over it (elementFromPoint missed) ({info})"
    return info


def look(page, lang, theme, had_did):
    B.go(page, "dashboard", 1800)
    page.wait_for_selector(".td-hero", timeout=15000)
    page.wait_for_timeout(600)
    assert page.evaluate("() => document.documentElement.dir") == ("rtl" if lang == "ar" else "ltr"), "wrong document direction"
    big = page.locator(".td-big")
    assert big.count() == 1 and big.inner_text().strip(), "the hero total is missing"
    reachable(page, big, "hero total")
    page.evaluate("() => document.getElementById('sub-content').scrollTo(0, 0)")
    page.screenshot(path=str(SHOTS / f"today_{lang}_{theme}.png"))
    reachable(page, page.locator(".td-cards .td-card .ret-btn-primary"), "first card's primary button")
    if had_did:
        reachable(page, page.locator(".td-did-sum"), "'What Aura did today' summary")
    over = page.evaluate(
        """() => { const c = document.getElementById('sub-content');
                   return { doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                            main: c.scrollWidth - c.clientWidth }; }""")
    assert over["doc"] <= 1 and over["main"] <= 1, f"horizontal overflow at 1366x768: {over}"
    xs = page.evaluate(
        """() => { const bars = [...document.querySelectorAll('.td-chart svg rect')];
                  const now = document.querySelector('.td-chart .td-bar-now');
                  const first = document.querySelector('.td-chart .td-bar');
                  return { n: bars.length, first: first.getBoundingClientRect().left, now: now.getBoundingClientRect().left }; }""")
    assert xs["n"] == 14, f"the chart drew {xs['n']} bars, not 14"
    if lang == "ar":
        assert xs["first"] > xs["now"], f"Arabic chart is not mirrored (first day should be right of today): {xs}"
    else:
        assert xs["first"] < xs["now"], f"English chart is backwards (first day should be left of today): {xs}"
    # The shell scrolls inside #sub-content, so a full-page capture sees one
    # screen. Capture the scrolled screens instead, a viewport apart.
    for i in (1, 2):
        page.evaluate("(i) => document.getElementById('sub-content').scrollTo(0, i * 600)", i)
        page.wait_for_timeout(250)
        page.screenshot(path=str(SHOTS / f"today_{lang}_{theme}_s{i}.png"))
    page.evaluate("() => document.getElementById('sub-content').scrollTo(0, 0)")


def main():
    with sync_playwright() as p:
        browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
        page.on("pageerror", lambda e: PROBLEMS.append(f"pageerror: {e}"))
        page.on("console", lambda m: PROBLEMS.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.on("response", lambda r: PROBLEMS.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)

        seed(page)

        # UI-1 (review A #9): a brand-new shop has never switched e-invoicing on,
        # so the server's tax card says reason 'switched_off'. The owner must see
        # ONE calm line ("optional for now", the checklist's own words), not a
        # "Needs attention" card. Checked in the real browser BEFORE the shop
        # switches it on below -- after that the card is genuinely wanted.
        def fresh_einvoicing_is_quiet():
            d0 = kinds_from_server(page)
            tax0 = [c for c in d0.get("cards", []) if c["kind"] == "tax"]
            assert tax0 and (tax0[0].get("extra") or {}).get("reason") == "switched_off", (
                f"harness: a fresh shop should open the tax card with reason switched_off, got {tax0}")
            B.go(page, "dashboard", 1800)
            page.wait_for_selector(".td-hero", timeout=15000)
            assert page.locator(".td-card[data-kind='tax']").count() == 0, "a switched-off e-invoicing is drawn as a card"
            clear = page.locator(".td-card[data-kind='all_clear']").inner_text()
            assert "optional for now" in clear, f"no calm optional line in All clear: {clear!r}"
        check("a fresh shop (e-invoicing never switched on) gets a calm line, not a tax card", fresh_einvoicing_is_quiet)

        # From here on the shop HAS switched e-invoicing on (regime jordan, no provider wired:
        # reason not_connected), which is the state the card verbs are for.
        st, r = api(page, "POST", "/api/einvoicing/settings", {"tax_regime": "jordan", "enabled": "1"})
        assert st == 200, f"could not switch e-invoicing on for the harness: {st} {r}"
        data = kinds_from_server(page)
        kinds = [c["kind"] for c in data.get("cards", [])]
        print(f"server cards: {kinds}; aura_did: {[a['kind'] for a in data.get('aura_did', [])]}; all_clear: {[a['kind'] for a in data.get('all_clear', [])]}", flush=True)
        had_did = bool(data.get("aura_did"))

        def server_has_every_card():
            missing = [k for k in EXPECTED_KINDS if k not in kinds]
            assert not missing, f"the seeded shop should open these cards, the server did not: {missing} (got {kinds})"
        check("the server opens every card kind the screen is built for", server_has_every_card)

        def header_says_today():
            B.go(page, "dashboard", 1500)
            assert page.inner_text("#sub-header-section").strip() == "Today", page.inner_text("#sub-header-section")
            nav = page.locator(".sub-nav-item, .sub-sidebar a, nav a", has_text="Today")
            assert nav.count() >= 1, "no navigation entry says Today"
            assert page.locator("nav >> text=Dashboard").count() == 0, "the old 'Dashboard' label is still in the navigation"
        check("the header and navigation say Today", header_says_today)

        for lang in ("en", "ar"):
            for theme in ("sand", "night"):
                def one(lang=lang, theme=theme):
                    page.evaluate("(l) => AuraI18n.setLang(l)", lang)
                    page.wait_for_timeout(900)
                    page.evaluate("(t) => ThemeEngine.apply(t)", theme)
                    page.wait_for_timeout(500)
                    before = len(PROBLEMS)
                    look(page, lang, theme, had_did)
                    new = PROBLEMS[before:]
                    assert not new, "errors while on Today: " + " | ".join(new)
                check(f"1366x768 {lang} / {theme}: hero, first verb, did-today reachable; no overflow; chart direction; no errors", one)

        page.evaluate("(l) => AuraI18n.setLang(l)", "en")
        page.evaluate("(t) => ThemeEngine.apply(t)", "sand")
        page.wait_for_timeout(600)

        def verbs():
            page.evaluate("() => { window.__left = null; SubsystemApp._leaveToPage = (u) => { window.__left = u; }; }")
            goes = {"cheques": "cheques", "collect": "receivables", "restock": "purchases"}
            for kind, screen in goes.items():
                B.go(page, "dashboard", 1500)
                page.wait_for_selector(".td-hero", timeout=10000)
                btn = page.locator(f".td-card[data-kind='{kind}'] .ret-btn-primary")
                assert btn.count() == 1, f"card {kind}: expected one primary button, found {btn.count()}"
                btn.click()
                page.wait_for_timeout(900)
                cur = page.evaluate("() => SubsystemApp.currentSection")
                assert cur == screen, f"the {kind} verb went to {cur!r}, not {screen!r}"
            B.go(page, "dashboard", 1500)
            page.locator(".td-card[data-kind='drawers'] .ret-btn-primary").click()
            page.wait_for_selector(".ret-modal-overlay", timeout=8000)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            B.go(page, "dashboard", 1500)
            page.locator(".td-card[data-kind='tax'] .ret-btn-primary").click()
            page.wait_for_timeout(400)
            left = page.evaluate("() => window.__left")
            assert left == "/static/einvoicing.html", f"the tax verb left to {left!r}"
        check("every card's verb goes where it says (cheques, receivables, purchases, drawer history, e-invoicing)", verbs)

        def drawers_and_tax_are_labelled():
            B.go(page, "dashboard", 1500)
            page.wait_for_selector(".td-cards .td-card", timeout=10000)
            labelled = page.evaluate(
                """() => [...document.querySelectorAll('.td-cards .td-card[data-kind]')]
                          .filter(c => c.querySelector('.td-scope')).map(c => c.dataset.kind).sort()""")
            assert labelled == ["drawers", "tax"], f"'On this computer only' is on {labelled}, expected exactly drawers and tax (cards: " + page.evaluate("() => [...document.querySelectorAll('.td-card')].map(c => c.dataset.kind + (c.querySelector('.td-scope') ? '+L' : ''))") + ")"
        check("'On this computer only' is on exactly the drawers and tax cards", drawers_and_tax_are_labelled)

        # A cashier, in a separate browser context (own cookie jar).
        email, pw = "cashier@p2today.local", "CashierPW2t!"
        _, body = api(page, "POST", "/api/admin/employees", {"email": email, "role": "cashier"})
        conn = registry_conn()
        try:
            uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
            conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?", (hash_password(pw), uid))
            conn.commit()
        finally:
            conn.close()

        def cashier_gets_the_landing():
            cctx = browser.new_context(viewport={"width": 1366, "height": 768})
            cp = cctx.new_page()
            seen = []
            cp.on("request", lambda rq: seen.append(rq.url))
            bad = []
            cp.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status in (401, 403) and "/api/auth" not in r.url else None)
            cp.on("pageerror", lambda e: bad.append(f"pageerror {e}"))
            cp.goto(B.BASE)
            B.dismiss_intro_overlay(cp)
            st, _ = api(cp, "POST", "/api/auth/login", {"email": email, "password": pw})
            assert st == 200, f"cashier login failed: {st}"
            cp.goto(B.BASE)
            B.dismiss_intro_overlay(cp)
            cp.wait_for_selector("#sub-header-section", timeout=20000)
            cp.wait_for_timeout(2500)
            cp.screenshot(path=str(SHOTS / "cashier_home.png"))
            assert cp.locator(".td-hero").count() == 0 and cp.locator(".td").count() == 0, "a cashier was shown Today"
            assert not [u for u in seen if "/reports/today" in u], "a cashier's browser requested /reports/today"
            assert cp.inner_text("#sub-content").strip(), "the cashier's landing is empty (anti-vacuity: nothing rendered at all)"
            assert not bad, f"refusals/errors for the cashier: {bad}"
            cctx.close()
        check("a cashier gets the cashier landing: no Today, no /reports/today request, no refusal", cashier_gets_the_landing)

        browser.close()
    B.finish()


if __name__ == "__main__":
    main()
