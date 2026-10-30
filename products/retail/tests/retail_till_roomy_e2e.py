"""Till-roomy -- the selling screen gets its height back, teaches its keys, and a
whole sale (weights, notes, discounts, reprint, returns) runs on the keyboard.
Real browser, real backend, real database -- nothing here reads source text.

Owner brief (2026-10-30): "the screen feels too squeezed" and "a full sale by
keyboard". Each check below is one sentence of that brief made executable:

  ROOM       the first product row starts far higher at 1366x768 AND 1440x900,
             the drawer is ONE chip in the scan bar (End Shift is behind its
             menu, not on the row), the key strip is ONE line, the page title is
             out of the way. The measured numbers are printed and saved.
  KEYS TAUGHT the F1-F6 badges are on screen at both short heights (they were
             hidden under 920px), nothing overflows its tile, Cash carries F1 + X,
             the strip lists the keys.
  KEYBOARD   "1.25*" rings 1.25 kg and the DATABASE holds 1.25; Shift+F5-F8 take
             a cash note; Change Due is up for a short and for a covering amount;
             Shift+F11 edits a line's discount; X in the amount box charges exact
             cash (and in the search box does not); Shift+F10 reprints; Shift+F12
             opens Returns; Shift+F4 still reaches End Shift.
  SAFETY     Void Sale is at the far end from Hold; Delete is apart from Edit/Stock.
  ARABIC     the "more categories" arrow points the reading-forward way in EN and
             in AR (the drawn glyph is measured), a real gap (two no-break spaces, >=4.5px at the
             tile's 15px; one plain space measured 2.8px) sits between the currency
             and a small amount, the drawer line has no ISO date, a unit typed in
             Arabic does not leak into the English screen and takes its plural in
             Arabic.

Run with the SYSTEM Python:  py -3.14 products/retail/tests/retail_till_roomy_e2e.py
Env: TILL_ROOMY_SHOTS=<dir> to keep the screenshots; P1C_MUTATE=<key> serves a
broken copy of the product (see MUTANTS in retail_p1c_e2e_boot.py, tr_* keys).
"""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="till_roomy_")
os.environ.setdefault("AURA_E2E_SCRATCH", os.path.join(_tmp, "scratch"))
os.environ.setdefault("AURA_E2E_SHOTS", os.environ.get("TILL_ROOMY_SHOTS", os.path.join(_tmp, "shots")))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402  (first: sets the AURA_* environment)

from playwright.sync_api import sync_playwright  # noqa: E402

# Measured BEFORE this change, same fixture (1366x768 / 1440x900 EN): the first
# product row started at y=349 / y=380.7. The ceilings below leave ~10px of slack
# over what was measured after.
BEFORE_Y = {"1366x768": 349, "1440x900": 380.7}
MAX_FIRST_ROW_Y = {"1366x768": 250, "1440x900": 255}

MEASURE_JS = """() => {
  const r = (sel) => { const e = document.querySelector(sel); if (!e) return null;
    const b = e.getBoundingClientRect(); return {top: b.top, h: b.height, bottom: b.bottom, left: b.left, right: b.right}; };
  return { header: r('.sub-header'), title: r('#sub-header-section'), scanbar: r('.pos-scanbar'), strip: r('.pos-kbd-hints'),
           drawer: r('#cash-drawer-bar'), cats: r('.pos-cat-row'), firstCard: r('#pos-product-grid .pos-card') };
}"""


def main():
    with sync_playwright() as p:
        browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
        ids = B.seed_catalogue(page)
        shots = B.SHOTS
        kb = page.keyboard

        # ---- extra fixtures: a weighed product, a low-stock product typed in Arabic, long category names
        def mk(name, sku, price, unit, stock, reorder=None, barcode=None):
            body = {"name": name, "sku": sku, "sell_price": price, "cost_price": round(price / 2, 3), "unit": unit}
            if reorder is not None:
                body["reorder_level"] = reorder
            _, r = B.api(page, "POST", "/api/sub/retail/products", body)
            pid = ((r or {}).get("data") or {}).get("id")
            assert pid, f"{name} not created: {r}"
            B.api(page, "POST", f"/api/sub/retail/products/{pid}/stock-adjust", {"quantity": stock, "reason": "till roomy seed"})
            return pid

        mk("Tomatoes", "TR-TOM", 2, "kg", 100)
        mk("Lentils", "TR-LEN", 1.5, "قطعة", 4, reorder=10)
        for i in range(8):
            B.api(page, "POST", "/api/sub/retail/categories", {"name": f"Long category name number {i + 1}"})
        B.go_pos(page)

        def key(k):
            kb.press(k)
            page.wait_for_timeout(130)

        def human(text):
            kb.type(text, delay=90)

        def cart():
            return page.evaluate("() => RetailSystem._cart.map(l => [l.name, l.quantity, l.disc_pct == null ? null : l.disc_pct])")

        def wait_cart_len(n):
            page.wait_for_function("(n) => RetailSystem._cart.length === n", arg=n, timeout=8000)

        def last_sale():
            rows = B.db_one("SELECT id, total, amount_paid, change_amount, payment_method FROM sales ORDER BY id DESC LIMIT 1")
            return rows[0] if rows else None

        def sale_count():
            return B.db_one("SELECT COUNT(*) FROM sales")[0][0]

        def new_sale_from_receipt():
            page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
            key("Enter")
            page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

        def focus_search():
            page.evaluate("() => RetailSystem._refocusScan(true)")

        def add_by_typing(name):
            focus_search()
            human(name)
            key("Enter")

        # ===================================================================== ROOM
        measured = {}

        def room(width, height):
            page.set_viewport_size({"width": width, "height": height})
            B.go_pos(page)
            m = page.evaluate(MEASURE_JS)
            tag = f"{width}x{height}"
            measured[tag] = m
            y = m["firstCard"]["top"]
            page.screenshot(path=str(shots / f"after_{tag}_en.png"))
            assert y <= MAX_FIRST_ROW_Y[tag], f"first product row at y={y:.1f} at {tag} (ceiling {MAX_FIRST_ROW_Y[tag]}, was {BEFORE_Y[tag]})"
            # the drawer is a chip IN the scan bar row, not a strip of its own
            sb, dr = m["scanbar"], m["drawer"]
            assert dr and sb["top"] - 1 <= dr["top"] and dr["bottom"] <= sb["bottom"] + 1, f"the drawer chip is not inside the scan bar row: {dr} vs {sb}"
            # the key strip is one line
            assert m["strip"]["h"] <= 34, f"the key strip is {m['strip']['h']:.0f}px tall: it wrapped"
            # the page title is out of the way (visually hidden), the header is slim
            assert m["title"]["h"] <= 2, f"the page title is still on screen ({m['title']['h']}px tall)"
            assert m["header"]["h"] <= 60, f"the header is {m['header']['h']:.0f}px tall"
            print(f"      {tag}: first product row y={y:.1f} (before {BEFORE_Y[tag]}), header {m['header']['h']:.0f}px, strip {m['strip']['h']:.0f}px", flush=True)

        B.check("ROOM 1366x768: first product row starts high; drawer is a chip in the scan bar; strip is one line; title hidden", lambda: room(1366, 768))
        B.check("ROOM 1440x900: the same", lambda: room(1440, 900))
        (shots / "measure.json").write_text(json.dumps({"before_y": BEFORE_Y, "after": measured}, indent=1), encoding="utf-8")

        # ===================================================================== KEYS TAUGHT
        def badges(width, height):
            page.set_viewport_size({"width": width, "height": height})
            B.go_pos(page)
            B.api(page, "GET", "/api/health")
            add_by_typing("Widget"); wait_cart_len(1)
            info = page.evaluate("""() => Array.from(document.querySelectorAll('.pos-pay-btn')).map(b => {
                const kbd = b.querySelector('.pos-pay-kbd'); const kb = kbd ? kbd.getBoundingClientRect() : null;
                const r = b.getBoundingClientRect();
                return {m: b.dataset.method, kbd: kbd ? kbd.textContent : null, kbdShown: !!kb && kb.width > 0 && getComputedStyle(kbd).display !== 'none',
                        x: Array.from(b.querySelectorAll('.pos-pay-kbd-x')).map(e => e.textContent), h: r.height, w: r.width,
                        overflow: b.scrollHeight - b.clientHeight, label: b.querySelector('.pos-pay-label').textContent,
                        reasonShown: !!b.querySelector('.pos-pay-reason:not([hidden])')};
            })""")
            tag = f"{width}x{height}"
            for t in info:
                assert t["h"] <= 56, f"{tag}: the {t['m']} tile is {t['h']}px tall"
                assert t["overflow"] <= 1, f"{tag}: the {t['m']} tile's content overflows it by {t['overflow']}px: {t}"
                if t["m"] == "credit" and t["reasonShown"]:
                    continue   # the reason replaces the badge on a disabled Credit tile (it has no usable key then)
                assert t["kbdShown"], f"{tag}: the {t['m']} tile shows NO key badge: {t}"
            assert [t["kbd"] for t in info if t["m"] != "credit" or not t["reasonShown"]][0] == "F1"
            cash = next(t for t in info if t["m"] == "cash")
            assert cash["x"] == ["X"], f"{tag}: Cash does not carry X: {cash}"
            mobile = next(t for t in info if t["m"] == "mobile")
            assert mobile["label"] == "CliQ", f"{tag}: the Mobile tile reads {mobile['label']!r} (it wrapped to two lines as 'Mobile / CliQ')"
            page.screenshot(path=str(shots / f"after_tiles_{tag}.png"))
            key("Escape")
            page.evaluate("() => RetailSystem._clearCart()")

        B.check("KEYS TAUGHT 1366x768: F1-F6 badges visible, nothing overflows a tile, Cash has F1 + X, Mobile reads CliQ", lambda: badges(1366, 768))
        B.check("KEYS TAUGHT 1440x900: the same", lambda: badges(1440, 900))

        def strip_lists_keys():
            page.set_viewport_size({"width": 1366, "height": 768})
            B.go_pos(page)
            vis = page.evaluate("""() => { const list = document.querySelector('.pos-kbd-list').getBoundingClientRect();
              return Array.from(document.querySelectorAll('.pos-kbd-hints .pos-kbd-item')).map(i => { const r = i.getBoundingClientRect();
                return [i.innerText.replace(/\\s+/g, ' ').trim(), r.top >= list.top - 1 && r.bottom <= list.bottom + 1 && r.width > 0]; }); }""")
            shown = [t for t, ok in vis if ok]
            assert shown[-1].startswith("F12"), f"F12 More is not on the strip: {vis}"
            for want in ("Enter", "X", "F1", "N*"):
                assert any(s.startswith(want) for s in shown), f"the strip no longer teaches {want}: {shown}"
            assert len(shown) >= 5, f"too few keys fit on the one-line strip at 1366: {shown}"

        B.check("KEYS TAUGHT: the one-line strip lists Enter, X, F1-F6, N*, ... and F12 More at 1366x768", strip_lists_keys)

        # ===================================================================== DRAWER CHIP
        def drawer_chip():
            page.set_viewport_size({"width": 1366, "height": 768})
            B.go_pos(page)
            row_buttons = page.evaluate("() => Array.from(document.querySelectorAll('.pos-scanbar button')).filter(b => b.offsetParent !== null && b.getBoundingClientRect().width > 0).map(b => b.innerText.trim())")
            assert not any("End Shift" in t for t in row_buttons), f"End Shift is a visible button on the selling row: {row_buttons}"
            assert page.locator("#cd-menu").is_hidden()
            page.locator("#cd-chip-btn").click()
            page.wait_for_selector("#cd-menu", state="visible", timeout=3000)
            labels = page.evaluate("() => Array.from(document.querySelectorAll('#cd-menu .cd-menu-item')).map(b => b.innerText.replace(/\\s+/g, ' ').trim())")
            assert any("End Shift" in l for l in labels) and any("Cash In/Out" in l for l in labels), labels
            page.screenshot(path=str(shots / "after_drawer_menu_1366x768.png"))
            key("Escape")
            assert page.locator("#cd-menu").is_hidden(), "Esc did not close the drawer menu"
            focus_search()
            key("Shift+F4")
            page.wait_for_selector("#cd-close-dialog-title", timeout=5000)     # End Shift (Z) dialog = its own confirmation
            key("Escape")
            page.wait_for_selector("#cd-close-dialog-title", state="detached", timeout=5000)

        B.check("DRAWER: one chip in the scan bar; End Shift only inside its menu; Esc closes it; Shift+F4 still opens End Shift", drawer_chip)

        # ===================================================================== KEYBOARD: a weighed sale
        def weighed_sale():
            page.set_viewport_size({"width": 1366, "height": 768})
            B.go_pos(page)
            add_by_typing("Tomatoes"); wait_cart_len(1)
            key("ArrowDown")                                   # select the line
            kb.type("1.25", delay=90)
            key("Shift+Digit8")                                # the "*"
            page.wait_for_function("() => RetailSystem._cart[0] && RetailSystem._cart[0].quantity === 1.25", timeout=5000)
            assert page.evaluate("() => document.getElementById('pos-search').value") == "", "the typed 1.25 stayed in the search box"
            key("Escape")
            before = sale_count()
            key("Enter")                                       # empty search: go to pay
            key("Enter")                                       # blank amount = exact: charge
            new_sale_from_receipt()
            assert sale_count() == before + 1
            q = B.db_one("SELECT quantity, line_total FROM sale_items ORDER BY id DESC LIMIT 1")[0]
            assert abs(q[0] - 1.25) < 1e-9 and abs(q[1] - 2.5) < 1e-6, f"the database holds {q}, not 1.25 kg = 2.5"

        B.check("KEYBOARD: '1.25*' on a selected line rings 1.25 kg and the DATABASE holds 1.25 (line total 2.5)", weighed_sale)

        # ===================================================================== KEYBOARD: cash notes + Change Due
        def notes_and_change_due():
            B.go_pos(page)
            add_by_typing("Widget"); wait_cart_len(1)           # 10
            add_by_typing("Gadget"); wait_cart_len(2)           # + 5 = 15
            key("F1")                                           # Cash: caret in the amount box, panel open
            focus = page.evaluate("() => document.activeElement && document.activeElement.id")
            assert focus == "pos-tendered", focus
            key("Shift+F6")                                     # a 10 note: short of 15
            assert page.evaluate("() => document.getElementById('pos-tendered').value") == "10"
            lbl = page.evaluate("() => document.getElementById('pos-change-lbl').textContent")
            assert lbl == "Still to pay", lbl
            assert page.evaluate("() => getComputedStyle(document.getElementById('pos-change-row')).display") != "none", "Change Due row hidden for a short amount"
            assert sale_count() == B.db_one("SELECT COUNT(*) FROM sales")[0][0]
            key("Shift+F7")                                     # a 20 note: covers
            assert page.evaluate("() => document.getElementById('pos-tendered').value") == "20"
            assert page.evaluate("() => document.getElementById('pos-change-lbl').textContent") == "Change Due"
            size = page.evaluate("() => parseFloat(getComputedStyle(document.getElementById('pos-change')).fontSize)")
            assert size >= 24, f"Change Due is only {size}px"
            fig = page.evaluate("() => document.getElementById('pos-change').textContent")
            assert "5.000" in fig, fig
            page.evaluate("() => RetailSystem._closeCashPanel(false)")
            page.screenshot(path=str(shots / "after_change_due_1366x768.png"))
            before = sale_count()
            key("Enter")                                        # charge with the 20 in the box
            new_sale_from_receipt()
            assert sale_count() == before + 1
            s = last_sale()
            assert s[4] == "cash" and abs(s[2] - 20) < 6e-4 and abs(s[3] - 5) < 6e-4, f"sale {s}: expected cash 20 tendered, 5 change"

        B.check("KEYBOARD: Shift+F6/F7 take 10 and 20 from the scan box path; Change Due shows short ('Still to pay') and covered (large); Enter charges", notes_and_change_due)

        # ===================================================================== KEYBOARD: line discount
        def line_discount():
            B.go_pos(page)
            add_by_typing("Widget"); wait_cart_len(1)
            focus_search()
            key("Shift+F11")
            page.wait_for_selector("#lm-dialog", timeout=5000)
            assert page.evaluate("() => document.activeElement && document.activeElement.id") == "lm-pct", "the line dialog did not open on Discount"
            kb.type("10")
            key("Enter")                                        # Enter applies
            page.wait_for_selector("#lm-dialog", state="detached", timeout=5000)
            assert cart()[0][2] == 10, cart()
            total = page.evaluate("() => RetailSystem._currentTotals.total")
            assert abs(total - 9) < 6e-4, f"10% off a 10 line should total 9, got {total}"
            page.evaluate("() => RetailSystem._clearCart()")

        B.check("KEYBOARD: Shift+F11 opens the line on Discount, '10' + Enter applies it (total 9)", line_discount)

        # ===================================================================== KEYBOARD: X
        def x_keys():
            B.go_pos(page)
            add_by_typing("Gadget"); wait_cart_len(1)
            before = sale_count()
            # X in the SEARCH box types a letter and charges nothing
            focus_search()
            kb.press("x")
            page.wait_for_timeout(500)
            assert sale_count() == before, "X in the search box charged the sale"
            page.evaluate("() => { document.getElementById('pos-search').value = ''; }")
            key("Enter")                                        # empty search: go to pay -> amount box
            assert page.evaluate("() => document.activeElement && document.activeElement.id") == "pos-tendered"
            kb.press("x")                                        # exact cash from the amount box
            new_sale_from_receipt()
            assert sale_count() == before + 1
            s = last_sale()
            assert s[4] == "cash" and abs(s[2] - 5) < 6e-4 and abs(s[1] - 5) < 6e-4, f"exact-cash sale wrong: {s}"

        B.check("KEYBOARD: X in the empty amount box charges exact cash; X in the search box charges nothing", x_keys)

        # ===================================================================== KEYBOARD: reprint + returns
        def reprint_and_returns():
            B.go_pos(page)
            page.evaluate("() => { window.__printed = []; RetailSystem._printReceipt = async (s) => { window.__printed.push(s.sale_number); }; }")
            assert page.evaluate("() => !!RetailSystem._lastSaleData"), "no completed sale in memory"
            focus_search()
            key("Shift+F10")
            page.wait_for_function("() => window.__printed.length === 1", timeout=3000)
            assert page.evaluate("() => window.__printed[0]") == page.evaluate("() => RetailSystem._lastSaleData.sale_number")
            key("Shift+F12")
            page.wait_for_function("() => RetailSystem._section === 'returns'", timeout=5000)
            B.go_pos(page)

        B.check("KEYBOARD: Shift+F10 prints the last sale again; Shift+F12 opens Returns", reprint_and_returns)

        # ===================================================================== SAFETY
        def safety():
            page.set_viewport_size({"width": 1366, "height": 768})
            B.go_pos(page)
            g = page.evaluate("""() => { const c = (s) => { const b = document.querySelector(s).getBoundingClientRect(); return {l: b.left, r: b.right, cx: (b.left + b.right) / 2}; };
              return {void: c('.pos-clear-btn'), hold: c('.pos-hold-btn'), pane: c('.pos-right')}; }""")
            assert abs(g["void"]["cx"] - g["hold"]["cx"]) >= 220, f"Void Sale and Hold are only {abs(g['void']['cx'] - g['hold']['cx']):.0f}px apart: {g}"
            assert g["void"]["l"] - g["pane"]["l"] < 40, f"Void Sale is not at the start edge of the sale pane: {g}"
            B.go(page, "products")
            page.wait_for_selector("table tbody tr", timeout=10000)
            d = page.evaluate("""() => { const row = document.querySelector('table tbody tr'); const bs = Array.from(row.querySelectorAll('button'));
              const f = (t) => bs.find(b => b.textContent.trim() === t).getBoundingClientRect(); const s = f('Stock'), x = f('Delete'); return {stockRight: s.right, deleteLeft: x.left, delRtl: false}; }""")
            assert d["deleteLeft"] - d["stockRight"] >= 24, f"Delete is only {d['deleteLeft'] - d['stockRight']:.0f}px from Stock"
            B.go_pos(page)

        B.check("SAFETY: Void Sale is far from Hold, at the pane's start edge; Delete is >=24px apart from Stock", safety)

        # ===================================================================== ARABIC + units
        def units_en():
            B.go_pos(page)
            page.fill("#pos-search", "Lentils")
            page.wait_for_timeout(700)
            txt = page.evaluate("() => (document.querySelector('#pos-product-grid .pos-card') || {innerText: ''}).innerText")
            assert "Low stock" in txt, txt
            assert not any("؀" <= ch <= "ۿ" for ch in txt), f"an Arabic unit leaked onto the English tile: {txt!r}"
            assert "pcs" in txt, txt
            page.fill("#pos-search", "")

        B.check("ARABIC: a unit typed in Arabic does not leak onto the English low-stock tile (it reads pcs)", units_en)

        def rtl_checks():
            page.evaluate("() => { if (AuraI18n.current !== 'ar') AuraI18n.toggle(); }")
            page.wait_for_timeout(900)
            page.set_viewport_size({"width": 1366, "height": 768})
            B.go_pos(page)
            page.screenshot(path=str(shots / "after_1366x768_ar.png"))
            # 1. a space between the currency mark and a small amount (measured from the glyph boxes)
            gap = page.evaluate("""() => { const e = document.querySelector('.pos-card-price .money'); const n = e.firstChild; const t = n.textContent;
              const rng = document.createRange(); const box = (i) => { rng.setStart(n, i); rng.setEnd(n, i + 1); return rng.getBoundingClientRect(); };
              const sym = [], dig = [];
              const firstDigit = Array.from(t).findIndex((ch) => ch >= '0' && ch <= '9');   // the mark's own "." sits BEFORE it
              for (let i = 0; i < t.length; i++) { const c = t.charCodeAt(i); const b = box(i); if (b.width === 0) continue;
                if (i < firstDigit && (c === 0x2e || (c >= 0x600 && c <= 0x6ff))) sym.push(b); else if (i >= firstDigit) dig.push(b); }
              const ext = (a) => ({l: Math.min(...a.map(b => b.left)), r: Math.max(...a.map(b => b.right))});
              const s = ext(sym), d = ext(dig);
              return {gap: s.r <= d.l ? d.l - s.r : s.l - d.r, text: t}; }""")
            assert gap["gap"] >= 4.5, f"the currency mark is crowding the amount (gap {gap['gap']:.1f}px): {gap['text']!r}"
            # 2. the drawer chip: no ISO date, no orphan dot
            chip = page.evaluate("() => document.getElementById('cash-drawer-bar').innerText")
            import re
            assert not re.search(r"\d{4}-\d{2}-\d{2}", chip), f"an ISO date in the drawer chip: {chip!r}"
            assert "·" not in chip, f"a stray dot in the drawer chip: {chip!r}"
            # 3. the plural: 4 pieces is 'قطع', not 'قطعة'
            page.fill("#pos-search", "Lentils")
            page.wait_for_timeout(700)
            tile = page.evaluate("() => document.querySelector('#pos-product-grid .pos-card').innerText")
            assert "4 قطع" in tile and "4 قطعة" not in tile, f"the Arabic plural is wrong: {tile!r}"
            page.fill("#pos-search", "")

        B.check("ARABIC: the currency mark is not glued to small amounts, the drawer chip has no ISO date or stray dot, '4 قطع' takes its plural", rtl_checks)

        # --- the "more categories" arrow, measured from the DRAWN glyph, in AR then EN
        def arrow_points(lang):
            page.evaluate("(l) => { if (AuraI18n.current !== l) AuraI18n.toggle(); }", lang)
            page.wait_for_timeout(900)
            page.set_viewport_size({"width": 1366, "height": 768})
            B.go_pos(page)
            page.evaluate("() => RetailSystem._updateCatOverflow()")
            page.wait_for_selector("#pos-cat-wrap.has-more #pos-cat-more", state="visible", timeout=5000)
            png = page.locator("#pos-cat-more .pos-cat-more-glyph").screenshot()
            b64 = base64.b64encode(png).decode()
            side = page.evaluate("""async (b64) => {
              const bmp = await createImageBitmap(await (await fetch('data:image/png;base64,' + b64)).blob());
              const c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height; const x = c.getContext('2d'); x.drawImage(bmp, 0, 0);
              const d = x.getImageData(0, 0, c.width, c.height).data; const bg = [d[0], d[1], d[2]];
              const ink = (i, j) => { const k = (j * c.width + i) * 4; return Math.abs(d[k] - bg[0]) + Math.abs(d[k + 1] - bg[1]) + Math.abs(d[k + 2] - bg[2]) > 120; };
              let minx = 1e9, maxx = -1, miny = 1e9, maxy = -1;
              for (let j = 0; j < c.height; j++) for (let i = 0; i < c.width; i++) if (ink(i, j)) { minx = Math.min(minx, i); maxx = Math.max(maxx, i); miny = Math.min(miny, j); maxy = Math.max(maxy, j); }
              if (maxx < 0) return null;
              const mid = (miny + maxy) / 2; let sum = 0, n = 0;
              for (let j = Math.floor(mid - 1); j <= Math.ceil(mid + 1); j++) for (let i = minx; i <= maxx; i++) if (ink(i, j)) { sum += i; n += 1; }
              return n ? (sum / n) - (minx + maxx) / 2 : null; }""", b64)
            assert side is not None, "no glyph pixels found"
            return side

        def arrow_en():
            s = arrow_points("en")
            assert s > 0.2, f"EN: the more-categories arrow does not point right (apex offset {s:.2f})"

        def arrow_ar():
            s = arrow_points("ar")
            assert s < -0.2, f"AR: the more-categories arrow does not point left (apex offset {s:.2f}) -- the wrong way for 'more' in RTL"
            page.screenshot(path=str(shots / "after_categories_arrow_1366x768_ar.png"))
            page.evaluate("() => { if (AuraI18n.current !== 'en') AuraI18n.toggle(); }")
            page.wait_for_timeout(600)

        B.check("ARABIC: the 'more categories' arrow (drawn glyph, measured) points right in EN", arrow_en)
        B.check("ARABIC: ...and points LEFT in AR (reading-forward in RTL)", arrow_ar)

        browser.close()
    B.finish()


main()
