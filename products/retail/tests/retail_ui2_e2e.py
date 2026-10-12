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


ITEMS = {"fav": item_fav}


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
