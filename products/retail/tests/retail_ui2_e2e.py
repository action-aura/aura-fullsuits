"""UI-2 -- favourites, returns from the sale, blind count, customer at the till,
driven in a real browser (EN + AR, 1366x768 + 1024x768).

Boots the REAL Flask backend (retail_p1c_e2e_boot.py's recipe), signs up as the
owner, seeds a catalogue, then drives headless Chromium the way a shop does and
measures what a unit test cannot: that the Favourites page really leads the rail
and really survives a reload, that a long rail really shows its fade + chevron,
that the End Shift dialog really hides the expected figure from a cashier, that a
barcode scanned on the Returns dialog really loads the sale, etc.

Run with the SYSTEM Python (Playwright lives there):
    py -3.14 products/retail/tests/retail_ui2_e2e.py [fav|returns|count|customer ...]
Screenshots go to $AURA_E2E_SHOTS for a human to LOOK at.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

SIZES = [(1366, 768), (1024, 768)]


def shot(page, name):
    page.screenshot(path=str(B.SHOTS / f"{name}.png"))


def set_lang(page, lang):
    page.evaluate("(l) => AuraI18n.setLang(l)", lang)
    page.wait_for_timeout(400)


def set_size(page, w, h):
    page.set_viewport_size({"width": w, "height": h})
    page.wait_for_timeout(250)


def pill_texts(page):
    return page.evaluate("() => [...document.querySelectorAll('#pos-cats .pos-cat-btn')].map(b => b.textContent.trim())")


def active_pill(page):
    return page.evaluate("() => { const b = document.querySelector('#pos-cats .pos-cat-btn.active'); return b ? b.textContent.trim() : null; }")


def tile_names(page):
    return page.evaluate("() => [...document.querySelectorAll('#pos-product-grid .pos-card .pos-card-name')].map(n => n.textContent.trim())")


def toast_text(page):
    return page.evaluate("() => { const t = document.querySelector('.sub-toast, .toast, [role=status]'); return t ? t.textContent.trim() : ''; }")


# ───────────────────────────── item 1: favourites ─────────────────────────────

def item_fav(page, ids):
    # make Gadget LOW (3 left, reorder level defaults to 5) and Gizmo OUT
    B.api(page, "POST", f"/api/sub/retail/products/{ids['gadget']}/stock-adjust", {"quantity": -97, "reason": "count"})
    B.api(page, "POST", f"/api/sub/retail/products/{ids['gizmo']}/stock-adjust", {"quantity": -100, "reason": "count"})
    # twelve extra categories so the rail overflows at 1366 wide
    for i in range(12):
        B.api(page, "POST", "/api/sub/retail/categories", {"name": f"Category number {i + 1}"})
    B.api(page, "POST", "/api/sub/retail/settings/credit", {"pos_favourites": []})

    def before_any_pin():
        B.go_pos(page)
        texts = pill_texts(page)
        assert texts[0] == "All" and not any("Favourites" in t for t in texts), f"rail with no pins: {texts[:3]}"
        assert active_pill(page) == "All", "default page must be All when nothing is pinned"
        html = page.inner_html("#pos-product-grid")
        assert "Stock:" not in html, "a normal tile still prints the stock line"
        low = page.locator(".pos-card", has_text="Gadget").inner_text()
        assert "Low stock: 3" in low, f"low tile: {low!r}"
        out = page.locator(".pos-card", has_text="Gizmo").inner_text()
        assert "Out of stock" in out, f"out tile: {out!r}"
        widget = page.locator(".pos-card", has_text="Widget").inner_text()
        assert "Stock" not in widget, f"a normal tile shows stock: {widget!r}"
    B.check("fav: no pins -> rail starts at All, default All; normal tile quiet, low shows 'Low stock: 3', out shows the red word", before_any_pin)

    def rail_overflow():
        has = page.evaluate("() => document.getElementById('pos-cat-wrap').classList.contains('has-more')")
        vis = page.evaluate("() => { const b = document.getElementById('pos-cat-more'); const r = b.getBoundingClientRect(); return getComputedStyle(b).display !== 'none' && r.width > 0; }")
        assert has and vis, f"a 13-pill rail shows no fade/chevron (has-more={has}, visible={vis})"
        page.click("#pos-cat-more"); page.wait_for_timeout(450)
        scrolled = page.evaluate("() => Math.abs(document.getElementById('pos-cats').scrollLeft)")
        assert scrolled > 50, f"the chevron did not scroll the rail ({scrolled})"
        page.evaluate("() => { const b = document.getElementById('pos-cats'); b.scrollLeft = b.dir === 'rtl' || document.documentElement.dir === 'rtl' ? -b.scrollWidth : b.scrollWidth; }")
        page.wait_for_timeout(300)
        has_end = page.evaluate("() => document.getElementById('pos-cat-wrap').classList.contains('has-more')")
        assert not has_end, "the cue is still on at the end of the rail"
        page.evaluate("() => { document.getElementById('pos-cats').scrollLeft = 0; }")
    B.check("fav: a 13-pill rail shows the fade + chevron, the chevron scrolls, the cue clears at the end", rail_overflow)
    shot(page, "fav_01_rail_overflow_1366")

    def pin_with_star():
        tile = page.locator(".pos-card", has_text="Widget")
        tile.hover()
        star = tile.locator(".pos-card-star")
        assert star.is_visible(), "hovering a tile does not reveal the owner's star"
        shot(page, "fav_02_hover_star_1366")
        star.click(); page.wait_for_timeout(500)
        page.locator(".pos-card", has_text="Doohickey").locator(".pos-card-star").evaluate("e => e.click()")
        page.wait_for_timeout(500)
        texts = pill_texts(page)
        assert texts[0] == "Favourites", f"Favourites should lead the rail: {texts[:3]}"
        assert active_pill(page) == "All", "pinning must not yank the cashier off the page they are on"
        _, s = B.api(page, "GET", "/api/sub/retail/settings/credit")
        assert json.loads(s["data"]["pos_favourites"]) == [ids["widget"], ids["doohickey"]], s["data"].get("pos_favourites")
    B.check("fav: the star pins (no add-to-cart), Favourites leads the rail, server holds the pin order", pin_with_star)

    def right_click_and_key():
        tile = page.locator(".pos-card", has_text="Thingamajig")
        tile.click(button="right"); page.wait_for_timeout(500)
        assert json.loads(B.api(page, "GET", "/api/sub/retail/settings/credit")[1]["data"]["pos_favourites"])[-1] == ids["thingamajig"], "right-click did not pin"
        assert page.evaluate("() => RetailSystem._cart.length") == 0, "a pin gesture added a product to the cart"
        tile.focus(); page.keyboard.press("ContextMenu"); page.wait_for_timeout(500)
        favs = json.loads(B.api(page, "GET", "/api/sub/retail/settings/credit")[1]["data"]["pos_favourites"])
        assert ids["thingamajig"] not in favs, "the context-menu key (keyboard parity) did not unpin"
    B.check("fav: right-click pins, the context-menu key unpins (keyboard parity), neither touches the cart", right_click_and_key)

    def reload_lands_on_favourites():
        page.reload(); B.dismiss_intro_overlay(page)
        page.wait_for_selector("#sub-header-section", timeout=20000)
        B.go_pos(page)
        assert active_pill(page) == "Favourites", f"after a reload the till should open on Favourites, got {active_pill(page)}"
        names = tile_names(page)
        assert names == ["Widget", "Doohickey"], f"Favourites tiles (pin order): {names}"
    B.check("fav: after a full reload the till opens on Favourites showing exactly the pins, in pin order", reload_lands_on_favourites)
    shot(page, "fav_03_favourites_page_1366_en")

    def dialog_checkbox():
        B.go(page, "products")
        page.wait_for_selector("#sub-content table tbody tr, .ret-table tbody tr", timeout=8000)
        page.evaluate("(id) => RetailSystem._openEditProduct(id)", ids["gadget"])
        page.wait_for_selector("#pm-fav", timeout=5000)
        assert not page.is_checked("#pm-fav"), "Gadget is not pinned yet"
        page.check("#pm-fav")
        shot(page, "fav_04_product_dialog_1366")
        page.click("#pm-save-btn"); page.wait_for_timeout(900)
        favs = json.loads(B.api(page, "GET", "/api/sub/retail/settings/credit")[1]["data"]["pos_favourites"])
        assert ids["gadget"] in favs, f"the dialog checkbox did not pin: {favs}"
    B.check("fav: the product edit dialog's checkbox pins the product", dialog_checkbox)

    def cap_message():
        filler = [f"filler-{i}" for i in range(37)]
        favs = json.loads(B.api(page, "GET", "/api/sub/retail/settings/credit")[1]["data"]["pos_favourites"])
        B.api(page, "POST", "/api/sub/retail/settings/credit", {"pos_favourites": (favs + filler)[:40]})
        B.go_pos(page)
        n = len(json.loads(B.api(page, "GET", "/api/sub/retail/settings/credit")[1]["data"]["pos_favourites"]))
        assert n == 40, n
        page.evaluate("() => { RetailSystem._activeCat = null; RetailSystem._renderCatBar(); RetailSystem._renderPOSGrid(); }")
        page.wait_for_timeout(300)
        tile = page.locator(".pos-card", has_text="Thingamajig")
        tile.hover(); tile.locator(".pos-card-star").click(); page.wait_for_timeout(500)
        shot(page, "fav_05_cap_refused_1366")
        txt = page.evaluate("() => document.body.innerText")
        assert "at most 40 favourites" in txt, "the cap refusal message is not on screen"
        n2 = len(json.loads(B.api(page, "GET", "/api/sub/retail/settings/credit")[1]["data"]["pos_favourites"]))
        assert n2 == 40, f"the 41st pin was stored ({n2})"
        B.api(page, "POST", "/api/sub/retail/settings/credit", {"pos_favourites": [ids["widget"], ids["doohickey"], ids["gadget"]]})
    B.check("fav: with 40 pinned the 41st is refused with a visible message and not stored", cap_message)

    for (w, h) in SIZES:
        for lang in ("en", "ar"):
            def sized(w=w, h=h, lang=lang):
                set_size(page, w, h); set_lang(page, lang)
                page.reload(); B.dismiss_intro_overlay(page)
                page.wait_for_selector("#sub-header-section", timeout=20000)
                set_lang(page, lang)
                B.go_pos(page)
                assert active_pill(page) in ("Favourites", "المفضلة"), active_pill(page)
                over = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert over <= 1, f"horizontal overflow {over}"
                shot(page, f"fav_06_till_{w}x{h}_{lang}")
                page.locator(".pos-card").first.hover()
                shot(page, f"fav_07_hover_{w}x{h}_{lang}")
                if lang == "ar":
                    txt = page.inner_text("#pos-cats")
                    assert "المفضلة" in txt, f"Arabic rail: {txt!r}"
            B.check(f"fav: {w}x{h} {lang}: till opens on Favourites, no overflow (screenshots taken)", sized)
    set_size(page, 1366, 768); set_lang(page, "en")


# ───────────────────── shared: ring sales, decode barcodes, cashier ─────────────────────

ZX_LIB = Path(r"C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\ui2\pylib")


def zx_decode(png_bytes):
    """Read barcodes out of a PNG with an INDEPENDENT decoder (ZXing-cpp, installed in
    a scratch dir outside the repo and outside CI). Returns [(format, text)]."""
    sys.path.insert(0, str(ZX_LIB))
    import io
    import numpy as np
    import zxingcpp
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    return [(b.format.name, b.text) for b in zxingcpp.read_barcodes(np.array(img))]


def ring_cash_sale(page, product_ids, qty=1):
    page.evaluate("() => RetailSystem._clearCart()")
    for pid in product_ids:
        for _ in range(qty):
            page.evaluate("(id) => RetailSystem._addToCart(id)", pid)
    page.wait_for_timeout(250)
    page.evaluate("() => RetailSystem._exactCashCharge()")
    page.wait_for_selector("#ret-receipt-dialog", timeout=8000)
    page.wait_for_timeout(250)
    page.evaluate("() => RetailSystem._closeModalOverlay(document.getElementById('ret-receipt-dialog'))")
    page.wait_for_timeout(250)


def make_cashier(email="cashier@ui2.local", password="Ui2CashierPW1!"):
    """A real cashier account in the in-process registry (same recipe as the py tests)."""
    import uuid
    from commercial_runtime.identity import user_accounts
    from commercial_runtime.identity.registry_db import get_conn
    from commercial_runtime.security.passwords import hash_password
    conn = get_conn()
    row = conn.execute("SELECT company_id FROM users WHERE role='admin' LIMIT 1").fetchone()
    company_id = row[0]
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (uid, company_id, "EMP-0099", email, hash_password(password), "cashier", "active"))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), uid, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, uid, "cashier")
    conn.commit(); conn.close()
    return email, password


OWNER = ("owner@p1c.local", "P1cTillTest12345!")
CASHIER = ("cashier@ui2.local", "Ui2CashierPW1!")


def login_as(page, creds):
    status, body = B.api(page, "POST", "/api/auth/login", {"email": creds[0], "password": creds[1]})
    assert status == 200, f"login failed {status} {body}"
    page.reload(); B.dismiss_intro_overlay(page)
    page.wait_for_selector("#sub-header-section", timeout=20000)
    page.wait_for_timeout(900)


def short_number(n):
    import re
    return re.sub(r"-[0-9a-f]{8}-[0-9a-f]{8}$", "", n)


# ───────────────────────── item 2: returns from the sale ─────────────────────────

def item_returns(page, ids):
    B.go_pos(page)
    # three sales; the first rung is the OLDEST
    ring_cash_sale(page, [ids["widget"]])                          # 10.000
    ring_cash_sale(page, [ids["doohickey"], ids["thingamajig"]])   # 15.000
    ring_cash_sale(page, [ids["widget"]], qty=2)                   # 20.000
    _, recent = B.api(page, "GET", "/api/sub/retail/sales/recent?limit=10")
    rows = recent["data"]
    assert len(rows) >= 3, rows
    newest, oldest = rows[0], rows[2]

    def nav_door():
        B.go(page, "dashboard")
        link = page.locator("[onclick*=\"_navigate('sales')\"]").first
        assert link.count() >= 1 and link.is_visible(), "no Sales entry in the sidebar"
        link.click(); page.wait_for_selector("#sh-table tbody tr", timeout=8000)
        n = page.locator("#sh-table tbody tr").count()
        assert n >= 3, f"Sales History shows {n} rows"
        shot(page, "ret_01_sales_screen_1366_en")
    B.check("returns: the Sales door is in the sidebar and opens the Sales History with the sales", nav_door)

    def recent_list():
        B.go(page, "returns")
        page.click("text=Process Return")
        page.wait_for_selector("#ret-recent .ret-recent-row", timeout=8000)
        nums = page.evaluate("() => [...document.querySelectorAll('#ret-recent .ret-recent-row')].map(r => r.dataset.sn)")
        assert nums[0] == newest["sale_number"] and nums[2] == oldest["sale_number"], f"order: {nums[:4]}"
        assert len(nums) <= 30
        shot(page, "ret_02_recent_list_1366_en")
        page.locator("#ret-recent .ret-recent-row").nth(1).click()
        page.wait_for_selector("#ret-sale-items table", timeout=8000)
        header = page.inner_text("#ret-sale-items > div")
        assert "Items from" in header, header
        assert page.evaluate("() => RetailSystem._returnSaleId") == rows[1]["id"]
        assert page.evaluate("() => document.getElementById('ret-recent-wrap').hidden") is True
        shot(page, "ret_03_sale_loaded_1366_en")
    B.check("returns: Recent sales lists newest first; clicking one loads its items", recent_list)

    def scan_into_dialog():
        page.evaluate("() => RetailSystem._closeModalOverlay(document.getElementById('ret-return-modal'))")
        page.click("text=Process Return")
        page.wait_for_selector("#ret-recent .ret-recent-row", timeout=8000)
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        page.keyboard.type(short_number(oldest["sale_number"]), delay=4)
        page.keyboard.press("Enter")
        page.wait_for_selector("#ret-sale-items table", timeout=8000)
        loaded = page.evaluate("() => RetailSystem._returnSaleId")
        assert loaded == oldest["id"], f"the scan (focus off the box) loaded sale {loaded}, wanted {oldest['id']}"
        page.evaluate("() => RetailSystem._closeModalOverlay(document.getElementById('ret-return-modal'))")
        page.click("text=Process Return")
        page.wait_for_selector("#ret-recent .ret-recent-row", timeout=8000)
        page.focus("#ret-sale-search")
        page.keyboard.type(short_number(newest["sale_number"]), delay=4)
        page.keyboard.press("Enter")
        page.wait_for_selector("#ret-sale-items table", timeout=8000)
        assert page.evaluate("() => RetailSystem._returnSaleId") == newest["id"], "the scan (focus on the box) did not load the sale"
        page.evaluate("() => RetailSystem._closeModalOverlay(document.getElementById('ret-return-modal'))")
    B.check("returns: a wedge-scanner burst of the receipt number loads the sale (focus on the box AND off it)", scan_into_dialog)

    def barcodes_scan_back():
        import base64
        number = short_number(newest["sale_number"])
        # (a) the browser-print receipt's inline SVG, rendered by the real browser
        page.evaluate("""(n) => { const d = document.createElement('div'); d.id = 'bc-probe';
            d.style.cssText = 'position:fixed;left:20px;top:20px;background:#fff;padding:20px;z-index:99999';
            d.innerHTML = RetailSystem._receiptBarcodeBlock(n, 80); document.body.appendChild(d); }""", newest["sale_number"])
        png = page.locator("#bc-probe").screenshot()
        (B.SHOTS / "ret_04_print_barcode.png").write_bytes(png)
        got = zx_decode(png)
        assert got == [("Code128", number)], f"browser-print barcode read back as {got}"
        page.evaluate("() => document.getElementById('bc-probe').remove()")
        # (b) the raster receipt: the real server model painted by the real canvas, at both widths
        for width in (576, 384):
            data = page.evaluate("""async ([id, w]) => {
                const r = await fetch('/api/sub/retail/printer/receipt-model?sale_id=' + id + '&width_dots=' + w);
                const j = await r.json();
                const lines = j.data.lines.filter(l => l.kind === 'barcode');
                const out = await ReceiptRaster.render(j.data);
                return { value: lines.length ? lines[0].value : null, png: out.canvas.toDataURL('image/png') }; }""", [newest["id"], width])
            assert data["value"] == number, f"model barcode value {data['value']!r}"
            png = base64.b64decode(data["png"].split(",", 1)[1])
            (B.SHOTS / f"ret_05_raster_receipt_{width}.png").write_bytes(png)
            got = zx_decode(png)
            assert ("Code128", number) in got, f"raster receipt at {width} dots read back as {got}"
    B.check("returns: both receipts' barcodes are read back by an independent decoder (print SVG; raster canvas 80mm + 58mm)", barcodes_scan_back)

    for (w, h) in SIZES:
        for lang in ("en", "ar"):
            def sized(w=w, h=h, lang=lang):
                set_size(page, w, h); set_lang(page, lang)
                B.go(page, "returns")
                page.evaluate("() => RetailSystem._openCreateReturn()")
                page.wait_for_selector("#ret-recent .ret-recent-row", timeout=8000)
                over = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert over <= 1, f"horizontal overflow {over}"
                rows_h = page.evaluate("() => [...document.querySelectorAll('.ret-recent-row')].slice(0, 3).map(x => x.getBoundingClientRect().height)")
                assert rows_h and min(rows_h) >= 44, f"recent rows under the touch floor: {rows_h}"
                shot(page, f"ret_06_dialog_{w}x{h}_{lang}")
                page.evaluate("() => RetailSystem._closeModalOverlay(document.getElementById('ret-return-modal'))")
            B.check(f"returns: {w}x{h} {lang}: dialog renders the recent list, rows >=44px, no overflow (screenshot)", sized)
    set_size(page, 1366, 768); set_lang(page, "en")

    def sales_door_for_roles():
        make_cashier(*CASHIER)
        login_as(page, CASHIER)
        B.go(page, "dashboard")
        assert page.locator("[onclick*=\"_navigate('sales')\"]").count() >= 1, "a cashier does not see the Sales door"
        login_as(page, OWNER)
    B.check("returns: a cashier sees the Sales door (retail.sell), via a real login", sales_door_for_roles)


ITEMS = {"fav": item_fav, "returns": item_returns}


def main():
    wanted = [a for a in sys.argv[1:] if a in ITEMS] or list(ITEMS)
    with sync_playwright() as p:
        browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
        ids = B.seed_catalogue(page)
        for k in wanted:
            ITEMS[k](page, ids)
        browser.close()
    B.finish()


if __name__ == "__main__":
    main()
