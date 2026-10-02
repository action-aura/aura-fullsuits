"""P1c -- "I could not find where to change the currency", in a real browser.

Settings > Shop & receipt now has a Shop currency card (owner only). This drives
it for real against the real backend:

  * the card lists the currencies the server can format, the current one selected;
  * with NO sale yet, choosing USD and pressing Change currency just works -- and
    the till then shows the new symbol and decimals ("$ 10.00", two places);
  * once a sale exists, choosing JOD shows the plain warning dialog; Cancel changes
    nothing; Change it applies, and the till shows "JD 10.000" (three places);
  * the past sale's stored total is byte-identical after both changes.

Run with the SYSTEM Python:  py -3.14 products/retail/tests/retail_p1c_currency_e2e.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402


def stored_currency(page):
    _, j = B.api(page, "GET", "/api/sub/retail/settings/tax")
    return j["data"]["base_currency"]


def open_currency_card(page):
    B.go(page, "admin-center", 1500)
    page.evaluate("() => SettingsHub.open('shop-receipt')")
    page.wait_for_selector("#cur-select:not([disabled])", timeout=10000)


def till_total_text(page, product_key_id):
    B.go_pos(page)
    page.evaluate("() => RetailSystem._clearCart()")
    page.evaluate("(id) => RetailSystem._addToCart(id)", product_key_id)
    page.wait_for_timeout(500)
    return page.inner_text("#pos-total").strip()


with sync_playwright() as p:
    browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
    ids = B.seed_catalogue(page)     # widget = 10

    def card_lists_currencies():
        open_currency_card(page)
        assert page.input_value("#cur-select") == "JOD", "the current currency is not preselected"
        opts = page.eval_on_selector_all("#cur-select option", "els => els.map(e => e.value)")
        assert opts[0] == "JOD" and {"USD", "EUR", "SAR", "AED", "KWD"} <= set(opts), f"the list is missing currencies: {opts}"
        label = page.eval_on_selector("#cur-select option[value=USD]", "e => e.textContent")
        assert "US dollar" in label and "$" in label, f"an option does not carry its name and symbol: {label!r}"
        page.screenshot(path=str(B.SHOTS / "currency_card.png"))

    B.check("Settings shows the Shop currency card with the server's list, current one selected", card_lists_currencies)

    def change_without_sales():
        open_currency_card(page)
        page.select_option("#cur-select", "USD")
        page.click("#cur-apply")
        page.wait_for_function("() => /USD/.test(document.getElementById('cur-status').textContent)", timeout=8000)
        assert page.locator("#ret-confirm-ok").count() == 0, "a shop with no sales was asked to confirm"
        assert stored_currency(page) == "USD"
        txt = till_total_text(page, ids["widget"])
        assert re.search(r"\$\s?10\.00\b", txt), f"the till did not switch to $ with two decimals: {txt!r}"

    B.check("no sales yet: choosing USD applies at once and the till shows $ with two decimals", change_without_sales)

    # one sale on the books (the same route the till posts to)
    _, s = B.api(page, "POST", "/api/sub/retail/sales", {
        "items": [{"product_id": ids["widget"], "quantity": 2, "unit_price": 10}],
        "payment_method": "cash", "amount_paid": 20, "idempotency_key": "p1c-currency-sale"})
    sale_id = (s.get("data") or {}).get("id")
    assert sale_id, f"the seeded sale was not created: {s}"
    total_before = B.db_one("SELECT total FROM sales WHERE id = ?", (sale_id,))[0][0]

    def change_over_sales_cancel_then_confirm():
        open_currency_card(page)
        page.select_option("#cur-select", "JOD")
        page.click("#cur-apply")
        page.wait_for_selector("#ret-confirm-ok", timeout=8000)
        msg = page.inner_text(".ret-modal-overlay")
        assert "past sales" in msg.lower() and "old amounts" in msg.lower(), f"the warning is not the plain one: {msg!r}"
        page.screenshot(path=str(B.SHOTS / "currency_warning.png"))
        page.click("#ret-confirm-cancel")
        page.wait_for_selector("#ret-confirm-ok", state="detached", timeout=5000)
        assert stored_currency(page) == "USD", "Cancel still changed the currency"
        assert page.input_value("#cur-select") == "USD", "the select was not put back after Cancel"
        page.select_option("#cur-select", "JOD")
        page.click("#cur-apply")
        page.wait_for_selector("#ret-confirm-ok", timeout=8000)
        page.click("#ret-confirm-ok")
        page.wait_for_function("() => /JOD/.test(document.getElementById('cur-status').textContent)", timeout=8000)
        assert stored_currency(page) == "JOD"
        txt = till_total_text(page, ids["widget"])
        assert re.search(r"JD\s?10\.000\b", txt), f"the till did not switch to JD with three decimals: {txt!r}"

    B.check("with a sale on the books: the plain warning, Cancel changes nothing, confirming applies JD / 3 decimals", change_over_sales_cancel_then_confirm)

    def amounts_never_rewritten():
        total_after = B.db_one("SELECT total FROM sales WHERE id = ?", (sale_id,))[0][0]
        assert total_after == total_before, f"a stored sale total moved: {total_before} -> {total_after}"

    B.check("the past sale's stored total is unchanged after both currency changes", amounts_never_rewritten)

    page.screenshot(path=str(B.SHOTS / "currency_till_jd.png"))
    browser.close()

B.finish()
