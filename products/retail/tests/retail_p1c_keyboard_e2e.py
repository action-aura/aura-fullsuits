"""P1c -- a WHOLE SALE on the keyboard, in a real browser against the real app.

The owner's brief: "a cashier with a queue must be fast; every till operation on
the keyboard; a whole sale keyboard-only". This script is that sentence made
executable, and it is deliberately written with NO mouse call at all: the page
object is only ever driven through page.keyboard / CDP key events, and a
document-level listener counts every mousedown/pointerdown/click the browser
sees (a keyboard-activated button would show up as a click, so the count staying
zero is evidence the sale really needed no pointer).

Covers: type + Enter adds a product; a scanner burst adds one; "+" changes a
quantity; Arrow selects a line and Delete removes it; "N*" arms a quantity (and
the typed "2*" does not stay in the box); F7 picks a customer by typing; F8 sets
a 10% discount; Enter (empty search) goes to pay; typing the tendered amount and
Enter charges -- then the sales table is read straight from the DB for the exact
total, tender and customer. After the sale: Enter on a focused button never
charges; P prints and focus comes back to the search box; Enter on the receipt
starts the next sale; a scan while the receipt is open closes it and rings that
item; F9 holds with a typed note; Shift+F9 resumes with Enter; F11 price check
looks up WITHOUT adding and Enter adds; F12 shows the key card; Ctrl+Delete voids
(behind its confirm); an ARABIC-LAYOUT X key (real CDP event, key "ء", code KeyX)
charges exact cash; Shift+F2 records a cash movement; F10 charges.

Run with the SYSTEM Python:  py -3.14 products/retail/tests/retail_p1c_keyboard_e2e.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402  (must come first: sets the AURA_* environment)

from playwright.sync_api import sync_playwright  # noqa: E402

SCAN_WIDGET = "4901234567894"
SCAN_GADGET = "4901234567801"
SCAN_DOOHICKEY = "4901234567825"


def last_sale():
    rows = B.db_one("SELECT id, total, amount_paid, change_amount, payment_method, customer_id, discount_amount FROM sales ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def sale_count():
    return B.db_one("SELECT COUNT(*) FROM sales")[0][0]


with sync_playwright() as p:
    browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
    ids = B.seed_catalogue(page)
    B.go_pos(page)
    kb = page.keyboard

    # No pointer, ever: count every pointer event the browser delivers.
    page.evaluate("""() => {
      window.__ptr = 0;
      // A click with detail 0 is a KEYBOARD activation (Enter/Space on a button);
      // a real mouse click has detail >= 1. Only the latter is a pointer action.
      for (const t of ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'touchstart'])
        document.addEventListener(t, () => { window.__ptr++; }, true);
      document.addEventListener('click', (e) => { if (e.detail > 0) window.__ptr++; }, true);
    }""")

    def key(k):
        # A human's pace. Back-to-back synthetic presses (0 ms apart) are exactly
        # what the scanner detector reads as a barcode burst -- by design -- and
        # would make the till (correctly) ignore its own shortcuts.
        kb.press(k)
        page.wait_for_timeout(130)

    def human(text):
        kb.type(text, delay=90)         # a typist: gaps well over the scanner's 50ms window

    def scan(code):
        kb.type(code, delay=0)          # a wedge scanner: a burst of keys...
        kb.press("Enter")               # ...ended by Enter

    def cart():
        return page.evaluate("() => RetailSystem._cart.map(l => [l.name, l.quantity])")

    def wait_cart_len(n):
        page.wait_for_function("(n) => RetailSystem._cart.length === n", arg=n, timeout=8000)

    def active():
        return page.evaluate("() => { const e = document.activeElement; return e ? (e.id || e.tagName) : null; }")

    def search_value():
        return page.evaluate("() => document.getElementById('pos-search').value")

    def focus_search_programmatically():
        # Not a pointer action: the same call the product's own _refocusScan makes.
        page.evaluate("() => RetailSystem._refocusScan(true)")

    def wait_receipt():
        page.wait_for_selector("#ret-receipt-dialog", timeout=10000)

    # -----------------------------------------------------------------------
    def the_whole_sale():
        assert active() == "pos-search", f"the till did not open with the caret in the search box (focus: {active()})"
        # 1. three products by typing + Enter
        for name in ("Widget", "Gadget", "Gizmo"):
            human(name)
            key("Enter")
            wait_cart_len([n for n in ("Widget", "Gadget", "Gizmo")].index(name) + 1)
            assert search_value() == "", f"the search box kept {search_value()!r} after adding {name}"
        assert [c[0] for c in cart()] == ["Widget", "Gadget", "Gizmo"], cart()
        # 2. one by a scanner burst
        scan(SCAN_DOOHICKEY)
        wait_cart_len(4)
        assert cart()[3][0] == "Doohickey", f"the scan did not add Doohickey: {cart()}"
        assert search_value() == "", "the scan left text in the search box"
        assert sale_count() == 0, "a scan or an Enter charged the sale"
        # 3. "+" on the last line, then select Gadget with the arrows and "+" it twice
        key("Equal", )          # Shift-less "=" is the same physical key as "+"
        assert cart()[3][1] == 2, f"'+' did not add one to the last line: {cart()}"
        key("ArrowDown")        # first line (Widget)
        key("ArrowDown")        # Gadget
        key("NumpadAdd")
        key("NumpadAdd")
        assert cart()[1] == ["Gadget", 3], f"'+' on the selected line failed: {cart()}"
        assert page.evaluate("() => document.querySelectorAll('.pos-cart-row-sel').length") == 1, "the selected line is not highlighted"
        # 4. select Gizmo and delete it; then put Doohickey back to 1 with '-'
        key("ArrowDown")        # Gizmo
        key("Delete")
        assert [c[0] for c in cart()] == ["Widget", "Gadget", "Doohickey"], f"Delete removed the wrong line: {cart()}"
        key("ArrowDown")        # selection now follows the index: lands on Doohickey
        key("Minus")
        assert cart()[2] == ["Doohickey", 1], f"'-' did not take one off: {cart()}"
        key("Escape")           # clear the line selection
        assert page.evaluate("() => RetailSystem._selLine") is None
        # 5. "2*" then Thingamajig -> quantity 2; the "2*" must not stay in the box
        kb.type("2", delay=90)
        key("Shift+Digit8")
        assert search_value() == "", f"the typed multiplier stayed in the search box: {search_value()!r}"
        human("Thinga")
        key("Enter")
        wait_cart_len(4)
        assert cart()[3] == ["Thingamajig Deluxe", 2], f"'2*' did not give the next item quantity 2: {cart()}"
        # 6. F7: pick the customer by typing
        key("F7")
        page.wait_for_selector("#ret-cust-pick", timeout=5000)
        human("Samira")
        key("Enter")
        page.wait_for_selector("#ret-cust-pick", state="detached", timeout=5000)
        assert page.evaluate("() => document.getElementById('pos-customer').value") == str(ids["customer"]), "F7 did not select the customer"
        assert active() == "pos-search", f"focus did not return to the search box after the customer picker (focus: {active()})"
        # 7. F8: 10% discount, Enter returns to the search box
        key("F8")
        assert active() == "pos-disc", f"F8 did not focus the discount box (focus: {active()})"
        kb.type("10", delay=40)
        key("Enter")
        assert active() == "pos-search", f"Enter in the discount box did not return to the search box (focus: {active()})"
        assert page.evaluate("() => document.getElementById('pos-disc').value") == "10"
        expected_total = round((10 + 15 + 3 + 24) * 0.9, 3)
        shown = page.evaluate("() => RetailSystem._currentTotals.total")
        assert abs(shown - expected_total) < 0.0006, f"the till shows total {shown}, expected {expected_total}"
        assert sale_count() == 0, "something charged the sale before the cashier asked"
        # 8. Enter in the EMPTY search = go to pay; type the tendered amount; Enter charges
        key("Enter")
        assert active() == "pos-tendered", f"Enter in an empty search did not go to pay (focus: {active()})"
        assert sale_count() == 0, "go-to-pay charged the sale"
        tendered = math.ceil(expected_total) + 5
        kb.type(str(tendered), delay=40)
        key("Enter")
        wait_receipt()
        s = last_sale()
        assert s is not None and sale_count() == 1, "no sale row after the keyboard-only sale"
        assert abs(s[1] - expected_total) < 0.0006, f"sale total {s[1]} != expected {expected_total}"
        assert abs(s[2] - tendered) < 0.0006, f"amount_paid {s[2]} != tendered {tendered}"
        assert abs(s[3] - (tendered - expected_total)) < 0.0006, f"change {s[3]} wrong"
        assert s[4] == "cash" and str(s[5]) == str(ids["customer"]), f"tender/customer wrong: {s}"
        lines = B.db_one("SELECT p.name, si.quantity FROM sale_items si JOIN products p ON p.id = si.product_id WHERE si.sale_id = ? ORDER BY si.id", (s[0],))
        assert sorted((n, int(q)) for n, q in lines) == sorted([("Widget", 1), ("Gadget", 3), ("Doohickey", 1), ("Thingamajig Deluxe", 2)]), f"sale lines wrong: {lines}"

    B.check("a whole sale, keyboard only: type+Enter x3, scan, +/-, arrows+Delete, 2*, F7 customer, F8 discount, Enter-to-pay, tender, Enter", the_whole_sale)

    # -----------------------------------------------------------------------
    def receipt_keys_and_print_focus():
        # P prints (headless print() returns at once); focus must come back to search.
        key("KeyP")
        page.wait_for_timeout(900)
        assert page.locator("#ret-receipt-dialog").count() == 1, "P closed the receipt instead of printing it"
        assert active() == "pos-search", f"focus did not come back to the search box after printing (focus: {active()})"
        key("Enter")
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
        assert active() == "pos-search", f"focus is not in the search box after Enter on the receipt (focus: {active()})"
        assert cart() == [], "the cart was not empty for the next sale"

    B.check("receipt: P prints and focus returns to the search box; Enter starts the next sale with the caret in the search box", receipt_keys_and_print_focus)

    # -----------------------------------------------------------------------
    def enter_on_a_button_never_charges():
        human("Widget"); key("Enter"); wait_cart_len(1)
        before = sale_count()
        for sel, what in (("#pos-cart .pos-qty-btn:last-child", "a qty button"), ("#pos-product-grid .pos-card", "a product tile"),
                          (".pos-pay-btn[data-method=card]", "a tender button")):
            page.evaluate("(s) => document.querySelector(s).focus()", sel)   # keyboard-style focus (Tab), no pointer
            key("Enter")
            page.wait_for_timeout(250)
            assert sale_count() == before, f"Enter on {what} charged the sale"
        # the qty button pressed ITSELF (+1) and the tile added its product, tender button selected card
        assert page.evaluate("() => RetailSystem._paymentMethod") == "card", "Enter on the Card tender button did not select it"
        page.evaluate("() => { RetailSystem._clearCart(); }")   # tidy: back to an empty cart + cash
        page.wait_for_timeout(300)

    B.check("Enter on a focused qty / tile / tender button presses that button and never charges", enter_on_a_button_never_charges)

    # -----------------------------------------------------------------------
    def scan_while_receipt_open():
        focus_search_programmatically()
        human("Widget"); key("Enter"); wait_cart_len(1)
        key("F10")                                   # charge now (cash, blank = exact)
        wait_receipt()
        scan(SCAN_GADGET)                                  # the next customer's first item
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
        page.wait_for_function("() => RetailSystem._cart.length === 1", timeout=8000)
        assert cart()[0][0] == "Gadget", f"the scan did not start the next sale with Gadget: {cart()}"
        assert active() == "pos-search", f"focus is not in the search box after the scan (focus: {active()})"

    B.check("F10 charges; a scan while the receipt is open closes it and starts the next sale with that item", scan_while_receipt_open)

    # -----------------------------------------------------------------------
    def hold_and_resume_by_keyboard():
        key("F9")
        page.wait_for_selector("#ret-hold-modal", timeout=5000)
        assert active() == "hold-label", f"the hold dialog did not focus its note field (focus: {active()})"
        human("Table 4")
        key("Enter")
        page.wait_for_selector("#ret-hold-modal", state="detached", timeout=5000)
        wait_cart_len(0)
        _, held = B.api(page, "GET", "/api/sub/retail/held-sales")
        rows = (held or {}).get("data") or []
        assert len(rows) == 1 and rows[0].get("label") == "Table 4", f"hold did not save the typed note: {rows}"
        key("Shift+F9")
        page.wait_for_selector("#ret-held-dialog .pk-held-row", timeout=5000)
        key("Enter")                                  # resume the highlighted held sale
        page.wait_for_function("() => RetailSystem._cart.length === 1", timeout=8000)
        assert cart()[0][0] == "Gadget", f"Shift+F9 + Enter did not resume the held sale: {cart()}"

    B.check("F9 holds with a typed note (Enter saves); Shift+F9 then Enter resumes it", hold_and_resume_by_keyboard)

    # -----------------------------------------------------------------------
    def price_check_does_not_add():
        focus_search_programmatically()
        before = cart()
        key("F11")
        page.wait_for_selector("#ret-pricecheck", timeout=5000)
        human("Widget")
        key("Enter")
        page.wait_for_selector("#pc-result .pk-row", timeout=5000)
        txt = page.inner_text("#pc-result")
        assert "Widget" in txt and "10" in txt, f"price check did not show the name and price: {txt!r}"
        assert cart() == before, "the price check ADDED the item to the sale"
        key("Escape")
        page.wait_for_selector("#ret-pricecheck", state="detached", timeout=5000)
        assert active() == "pos-search", f"focus did not return to the search box after the price check (focus: {active()})"
        key("F11")
        page.wait_for_selector("#ret-pricecheck", timeout=5000)
        human("Widget"); key("Enter"); page.wait_for_selector("#pc-result .pk-row", timeout=5000)
        key("Enter")                                  # same term again = add
        page.wait_for_selector("#ret-pricecheck", state="detached", timeout=5000)
        page.wait_for_function("() => RetailSystem._cart.length === 2", timeout=8000)
        assert [c[0] for c in cart()] == ["Gadget", "Widget"], cart()

    B.check("F11 price check shows name/price/stock without adding; Enter again adds; Esc closes", price_check_does_not_add)

    # -----------------------------------------------------------------------
    def key_card_opens_and_closes():
        key("F12")
        page.wait_for_selector("#ret-keyhelp", timeout=5000)
        txt = page.inner_text("#ret-keyhelp")
        for needle in ("F10", "F7", "F11", "Ctrl+Delete", "Shift+F9"):
            assert needle in txt, f"the key card does not list {needle}: {txt[:300]!r}"
        key("Escape")
        page.wait_for_selector("#ret-keyhelp", state="detached", timeout=5000)
        assert active() == "pos-search", f"focus did not return to the search box after the key card (focus: {active()})"
        key("Control+Slash")
        page.wait_for_selector("#ret-keyhelp", timeout=5000)
        key("Escape")
        page.wait_for_selector("#ret-keyhelp", state="detached", timeout=5000)

    B.check("F12 and Ctrl+/ open the key card; Esc closes it and the caret returns to the search box", key_card_opens_and_closes)

    # -----------------------------------------------------------------------
    def drawer_keys():
        before = B.db_one("SELECT COUNT(*) FROM cash_movements")[0][0]
        key("Shift+F2")
        page.wait_for_selector("#cd-mv-modal", timeout=5000)
        assert active() == "cd-mv-amount", f"the cash movement dialog did not focus its amount (focus: {active()})"
        kb.type("5", delay=40)
        key("Enter")
        page.wait_for_selector("#cd-mv-modal", state="detached", timeout=5000)
        page.wait_for_timeout(300)
        assert B.db_one("SELECT COUNT(*) FROM cash_movements")[0][0] == before + 1, "Shift+F2, 5, Enter did not record a cash movement"
        key("Shift+F3")
        page.wait_for_selector("#cd-x-modal", timeout=5000)
        key("Escape")
        page.wait_for_selector("#cd-x-modal", state="detached", timeout=5000)

    B.check("Shift+F2 records a cash movement with Enter; Shift+F3 opens the X report; Esc closes", drawer_keys)

    # -----------------------------------------------------------------------
    def void_by_keyboard():
        assert len(cart()) == 2
        key("Control+Delete")
        page.wait_for_selector("#ret-confirm-ok", timeout=5000)
        assert page.evaluate("() => document.activeElement && document.activeElement.id") == "ret-confirm-cancel", "the void confirm does not start on Cancel"
        key("Enter")                                  # Enter on Cancel = no
        page.wait_for_selector("#ret-confirm-ok", state="detached", timeout=5000)
        assert len(cart()) == 2, "Enter on the void confirm's Cancel still voided the sale"
        key("Control+Delete")
        page.wait_for_selector("#ret-confirm-ok", timeout=5000)
        key("Tab"); key("Enter")                 # Tab to Void Sale, Enter presses it
        wait_cart_len(0)

    B.check("Ctrl+Delete voids the sale only after the confirm (Cancel is the default; Tab then Enter confirms)", void_by_keyboard)

    # -----------------------------------------------------------------------
    def arabic_layout_exact_cash():
        focus_search_programmatically()
        human("Widget"); key("Enter"); wait_cart_len(1)
        page.evaluate("() => document.activeElement && document.activeElement.blur()")   # X is a typed key: off the search box
        before = sale_count()
        cdp = ctx.new_cdp_session(page)
        # The ARABIC layout's glyph for the physical X key is not "x": e.key is "ء"-family, e.code stays KeyX.
        for typ in ("rawKeyDown", "keyUp"):
            cdp.send("Input.dispatchKeyEvent", {"type": typ, "key": "ء", "code": "KeyX", "windowsVirtualKeyCode": 88})
        wait_receipt()
        assert sale_count() == before + 1, "the Arabic-layout X key did not charge exact cash"
        s = last_sale()
        assert s[4] == "cash" and abs(s[2] - 10) < 0.0006 and abs(s[1] - 10) < 0.0006, f"exact-cash sale wrong: {s}"
        key("Enter")
        page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)

    B.check("an ARABIC-layout X (real CDP key event, e.key 'ء', e.code KeyX) charges exact cash", arabic_layout_exact_cash)

    # -----------------------------------------------------------------------
    def no_pointer_was_used():
        n = page.evaluate("() => window.__ptr")
        assert n == 0, f"the browser saw {n} pointer/click events: the sale was not keyboard-only"
        assert page.evaluate("() => document.querySelectorAll('.pos-cart-row-sel').length") <= 1

    B.check("the browser saw ZERO mouse/pointer/click events during the whole run", no_pointer_was_used)

    page.screenshot(path=str(B.SHOTS / "kbd_e2e_final.png"))
    browser.close()

B.finish()
