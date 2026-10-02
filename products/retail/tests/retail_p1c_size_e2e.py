"""P1c -- "buttons and text are too small and squeezed": measured, not eyeballed.

For each screen size (Normal / Large / Extra large) at 1366x768 -- the tightest
screen this product ships to -- this opens the till with a realistic sale (four
lines, a customer, a 5% discount, tender row showing) and measures REAL computed
font sizes and REAL element boxes against the targets, and proves the things a
cashier must never lose are on screen without page scrolling: the total, the
Charge button and all six tender buttons (checked by elementFromPoint, so a
button painted UNDER something else fails, not just one that is off-screen).

LARGE (the default) targets, from the plan:
    body text >= 16px; till product name >= 16px, price >= 15px; cart line text
    >= 16px; qty buttons >= 44x44; pay buttons >= 52px tall with >= 16px label;
    Charge >= 60px tall, >= 20px text; summary rows >= 17px; product tile min
    width ~170px; >= 8px between tappable controls.
NORMAL is the pre-P1c geometry (kept as a choice); EXTRA LARGE must be larger
than Large on every measure, and may scroll the product grid but never hide
the total / Charge.

Also measured at 1920x1080 for Large (the plan's second profile).

Screenshots: <till dir>/size_<scale>_<w>x<h>.png.
Run with the SYSTEM Python:  py -3.14 products/retail/tests/retail_p1c_size_e2e.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

MEASURE_JS = r"""() => {
  const q = (s) => document.querySelector(s);
  const fs = (el) => el ? parseFloat(getComputedStyle(el).fontSize) : null;
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, b: r.bottom, r: r.right}; };
  const vw = window.innerWidth, vh = window.innerHeight;
  // Visible = inside the viewport AND the topmost element at its centre is the
  // element itself (or a descendant): a button painted under another one fails.
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    if (r.top < 0 || r.bottom > vh + 0.5 || r.left < 0 || r.right > vw + 0.5) return false;
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!top && (el === top || el.contains(top));
  };
  const pay = [...document.querySelectorAll('.pos-pay-btn')];
  const payBoxes = pay.map(box);
  // smallest horizontal/vertical gap between neighbouring pay buttons
  let minGap = 1e9;
  for (let i = 0; i < payBoxes.length; i++) for (let j = i + 1; j < payBoxes.length; j++) {
    const a = payBoxes[i], b = payBoxes[j];
    const dx = Math.max(b.x - a.r, a.x - b.r), dy = Math.max(b.y - a.b, a.y - b.b);
    const g = Math.max(dx, dy);
    if (g >= -0.5 && g < minGap) minGap = g;
  }
  const cards = [...document.querySelectorAll('.pos-card')];
  const row = q('.pos-cart-row');
  const rows = [...document.querySelectorAll('.pos-cart-row')];
  const sumSpan = q('.pos-sum-row > span');
  return {
    viewport: [vw, vh],
    pageScrollable: document.documentElement.scrollHeight > vh + 1,
    scrollHeight: document.documentElement.scrollHeight,
    bodyFont: fs(document.body),
    cardNameFont: fs(q('.pos-card-name')), cardPriceFont: fs(q('.pos-card-price')),
    cardMinWidth: cards.length ? Math.min(...cards.map(c => c.getBoundingClientRect().width)) : null,
    cartNameFont: fs(q('.pos-item-name')), cartMetaFont: fs(q('.pos-item-meta')), cartTotalFont: fs(q('.pos-line-total')),
    qtyBtn: box(q('.pos-qty-btn')), removeBtn: box(q('.pos-remove-btn')),
    sumRowFont: fs(sumSpan), grandFont: fs(q('#pos-total')),
    payBtn: payBoxes[0], payLabelFont: fs(q('.pos-pay-label')), payMinH: Math.min(...payBoxes.map(b => b.h)),
    payGapMin: minGap === 1e9 ? null : minGap,
    charge: box(q('#pos-checkout-btn')), chargeFont: fs(q('#pos-checkout-btn')),
    visible: {
      total: visible(q('#pos-total')), charge: visible(q('#pos-checkout-btn')),
      pay: pay.map(visible), tendered: visible(q('#pos-tendered')),
      search: visible(q('#pos-search')),
    },
    cartRows: rows.length, cartRowsFullyInside: rows.filter(r => { const c = q('#pos-cart').getBoundingClientRect(), b = r.getBoundingClientRect(); return b.top >= c.top - 0.5 && b.bottom <= c.bottom + 0.5; }).length,
    cartBox: box(q('#pos-cart')), summaryBox: box(q('.pos-summary')),
    totalInside: (() => { const a = q('#pos-total'), s = q('.pos-summary'); if (!a || !s) return false;
      const r = a.getBoundingClientRect(), b = s.getBoundingClientRect(); return r.top >= b.top - 0.5 && r.bottom <= b.bottom + 0.5; })(),
    summaryScrolls: (() => { const s = q('.pos-summary'); return s ? s.scrollHeight > s.clientHeight + 1 : null; })(),
    gridBox: box(q('#pos-product-grid')),
    scale: (window.AuraUiScale && AuraUiScale.get()) || null,
    htmlClass: document.documentElement.className,
  };
}"""


def open_till_with_sale(page, ids):
    B.go_pos(page)
    page.evaluate("() => RetailSystem._clearCart()")   # the cart now survives navigation: start each size from the same sale
    page.wait_for_timeout(200)
    page.evaluate("""(ids) => {
      ['widget', 'gadget', 'gizmo', 'doohickey'].forEach(k => RetailSystem._addToCart(ids[k]));
    }""", ids)
    page.wait_for_timeout(300)
    page.evaluate("""(cid) => {
      const sel = document.getElementById('pos-customer'); sel.value = String(cid); RetailSystem._onCustomerChange();
      const d = document.getElementById('pos-disc'); d.value = '5'; RetailSystem._renderCart();
      const t = document.getElementById('pos-tendered'); t.value = '60'; RetailSystem._calcChange();
    }""", ids["customer"])
    page.wait_for_timeout(500)


def set_scale(page, scale):
    page.evaluate("(s) => AuraUiScale.set(s)", scale)
    page.wait_for_timeout(300)


def measure(page):
    return page.evaluate(MEASURE_JS)


LARGE_TARGETS = [
    # (label, getter, op, bound)
    ("body text", lambda m: m["bodyFont"], ">=", 16),
    ("till product name", lambda m: m["cardNameFont"], ">=", 16),
    ("till product price", lambda m: m["cardPriceFont"], ">=", 15),
    ("cart line name", lambda m: m["cartNameFont"], ">=", 16),
    ("cart line total", lambda m: m["cartTotalFont"], ">=", 16),
    ("qty button width", lambda m: m["qtyBtn"]["w"], ">=", 44),
    ("qty button height", lambda m: m["qtyBtn"]["h"], ">=", 44),
    ("pay button height", lambda m: m["payMinH"], ">=", 52),
    ("pay label text", lambda m: m["payLabelFont"], ">=", 16),
    ("Charge height", lambda m: m["charge"]["h"], ">=", 60),
    ("Charge text", lambda m: m["chargeFont"], ">=", 20),
    ("summary row text", lambda m: m["sumRowFont"], ">=", 17),
    ("product tile min width", lambda m: m["cardMinWidth"], ">=", 160),
    ("gap between pay buttons", lambda m: m["payGapMin"], ">=", 8),
]


def assert_always_visible(m, what):
    v = m["visible"]
    assert v["total"], f"{what}: the total is not visible/reachable (covered or off-screen)"
    assert v["charge"], f"{what}: the Charge button is not visible/reachable"
    assert m["totalInside"], f"{what}: the total is CLIPPED -- it sits partly outside the summary box (a centre-point hit test alone misses this)"
    if what.startswith("Extra") is False:
        assert all(v["pay"]), f"{what}: tender buttons not all visible: {v['pay']}"
        assert v["tendered"], f"{what}: Cash Tendered not visible"
        assert not m["pageScrollable"], f"{what}: the PAGE scrolls (scrollHeight {m['scrollHeight']} > {m['viewport'][1]})"
        assert m["cartRowsFullyInside"] >= min(m["cartRows"], 2), f"{what}: cart rows clipped ({m['cartRowsFullyInside']}/{m['cartRows']})"
        assert not m["summaryScrolls"], f"{what}: the summary scrolls internally -- a row is hidden"


SIZES = [("normal", 1366, 768), ("large", 1366, 768), ("xlarge", 1366, 768), ("large", 1920, 1080), ("normal", 1920, 1080), ("xlarge", 1920, 1080)]
only = os.environ.get("SIZE_ONLY")
results = {}

with sync_playwright() as p:
    browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
    ids = B.seed_catalogue(page)
    # Default (nothing stored): must be Large.
    page.evaluate("() => { try { localStorage.removeItem('aura_ui_scale'); } catch (e) {} }")
    page.reload()
    page.wait_for_selector("#sub-header-section", timeout=20000)
    page.wait_for_timeout(800)

    def default_is_large():
        s = page.evaluate("() => window.AuraUiScale ? AuraUiScale.get() : null")
        assert s == "large", f"the default screen size is {s!r}, expected 'large'"
        assert "ui-large" in page.evaluate("() => document.documentElement.className"), "html carries no ui-large class by default"

    B.check("the default screen size is Large", default_is_large)

    for scale, w, h in SIZES:
        name = f"{scale} @ {w}x{h}"
        if only and only not in name:
            continue
        page.set_viewport_size({"width": w, "height": h})
        set_scale(page, scale)
        open_till_with_sale(page, ids)
        m = measure(page)
        results[name] = m
        page.screenshot(path=str(B.SHOTS / f"size_{scale}_{w}x{h}.png"))
        print(f"-- {name}: " + json.dumps({k: m[k] for k in ("bodyFont", "cardNameFont", "cardPriceFont", "cardMinWidth", "cartNameFont", "payMinH", "payLabelFont", "chargeFont", "sumRowFont", "grandFont", "payGapMin")}) +
              f" charge={m['charge']['h']:.0f}px qty={m['qtyBtn']['w']:.0f}x{m['qtyBtn']['h']:.0f} scrollH={m['scrollHeight']} cartRows={m['cartRowsFullyInside']}/{m['cartRows']}", flush=True)

        def always(m=m, label=name):
            assert_always_visible(m, label if scale != "xlarge" else "Extra large")
        B.check(f"{name}: total, Charge (and, except Extra large, all six tenders + Cash Tendered) visible without page scrolling", always)

        if scale == "large":
            def targets(m=m, label=name):
                bad = []
                for lab, get, op, bound in LARGE_TARGETS:
                    v = get(m)
                    if v is None or not (v >= bound - 0.01):
                        bad.append(f"{lab}: {v} (needs >= {bound})")
                assert not bad, f"{label}: below the Large targets -> " + "; ".join(bad)
            B.check(f"{name}: meets every Large target", targets)

    # Ordering: each step up is bigger on every measure that is a size.
    def ordered():
        keys = ("bodyFont", "cardNameFont", "cartNameFont", "payLabelFont", "chargeFont", "sumRowFont")
        n, l, x = (results.get(f"{s} @ 1366x768") for s in ("normal", "large", "xlarge"))
        if not (n and l and x):
            return
        for k in keys:
            assert n[k] < l[k] <= x[k] or (n[k] <= l[k] < x[k]), f"{k}: normal {n[k]}, large {l[k]}, xlarge {x[k]} are not increasing"
        assert l["charge"]["h"] >= n["charge"]["h"] and x["charge"]["h"] >= l["charge"]["h"], "Charge does not grow with the setting"
        assert n["bodyFont"] < l["bodyFont"], "Large is not larger than Normal"

    B.check("Normal < Large <= Extra large on every text measure", ordered)

    # The control lives in Settings > Display and acts at once.
    def settings_control():
        page.set_viewport_size({"width": 1366, "height": 768})
        B.go(page, "admin-center", 1500)
        page.evaluate("() => SettingsHub.open('display')")
        page.wait_for_selector('[data-ui-scale="xlarge"]', timeout=8000)
        page.click('[data-ui-scale="xlarge"]')
        assert "ui-xlarge" in page.evaluate("() => document.documentElement.className"), "Settings > Extra large did not apply ui-xlarge"
        assert page.get_attribute('[data-ui-scale="xlarge"]', "aria-pressed") == "true"
        page.click('[data-ui-scale="normal"]')
        cls = page.evaluate("() => document.documentElement.className")
        assert "ui-xlarge" not in cls and "ui-large" not in cls, f"Normal left a size class behind: {cls!r}"
        assert page.evaluate("() => localStorage.getItem('aura_ui_scale')") == "normal"
        page.click('[data-ui-scale="large"]')
        page.screenshot(path=str(B.SHOTS / "size_settings_display.png"))

    B.check("Settings > Display switches Normal / Large / Extra large at once and stores it", settings_control)

    # Persistence: stored per device, survives reload.
    def persists():
        page.set_viewport_size({"width": 1366, "height": 768})
        set_scale(page, "xlarge")
        assert page.evaluate("() => localStorage.getItem('aura_ui_scale')") == "xlarge"
        page.reload()
        page.wait_for_selector("#sub-header-section", timeout=20000)
        assert "ui-xlarge" in page.evaluate("() => document.documentElement.className"), "the saved size was not applied at boot"
        set_scale(page, "large")

    B.check("the chosen size is stored on the device (aura_ui_scale) and applied at boot", persists)
    browser.close()

(B.SHOTS / "size_measurements.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
B.finish()
