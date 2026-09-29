"""
retail_p1b_palette_fix_e2e.py -- Phase 1b crew P, the adversarial verifier's fix
round (F1-F9), proved in a REAL browser against a REAL backend.
STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1b_palette_fix_e2e.py [--only tag,tag]

Tags (one guard each, so a mutation proof can re-run it alone):

  money    F1. Ctrl+K, type, Enter/Esc/click while a REAL 2-item cart sits in the
           till. The sales table (sqlite, counted directly) must not gain a row
           and the cart must be intact. A control step proves the same fixture
           DOES charge when the palette is not involved (failure shape 2: a
           fixture that cannot charge would pass forever).
  bar      F2. A licence bar (the real SubsystemApp._renderLicenseBanner) must not
           cover the palette's search field, at 390x844 and at 1366.
  find     F3. A customer phone (with and without the trunk zero) and a product
           SKU / barcode find their live row.
  manager  F4/M1. A MANAGER has no Audit Log in the sidebar or the palette.
  recent   F4/M2. Recents are per user: a cashier sees none of the owner's.
  escape   F4/M3. A product named <img src=x onerror=...> is inert in the list.
  cashier  F5/M5. A cashier's palette has no "Record customer payment".
  picker   F7. Ctrl+K over the open Theme picker does nothing; key hints stay
           untranslated (English "Esc" / "Enter" / "Ctrl K" in an Arabic UI).
  storage  F9. localStorage that THROWS from the first line: the login page and
           the signed-in shell still render.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    DEFAULT_WAIT_MS, VIEWPORT, Backend, _api_login, _owner_company_id, _seed_cashier,
    assert_reachable, human_click, probe,
)
from retail_p1b_palette_e2e import (  # noqa: E402
    check, close_with_esc, open_with_ctrl_k, palette_open, pick_port, row_keys, set_lang,
)
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@palettefix.test"
OWNER_PASSWORD = "PaletteFixOwner_123!"
CASHIER_EMAIL = "sara@palettefix.test"
CASHIER_PASSWORD = "PaletteFixCashier_123!"
MANAGER_EMAIL = "mgr@palettefix.test"
MANAGER_PASSWORD = "PaletteFixManager_123!"
PHONE = {"width": 390, "height": 844}
XSS_NAME = "<img src=x onerror=window.__p=1>"
# Both stores throw, as they do together when a browser blocks site data:
# sessionStorage's demo-mode reads in app-shell.js (boot, auth, activation
# gate) were unguarded too, and a test that blocks only localStorage cannot
# see them (orchestrator, after crew P's out-of-scope note).
THROW_ALL = ("for (const k of ['localStorage', 'sessionStorage']) { "
             "Object.defineProperty(window, k, { configurable: true, "
             "get() { throw new DOMException('The operation is insecure.', 'SecurityError'); } }); }")


def open_page(browser, base, *, email=None, password=None, **kw):
    """The palette suite's open_page defaults to ITS owner; this suite has its own."""
    return _open_page(browser, base, email=email or OWNER_EMAIL, password=password or OWNER_PASSWORD, **kw)


def seed_manager(data_dir: Path, company_id: str, email: str, password: str) -> None:
    """_seed_cashier hard-codes role 'cashier'; this is the same registry INSERT
    with role 'manager' (capabilities seeded for that role the way signup does)."""
    os.environ["AURA_APP_DATA"] = str(data_dir)
    from commercial_runtime.identity import user_accounts
    from commercial_runtime.identity.registry_db import get_conn
    from commercial_runtime.security.passwords import hash_password
    uid = str(uuid.uuid4())
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, branch_scope_uid) VALUES (?,?,?,?,?,?,?,0,?)",
            (uid, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), "manager", "active", None))
        conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                     (str(uuid.uuid4()), uid, "retail", "full"))
        user_accounts.seed_capabilities_for_user(conn, uid, "manager")
        conn.commit()
    finally:
        conn.close()


def sales_count(data_dir: Path) -> int:
    conn = sqlite3.connect(str(data_dir / "database" / "subsystems" / "retail.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    finally:
        conn.close()


def api(page, method: str, path: str, body=None):
    m = re.match(r"(https?://[^/]+)", page.url)
    fn = getattr(page.request, method.lower())
    resp = fn(m.group(1) + path, data=body) if body is not None else fn(m.group(1) + path)
    return resp.status, resp.json()


def type_and_settle(page, text: str, wait: int = 700) -> None:
    page.keyboard.type(text)
    page.wait_for_timeout(wait)


def fill_query_and_wait(page, text: str, wait: int = 1300) -> list:
    """Real typing (not fill()) so the same key events a person makes are sent;
    live product/customer rows arrive after a debounce + a fetch."""
    page.evaluate("() => { const i = document.getElementById('aura-palette-input'); i.value = ''; i.dispatchEvent(new Event('input', {bubbles: true})); }")
    page.keyboard.type(text)
    page.wait_for_timeout(wait)
    return row_keys(page)


# --------------------------------------------------------------------------- F1
def money_scenario(browser, base, data_dir, email, password, typed, how, expect_section):
    ctx, page = open_page(browser, base, email=email, password=password)
    try:
        page.evaluate("() => SubsystemApp._navigate('pos')")
        page.locator(".pos-card").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(800)
        human_click(page, ".pos-card", "Widget tile", text="Widget")
        page.wait_for_timeout(300)
        human_click(page, ".pos-card", "Gadget tile", text="Gadget")
        page.wait_for_timeout(400)
        cart_before = page.evaluate("() => RetailSystem._cart.length")
        assert cart_before == 2, f"fixture: expected 2 distinct lines in the cart, got {cart_before}"
        sales_before = sales_count(data_dir)
        page.keyboard.press("Control+k")
        page.locator("#aura-palette-input").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.keyboard.type(typed)
        page.wait_for_timeout(600)
        keys = row_keys(page)
        assert keys, f"typing {typed!r} found no row at all"
        if how == "enter":
            page.keyboard.press("Enter")
        elif how == "esc":
            page.keyboard.press("Escape")
        else:
            human_click(page, ".aura-palette-row", "first palette row")
        page.wait_for_timeout(2500)
        st = page.evaluate("""() => ({section: SubsystemApp.currentSection, cart: RetailSystem._cart.length,
            receipt: !!document.getElementById('ret-receipt-dialog'), open: AuraPalette.isOpen()})""")
        sales_after = sales_count(data_dir)
        who = email.split("@")[0]
        assert sales_after == sales_before, (
            f"[{who}/{how}] the palette CHARGED a sale: sales rows {sales_before} -> {sales_after} "
            f"(receipt dialog={st['receipt']}, section={st['section']})")
        assert not st["receipt"], f"[{who}/{how}] a receipt dialog appeared -- a sale was rung"
        assert st["cart"] == 2, f"[{who}/{how}] cart changed: 2 -> {st['cart']}"
        assert not st["open"], f"[{who}/{how}] palette still open after {how}"
        assert st["section"] == expect_section, f"[{who}/{how}] landed on {st['section']!r}, wanted {expect_section!r}"
    finally:
        ctx.close()


def money_control(browser, base, data_dir):
    """The fixture must be ABLE to charge: same cart, no palette, plain Enter on the
    till. Without this, 'sales did not change' could just mean the till was never able
    to sell (failure shape 2)."""
    ctx, page = open_page(browser, base)
    try:
        page.evaluate("() => SubsystemApp._navigate('pos')")
        page.locator(".pos-card").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(800)
        human_click(page, ".pos-card", "Widget tile", text="Widget")
        page.wait_for_timeout(300)
        human_click(page, ".pos-card", "Gadget tile", text="Gadget")
        page.wait_for_timeout(400)
        before = sales_count(data_dir)
        page.evaluate("() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); }")
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)
        after = sales_count(data_dir)
        assert after == before + 1, (
            f"control: plain Enter on a 2-item cart did NOT charge a sale ({before} -> {after}); "
            "the F1 assertions above would prove nothing")
    finally:
        ctx.close()


# --------------------------------------------------------------------------- F2
BAR_JS = """() => {
  const b = document.getElementById('aura-license-banner');
  const i = document.getElementById('aura-palette-input');
  const o = document.querySelector('.aura-palette-overlay');
  if (!b || !i || !o) return {banner: !!b, input: !!i, overlay: !!o};
  const br = b.getBoundingClientRect(), ir = i.getBoundingClientRect();
  const hitIn = document.elementFromPoint(ir.left + ir.width / 2, ir.top + ir.height / 2);
  const hitBar = document.elementFromPoint(br.left + br.width / 2, br.top + br.height / 2);
  return {banner: true, input: true, overlay: true, barTop: br.top, barBottom: br.bottom, barH: br.height,
          inputTop: ir.top, inputBottom: ir.bottom,
          inputHit: hitIn === i, inputHitBar: !!(hitIn && b.contains(hitIn)),
          barCoveredByScrim: !!(hitBar && o.contains(hitBar)),
          barZ: getComputedStyle(b).zIndex, overlayZ: getComputedStyle(o).zIndex};
}"""


def bar_case(browser, base, viewport):
    ctx, page = open_page(browser, base, email=CASHIER_EMAIL, password=CASHIER_PASSWORD, viewport=viewport)
    try:
        # The REAL renderer, called in the page with a real blocked state (the
        # licensed fixture never reaches one on its own). Called right before
        # measuring because the shell re-renders it from live state on its own poll.
        page.evaluate("() => SubsystemApp._renderLicenseBanner('EXPIRED')")
        page.locator("#aura-license-banner").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.keyboard.press("Control+k")
        page.locator("#aura-palette-input").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(300)
        m = page.evaluate(BAR_JS)
        assert m.get("banner") and m.get("input"), f"fixture: banner/input missing: {m}"
        assert m["inputHit"], (
            f"{viewport['width']}px: the palette search field is NOT what a person hits at its centre "
            f"(covered by the licence bar: {m['inputHitBar']}; bar z={m['barZ']}, overlay z={m['overlayZ']}, "
            f"bar {m['barTop']:.0f}..{m['barBottom']:.0f}, input {m['inputTop']:.0f}..{m['inputBottom']:.0f})")
        assert_reachable(page, "#aura-palette-input", f"palette field at {viewport['width']}px")
        assert m["barCoveredByScrim"], (
            f"{viewport['width']}px: the licence bar sits ABOVE the scrim ({m}) -- the page behind is dimmed but the bar is not")
        # a person types into it for real
        page.keyboard.type("prod")
        assert page.evaluate("() => document.getElementById('aura-palette-input').value") == "prod"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- main
def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_p1b_palfix_"))
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
            p0.fill("#su-company", "Palette Fix Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()
            cid = _owner_company_id(data_dir)
            _seed_cashier(data_dir, cid, CASHIER_EMAIL, CASHIER_PASSWORD)
            seed_manager(data_dir, cid, MANAGER_EMAIL, MANAGER_PASSWORD)

            # ---- data: two sellable products, an inert-name product, a customer
            oc, op = open_page(browser, base)
            ids = {}
            for key, body in (
                ("widget", {"name": "Widget", "sku": "W1", "sell_price": 5, "cost_price": 3, "unit": "pc"}),
                ("gadget", {"name": "Gadget", "sku": "SKU-4471", "barcode": "0123456789012", "sell_price": 7, "cost_price": 4, "unit": "pc"}),
                ("xss", {"name": XSS_NAME, "sku": "XSS1", "sell_price": 9, "cost_price": 6, "unit": "pc"}),
            ):
                s, b = api(op, "POST", "/api/sub/retail/products", body)
                assert s in (200, 201), f"fixture: product {key} -> {s} {b}"
                ids[key] = b["data"]["id"]
                api(op, "POST", f"/api/sub/retail/products/{ids[key]}/stock-adjust", {"quantity": 100, "reason": "seed"})
            s, b = api(op, "POST", "/api/sub/retail/customers", {"name": "Debtor Ali", "phone": "0791112233"})
            assert s in (200, 201), f"fixture: customer -> {s} {b}"
            s, b = api(op, "POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 10})
            assert s in (200, 201), f"fixture: cash session -> {s} {b}"
            oc.close()

            # =================================================================
            # F1 -- the money one
            # =================================================================
            if wanted("money"):
                for who, email, pw, typed, section in (
                    ("owner", OWNER_EMAIL, OWNER_PASSWORD, "reports", "reports"),
                    ("cashier", CASHIER_EMAIL, CASHIER_PASSWORD, "products", "products"),
                ):
                    for how, expect in (("enter", section), ("click", section), ("esc", "pos")):
                        check("money", f"{who}: {how} in the palette with a 2-item cart charges nothing",
                              lambda e=email, p=pw, t=typed, h=how, x=expect:
                              money_scenario(browser, base, data_dir, e, p, t, h, x))
                # control LAST: it really charges a sale, so it must not precede the rest.
                check("money", "control: the same cart WITHOUT the palette does charge on Enter",
                      lambda: money_control(browser, base, data_dir))

            # =================================================================
            # F2 -- licence bar vs palette
            # =================================================================
            if wanted("bar"):
                check("bar", "phone 390x844: licence bar does not cover the search field",
                      lambda: bar_case(browser, base, PHONE))
                check("bar", "desktop 1366: licence bar does not cover the field and the scrim dims it",
                      lambda: bar_case(browser, base, VIEWPORT))

            # =================================================================
            # F3 -- phone / SKU / barcode
            # =================================================================
            if wanted("find"):
                ctx, pg = open_page(browser, base)
                try:
                    def finds(q, prefix):
                        open_with_ctrl_k(pg)
                        keys = fill_query_and_wait(pg, q)
                        pg.keyboard.press("Escape")
                        pg.wait_for_timeout(200)
                        assert any(k.startswith(prefix) for k in keys), f"typing {q!r} found no {prefix}* row, got {keys}"

                    for q in ("0791112233", "791112233", "1112233"):
                        check("find", f"customer phone {q!r} finds the customer",
                              lambda q=q: finds(q, "customer:"))
                    for q in ("4471", "sku-4471", "0123456789012", "123456789012"):
                        check("find", f"product SKU/barcode {q!r} finds the product",
                              lambda q=q: finds(q, "product:"))
                    def zeros_are_not_a_wildcard():
                        open_with_ctrl_k(pg)
                        fill_query_and_wait(pg, "000")
                        _assert_no_rows(pg, "customer:")
                        pg.keyboard.press("Escape")
                    check("find", "typing only zeros does not match every customer", zeros_are_not_a_wildcard)
                finally:
                    ctx.close()

            # =================================================================
            # F4/M1 -- manager has no audit log
            # =================================================================
            if wanted("manager"):
                def manager_no_audit():
                    ctx_m, pm = open_page(browser, base, email=MANAGER_EMAIL, password=MANAGER_PASSWORD)
                    try:
                        role = pm.evaluate("() => SubsystemApp.role")
                        assert role == "manager", f"fixture: signed in as {role!r}, not a manager"
                        assert pm.locator('[data-section="audit-log"]').count() == 0, "manager's sidebar has an Audit Log row"
                        keys = pm.evaluate("() => AuraPalette.entries().map(e => e.key)")
                        assert "page:audit-log" not in keys, "manager's palette offers Audit Log"
                        open_with_ctrl_k(pm)
                        found = fill_query_and_wait(pm, "audit")
                        assert "page:audit-log" not in found, f"typing 'audit' shows Audit Log to a manager: {found}"
                    finally:
                        ctx_m.close()

                def owner_has_audit():
                    ctx_o, po = open_page(browser, base)
                    try:
                        assert po.locator('[data-section="audit-log"]').count() >= 1, "owner control: no Audit Log row"
                        assert "page:audit-log" in po.evaluate("() => AuraPalette.entries().map(e => e.key)"), "owner control: palette lacks Audit Log"
                    finally:
                        ctx_o.close()

                check("manager", "control: the owner has Audit Log in sidebar and palette", owner_has_audit)
                check("manager", "a manager has no Audit Log in the sidebar or the palette", manager_no_audit)

            # =================================================================
            # F4/M2 -- recents are per user
            # =================================================================
            if wanted("recent"):
                def recents_per_user():
                    ctx_r, pg = open_page(browser, base)
                    try:
                        open_with_ctrl_k(pg)
                        fill_query_and_wait(pg, "products", wait=600)
                        pg.keyboard.press("Enter")
                        pg.wait_for_timeout(1500)
                        assert pg.evaluate("() => SubsystemApp.currentSection") == "products", "fixture: owner did not reach Products"
                        # Only that something was stored: HOW it is keyed is what the
                        # cashier assertion below tests by behaviour, not by key name.
                        stored = pg.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('aura_palette_recent') && localStorage.getItem(k).includes('page:products'))")
                        assert stored, "fixture: choosing Products stored no recent"
                        # control: the owner, after a full reload, sees the Recent group
                        pg.goto(base, wait_until="domcontentloaded")
                        pg.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                        pg.wait_for_timeout(600)
                        open_with_ctrl_k(pg)
                        groups = pg.evaluate("() => [...document.querySelectorAll('.aura-palette-group')].map(g => g.textContent.trim())")
                        assert any("Recent" in g for g in groups), f"control: owner sees no Recent group: {groups}"
                        close_with_esc(pg)
                        # the cashier signs in on the SAME browser (same localStorage)
                        _api_login(pg, CASHIER_EMAIL, CASHIER_PASSWORD)
                        pg.goto(base, wait_until="domcontentloaded")
                        pg.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                        pg.wait_for_timeout(700)
                        set_lang(pg, "en")
                        assert pg.evaluate("() => SubsystemApp.role") == "cashier", "fixture: second sign-in was not the cashier"
                        open_with_ctrl_k(pg)
                        groups = pg.evaluate("() => [...document.querySelectorAll('.aura-palette-group')].map(g => g.textContent.trim())")
                        assert not any("Recent" in g for g in groups), f"the cashier sees the owner's recents: {groups}"
                    finally:
                        ctx_r.close()
                check("recent", "a cashier on the owner's browser sees none of the owner's recents", recents_per_user)

            # =================================================================
            # F4/M3 -- an HTML-looking product name is inert
            # =================================================================
            if wanted("escape"):
                def name_is_inert():
                    ctx_e, pg = open_page(browser, base)
                    try:
                        open_with_ctrl_k(pg)
                        keys = fill_query_and_wait(pg, "onerror", wait=1500)
                        assert f"product:{ids['xss']}" in keys, f"fixture: the <img> product row never appeared: {keys}"
                        st = pg.evaluate("() => ({p: window.__p === undefined ? null : window.__p, imgs: document.querySelectorAll('#aura-palette-list img').length, html: document.getElementById('aura-palette-list').innerHTML})")
                        assert st["p"] is None, "the product name RAN: window.__p was set by its onerror"
                        assert st["imgs"] == 0, f"an <img> element was injected into the list ({st['imgs']})"
                        assert "&lt;img" in st["html"], "the name is not shown as escaped text"
                    finally:
                        ctx_e.close()
                check("escape", "a product named <img onerror=...> is shown as text and runs nothing", name_is_inert)

            # =================================================================
            # F5/M5 -- cashier has no Record customer payment
            # =================================================================
            if wanted("cashier"):
                def receivables_gate():
                    ctx_o, po = open_page(browser, base)
                    try:
                        assert "action:customer-payment" in po.evaluate("() => AuraPalette.entries().map(e => e.key)"), \
                            "control: the owner lacks Record customer payment"
                    finally:
                        ctx_o.close()
                    ctx_c, pc = open_page(browser, base, email=CASHIER_EMAIL, password=CASHIER_PASSWORD)
                    try:
                        assert pc.locator('[data-section="receivables"]').count() == 0, \
                            "fixture: the cashier's sidebar shows Receivables, so the gate proves nothing"
                        assert "action:customer-payment" not in pc.evaluate("() => AuraPalette.entries().map(e => e.key)"), \
                            "the cashier's palette offers Record customer payment"
                        open_with_ctrl_k(pc)
                        found = fill_query_and_wait(pc, "customer payment")
                        assert "action:customer-payment" not in found, f"typing it still finds the action: {found}"
                    finally:
                        ctx_c.close()
                check("cashier", "a cashier's palette has no Record customer payment (owner's has)", receivables_gate)

            # =================================================================
            # F7 -- theme picker + key hints
            # =================================================================
            if wanted("picker"):
                def over_picker():
                    ctx_p, pg = open_page(browser, base)
                    try:
                        pg.evaluate("() => ThemeEngine.openPicker()")
                        pg.locator("#theme-picker-panel").wait_for(state="attached", timeout=DEFAULT_WAIT_MS)
                        pg.wait_for_timeout(400)
                        pg.keyboard.press("Control+k")
                        pg.wait_for_timeout(500)
                        assert not palette_open(pg), "Ctrl+K opened the palette on top of the Theme picker"
                        assert pg.evaluate("() => !!document.getElementById('theme-picker-panel')"), "the picker was torn down by Ctrl+K"
                        assert pg.evaluate("() => document.querySelectorAll('.aura-palette-overlay').length") == 0, "a palette overlay was left behind"
                    finally:
                        ctx_p.close()

                HINTS_JS = """() => [...document.querySelectorAll('#aura-palette-dialog kbd, #aura-palette-close, .sub-palette-trigger-key')]
                    .map(e => ({text: e.textContent.trim(), noI18n: !!e.closest('[data-no-i18n]'), translateNo: e.closest('[translate="no"]') !== null}))"""

                def hints_untranslated():
                    ctx_h, pg = open_page(browser, base, lang="en")
                    try:
                        open_with_ctrl_k(pg)
                        en = pg.evaluate(HINTS_JS)
                        close_with_esc(pg)
                        assert len(en) >= 3, f"fixture: too few key hints found: {en}"
                        set_lang(pg, "ar")
                        open_with_ctrl_k(pg)
                        ar = pg.evaluate(HINTS_JS)
                        assert [h["text"] for h in ar] == [h["text"] for h in en], \
                            f"key hints changed with the language: en={[h['text'] for h in en]} ar={[h['text'] for h in ar]}"
                        bad = [h for h in ar if not (h["noI18n"] and h["translateNo"])]
                        assert not bad, f"key hints not marked data-no-i18n + translate=no: {bad}"
                    finally:
                        ctx_h.close()
                check("picker", "Ctrl+K over the Theme picker does not open the palette", over_picker)
                check("picker", "key hints (Esc, Enter, Ctrl K) are marked untranslatable and unchanged in Arabic", hints_untranslated)

            # =================================================================
            # F9 -- storage that throws from the first line
            # =================================================================
            if wanted("storage"):
                def storage_case(logged_in):
                    ctx_s = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
                    try:
                        if logged_in:
                            p_login = ctx_s.new_page()
                            p_login.goto(base, wait_until="domcontentloaded")
                            _api_login(p_login, OWNER_EMAIL, OWNER_PASSWORD)
                            # The ACCOUNT speaks Arabic while the (blocked) cache says
                            # nothing: that is the only state in which the shell's
                            # write-back of the language to localStorage runs at all.
                            p_login.evaluate("""() => fetch('/api/auth/language', {method: 'POST', credentials: 'include',
                                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({language: 'ar'})})""")
                            p_login.close()
                        ctx_s.add_init_script(THROW_ALL)
                        pg = ctx_s.new_page()
                        errs = []
                        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
                        pg.goto(base, wait_until="domcontentloaded")
                        target = "#sub-header-section" if logged_in else "#rl-email"
                        try:
                            pg.locator(target).wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                        except Exception:
                            body = pg.evaluate("() => document.body.innerText.slice(0, 120)")
                            raise AssertionError(f"{target} never rendered with throwing storage; body={body!r}; pageerrors={errs[:3]}")
                        assert not errs, f"uncaught page errors with throwing storage: {errs[:3]}"
                        if logged_in:
                            # init() swallows a throw from the cache write in its own catch,
                            # so the shell STILL renders -- but everything after that line
                            # (currentUser, role, the activation gate) is silently skipped.
                            # The visible page alone cannot tell; the skipped state can.
                            st = pg.evaluate("() => ({role: SubsystemApp.role, lang: AuraI18n.current, "
                                             "user: !!(SubsystemApp.currentUser && SubsystemApp.currentUser.role)})")
                            assert st["role"] and st["user"], f"init() aborted after the language cache write: {st}"
                            assert st["lang"] == "ar", f"account language not applied with blocked storage: {st}"
                            # The human switch: click the language toggle with the store blocked.
                            # setLang() must not abort before apply()/the server write.
                            pg.locator("#aura-lang-toggle").click()
                            pg.wait_for_function("() => document.documentElement.getAttribute('lang') === 'en'", timeout=DEFAULT_WAIT_MS)
                            pg.wait_for_timeout(400)
                            assert not errs, f"switching language with blocked storage threw: {errs[:3]}"
                    finally:
                        ctx_s.close()
                check("storage", "throwing localStorage: the login page renders", lambda: storage_case(False))
                check("storage", "throwing localStorage: the signed-in shell renders", lambda: storage_case(True))
        finally:
            backend.stop()
            browser.close()

    failed = [r for r in _pe.RESULTS if not r[1]]
    print(f"\nRESULTS: {len(_pe.RESULTS) - len(failed)}/{len(_pe.RESULTS)} passed", flush=True)
    for name, ok, msg in failed:
        print(f"  FAIL {name}: {msg}", flush=True)
    return 1 if failed else 0


def _assert_no_rows(page, prefix):
    keys = row_keys(page)
    assert not any(k.startswith(prefix) for k in keys), f"'000' matched {prefix}* rows: {keys}"


if __name__ == "__main__":
    sys.exit(main())
