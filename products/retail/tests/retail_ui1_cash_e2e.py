"""UI-1 -- the cash panel and the Sale Complete dialog, in a real browser.

Boots the REAL Flask backend (retail_p1c_e2e_boot.py's recipe), signs up, seeds a
small catalogue, then drives a headless Chromium through full CASH SALES the way
a cashier does -- with the on-screen pad, with an amount chip, and with the
physical keyboard -- and measures what a screenshot alone cannot prove:

  * the panel opens on the Cash tile and on F1, has a 3x4 pad (digits, ".", "00")
    and an "Exact" chip + notes above the total, and the browser's number spinner
    is gone (#pos-tendered is a text field);
  * while it is open the TOTAL and the Charge button are still on screen and
    nothing is painted over them (elementFromPoint), at 1024x768 and 1366x768;
  * the figure inside the panel is Change due in the display size (30px) -- and
    smaller than the total, so "the total is the largest money element" holds;
  * a sale paid with the pad / a chip / the keyboard completes and records the
    right change, to the 3rd decimal;
  * the Sale Complete dialog leads with Change due at 40px in the money-in colour,
    and with SIX lines on a 768px till it does not clip: the body scrolls, the
    Print / New Sale / WhatsApp actions stay inside the viewport;
  * the same in Arabic (RTL): the pad keeps its digit order (dir=ltr), the panel
    mirrors, nothing overflows.

Screenshots (cashpanel_*.png, receipt_*.png) go to $AURA_E2E_SHOTS for a human to
LOOK at. Run with the SYSTEM Python:  py -3.14 products/retail/tests/retail_ui1_cash_e2e.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

PAD_KEYS = ["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "00"]

GEOM_JS = r"""() => {
  const q = (s) => document.querySelector(s);
  const vh = innerHeight;
  const hit = (el) => { if (!el) return false; const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.top < 0 || r.bottom > vh + 0.5) return false;
    const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!t && (el === t || el.contains(t)); };
  const fs = (el) => el ? parseFloat(getComputedStyle(el).fontSize) : null;
  const panel = q('#pos-cashpanel');
  const pad = q('#pos-cp-pad');
  const keys = pad ? [...pad.querySelectorAll('.pos-cp-key')].map(k => k.textContent.trim()) : [];
  const kb = pad ? [...pad.querySelectorAll('.pos-cp-key')].map(k => { const r = k.getBoundingClientRect(); return [r.width, r.height]; }) : [];
  const chips = [...document.querySelectorAll('#pos-cp-chips .pos-cp-chip')].map(c => c.innerText.replace(/\s+/g, ' ').trim());
  const pr = panel ? panel.getBoundingClientRect() : null;
  const pane = q('.pos-right').getBoundingClientRect();
  return {
    open: !!panel && !panel.hidden,
    keys, minKey: kb.length ? Math.min(...kb.map(x => Math.min(x[0], x[1]))) : null,
    chips,
    totalHit: hit(q('#pos-total')), chargeHit: hit(q('#pos-checkout-btn')),
    totalFont: fs(q('#pos-total')), figFont: fs(q('#pos-cp-fig')),
    label: q('#pos-cp-lbl') ? q('#pos-cp-lbl').textContent.trim() : null,
    fig: q('#pos-cp-fig') ? q('#pos-cp-fig').textContent.trim() : null,
    state: q('#pos-cp-status') ? q('#pos-cp-status').getAttribute('data-state') : null,
    panelInsidePane: pr ? (pr.left >= pane.left - 1 && pr.right <= pane.right + 1 && pr.bottom <= pane.bottom + 1) : null,
    panelBottom: pr ? pr.bottom : null,
    tenderedType: q('#pos-tendered').type,
    tendered: q('#pos-tendered').value,
    padDir: pad ? getComputedStyle(pad).direction : null,
    docOverflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
}"""

RECEIPT_JS = r"""() => {
  const q = (s) => document.querySelector(s);
  const vh = innerHeight;
  const dlg = q('#ret-receipt-dialog'), body = q('#rcpt-body'), actions = q('.rcpt-actions');
  const fig = q('#rcpt-change-fig');
  const inside = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.top >= -0.5 && r.bottom <= vh + 0.5; };
  const newSale = [...document.querySelectorAll('#ret-receipt-dialog button')].find(b => /New Sale|بيع جديد/.test(b.textContent));
  const wa = q('#rcpt-wa-send-btn');
  const figStyle = fig ? getComputedStyle(fig) : null;
  const lbl = q('.rcpt-change-lbl');
  // colour of the money-in token, resolved the way the browser resolves it
  const probe = document.createElement('span'); probe.style.color = 'var(--text-money-positive)'; document.body.appendChild(probe);
  const green = getComputedStyle(probe).color; probe.remove();
  return {
    dlgBottom: dlg ? dlg.getBoundingClientRect().bottom : null, vh,
    bodyScrolls: body ? body.scrollHeight > body.clientHeight + 1 : null,
    bodyClient: body ? body.clientHeight : null, bodyScroll: body ? body.scrollHeight : null,
    actionsInside: inside(actions), newSaleInside: inside(newSale), waInside: inside(wa),
    figFont: figStyle ? parseFloat(figStyle.fontSize) : null, figColor: figStyle ? figStyle.color : null, green,
    figText: fig ? fig.textContent.trim() : null,
    lblText: lbl ? lbl.textContent.trim() : null,
    figIsFirstBlock: body && fig ? (fig.compareDocumentPosition(body.querySelector('div[style*="surface-sunken"]')) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0 : null,
  };
}"""


def add_lines(page, ids, keys):
    page.evaluate("() => RetailSystem._clearCart()")
    for k in keys:
        page.evaluate("(id) => RetailSystem._addToCart(id)", ids[k] if k in ids else k)
    page.wait_for_timeout(350)


def total_of(page):
    return page.evaluate("() => RetailSystem._currentTotals.total")


def pad(page, *keys):
    for k in keys:
        page.click(f'#pos-cp-pad .pos-cp-key[data-k="{k}"]')
        page.wait_for_timeout(40)


def clear_box(page):
    for _ in range(12):
        page.click("#pos-cp-back")
    assert page.input_value("#pos-tendered") == ""


def main():
    results = {}
    with sync_playwright() as p:
        browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
        ids = B.seed_catalogue(page)
        # a sixth product so a six-line sale can be rung
        _, pr = B.api(page, "POST", "/api/sub/retail/products", {"name": "Sixth Item", "sku": "UI1-6", "sell_price": 4, "cost_price": 2, "unit": "pc"})
        sixth = pr["data"]["id"]
        B.api(page, "POST", f"/api/sub/retail/products/{sixth}/stock-adjust", {"quantity": 100, "reason": "ui1"})
        ids["sixth"] = sixth

        for (w, h) in [(1366, 768), (1024, 768)]:
            for lang in ("en", "ar"):
                tag = f"{w}x{h}_{lang}"
                page.set_viewport_size({"width": w, "height": h})
                page.evaluate("(l) => AuraI18n.setLang(l)", lang)
                page.wait_for_timeout(400)
                B.go_pos(page)

                def opens_on_cash_tile(tag=tag):
                    add_lines(page, ids, ["doohickey"])           # JD 3.000
                    page.click('.pos-pay-btn[data-method="cash"]')
                    page.wait_for_timeout(300)
                    g = page.evaluate(GEOM_JS)
                    results[f"open_{tag}"] = g
                    page.screenshot(path=str(B.SHOTS / f"cashpanel_{tag}.png"))
                    assert g["open"], "the cash panel did not open on the Cash tile"
                    assert g["keys"] == PAD_KEYS, f"pad keys are {g['keys']}"
                    assert g["minKey"] >= 44, f"a pad key is under the 44px touch floor: {g['minKey']}"
                    assert g["chips"][0].startswith(("Exact", "بالضبط")), f"first chip is not Exact: {g['chips']}"
                    assert [c.split()[-1] for c in g["chips"]][1:] == ["5", "10", "20", "50"], f"note chips for a 3.000 total: {g['chips']}"
                    assert g["tenderedType"] == "text", "the amount box is still a number input (browser spinner)"
                    assert g["padDir"] == "ltr", "the pad is mirrored in RTL -- digits must keep their order"
                    assert g["totalHit"], "the TOTAL is covered while the cash panel is open"
                    assert g["chargeHit"], "the Charge button is covered or off-screen while the panel is open"
                    assert g["panelInsidePane"], f"the panel sticks out of the sale pane: {g}"
                    assert g["state"] == "due" and g["fig"], f"panel figure before typing: {g}"
                    assert g["docOverflowX"] <= 1, "horizontal overflow with the panel open"
                    assert g["figFont"] == 30 and g["figFont"] < g["totalFont"], f"panel figure {g['figFont']}px vs total {g['totalFont']}px"
                B.check(f"{tag}: Cash tile opens the panel (12-key pad, Exact + notes, no spinner); total and Charge stay on screen", opens_on_cash_tile)

                def sale_with_pad(tag=tag):
                    clear_box(page)
                    pad(page, "1", "0")
                    g = page.evaluate(GEOM_JS)
                    assert g["tendered"] == "10", f"pad typed {g['tendered']!r}, not '10'"
                    assert g["state"] == "change", g
                    assert "7.000" in g["fig"].replace("٫", "."), f"10 against 3.000 should show 7.000 change, got {g['fig']!r}"
                    page.click("#pos-cp-back")
                    page.click("#pos-cp-back")
                    pad(page, "5", ".", "5", "0", "0", "9")     # 4th decimal ignored
                    assert page.input_value("#pos-tendered") == "5.500", page.input_value("#pos-tendered")
                    g = page.evaluate(GEOM_JS)
                    assert "2.500" in g["fig"].replace("٫", "."), g["fig"]
                    page.screenshot(path=str(B.SHOTS / f"cashpanel_change_{tag}.png"))
                    page.click("#pos-checkout-btn")
                    page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
                    r = page.evaluate(RECEIPT_JS)
                    results[f"receipt_{tag}"] = r
                    page.screenshot(path=str(B.SHOTS / f"receipt_{tag}.png"))
                    assert r["figFont"] == 40, f"Change due on Sale Complete is {r['figFont']}px, not 40"
                    assert r["figColor"] == r["green"], f"Change due is {r['figColor']}, not the money-in colour {r['green']}"
                    assert "2.500" in r["figText"].replace("٫", "."), r["figText"]
                    assert r["figIsFirstBlock"], "Change due does not lead the dialog (it must sit above the items)"
                    sale = page.evaluate("() => RetailSystem._lastSaleData")
                    assert abs(float(sale["change"]) - 2.5) < 1e-9, f"recorded change {sale['change']}"
                    page.click('#ret-receipt-dialog button:has-text("New Sale"), #ret-receipt-dialog button:has-text("بيع جديد")')
                    page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
                B.check(f"{tag}: a full cash sale with the pad (10 -> 7.000 change, 5.500 -> 2.500, 4th decimal ignored); Sale Complete leads with Change due at 40px, green", sale_with_pad)

                def sale_with_chip(tag=tag):
                    add_lines(page, ids, ["gadget", "gizmo"])    # 12.500
                    page.click('.pos-pay-btn[data-method="cash"]')
                    page.wait_for_timeout(250)
                    g = page.evaluate(GEOM_JS)
                    assert g["chips"][0].replace("٫", ".").split()[-1] == "12.500", g["chips"]
                    assert [c.split()[-1] for c in g["chips"]][1:] == ["20", "50"], g["chips"]
                    page.click("#pos-cp-chips .pos-cp-chip >> nth=1")        # the 20 note
                    g = page.evaluate(GEOM_JS)
                    assert g["tendered"] == "20" and "7.500" in g["fig"].replace("٫", "."), g
                    page.click("#pos-cp-chips .pos-cp-chip >> nth=0")        # Exact
                    g = page.evaluate(GEOM_JS)
                    assert g["tendered"] == "12.500" and "0.000" in g["fig"].replace("٫", "."), g
                    page.click("#pos-checkout-btn")
                    page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
                    page.click('#ret-receipt-dialog button:has-text("New Sale"), #ret-receipt-dialog button:has-text("بيع جديد")')
                    page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
                B.check(f"{tag}: chips -- Exact + 20 note on a 12.500 sale; Exact charges with no change", sale_with_chip)

        # ---- the physical keyboard, English 1366x768 --------------------------------
        page.set_viewport_size({"width": 1366, "height": 768})
        page.evaluate("(l) => AuraI18n.setLang(l)", "en")
        page.wait_for_timeout(400)
        B.go_pos(page)

        def keyboard_still_works():
            add_lines(page, ids, ["widget"])                          # 10.000
            page.keyboard.press("Escape")
            page.focus("#pos-search")
            page.keyboard.press("F1")
            page.wait_for_timeout(300)
            g = page.evaluate(GEOM_JS)
            assert g["open"], "F1 did not open the cash panel"
            assert page.evaluate("() => document.activeElement && document.activeElement.id") == "pos-tendered", "F1 did not put the caret in the amount box"
            page.keyboard.type("20")
            g = page.evaluate(GEOM_JS)
            assert g["tendered"] == "20" and "10.000" in g["fig"], g
            page.keyboard.type("x.55555")                             # letters dropped, decimals capped at 3
            assert page.input_value("#pos-tendered") == "20.555", page.input_value("#pos-tendered")
            page.keyboard.press("Escape")
            g = page.evaluate(GEOM_JS)
            assert not g["open"], "Esc did not close the cash panel"
            page.click('.pos-pay-btn[data-method="card"]')
            page.wait_for_timeout(200)
            assert not page.evaluate(GEOM_JS)["open"], "choosing Card left the cash panel open"
            page.click('.pos-pay-btn[data-method="cash"]')
            page.wait_for_timeout(200)
            assert page.evaluate(GEOM_JS)["open"], "choosing Cash again did not reopen the panel"
            page.keyboard.press("Escape")
        B.check("keyboard: F1 opens the panel with the caret in the amount box; typing is sanitised and capped; Esc closes; Card closes it; Cash reopens it", keyboard_still_works)

        # ---- a SIX-line sale on a 768px till: the Sale Complete dialog must not clip ----
        def six_lines_do_not_clip():
            page.set_viewport_size({"width": 1366, "height": 768})
            add_lines(page, ids, ["widget", "gadget", "gizmo", "doohickey", "thingamajig", "sixth"])
            page.click('.pos-pay-btn[data-method="cash"]')
            page.wait_for_timeout(250)
            clear_box(page)
            pad(page, "1", "0", "0")
            page.click("#pos-checkout-btn")
            page.wait_for_selector("#ret-receipt-dialog", timeout=10000)
            page.wait_for_timeout(300)
            r = page.evaluate(RECEIPT_JS)
            results["receipt_six"] = r
            page.screenshot(path=str(B.SHOTS / "receipt_six_lines_1366x768.png"))
            assert r["dlgBottom"] <= r["vh"] + 0.5, f"the dialog runs off the screen: {r}"
            assert r["actionsInside"] and r["newSaleInside"] and r["waInside"], f"Print / New Sale / WhatsApp are cut off: {r}"
            assert r["bodyScrolls"], f"six lines should make the dialog BODY scroll, not the whole dialog: {r}"
            page.click('#ret-receipt-dialog button:has-text("New Sale")')
        B.check("a six-line sale on a 768px till: the Sale Complete body scrolls, the actions (Print / New Sale / WhatsApp) stay inside the viewport", six_lines_do_not_clip)

        browser.close()
    (B.SHOTS / "cash_e2e_measurements.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    B.finish()


main()
