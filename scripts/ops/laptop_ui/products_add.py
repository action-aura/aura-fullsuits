"""Laptop Products screen as the owner: 'look' lists the add dialog's fields;
'add <name> <sku> <price>' creates a product through the real dialog."""
import sys
from playwright.sync_api import sync_playwright

MODE = sys.argv[1] if len(sys.argv) > 1 else "look"
SHOTS = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\join-door-shots\rehearsal"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:5010/")
    pg.wait_for_selector("#rl-pass, #sub-header-section", timeout=30000)
    if pg.locator("#rl-pass").count():
        pg.fill("#rl-email", "desk-owner@rehearsal.local")
        pg.fill("#rl-pass", "DeskOwner2026!Pass")
        pg.click("#rl-btn")
        pg.wait_for_selector("#sub-header-section", timeout=30000)
    pg.wait_for_timeout(1500)
    pg.evaluate("(s) => SubsystemApp._navigate(s)", "products")
    pg.wait_for_timeout(2500)
    btns = [t.strip() for t in pg.locator("#subsystem-shell button:visible").all_text_contents()]
    print("visible buttons:", [x for x in btns if x][:14])
    add = pg.get_by_role("button", name="+ Add Product")
    if not add.count():
        add = pg.locator("#subsystem-shell button:visible").filter(has_text="Add Product")
    add.first.click()
    pg.wait_for_timeout(1200)
    pg.screenshot(path=SHOTS + r"\laptop-products-add-modal.png")
    for sel in ("input", "select", "textarea"):
        for el in pg.locator(sel).all():
            try:
                if el.is_visible():
                    print(sel, "|", el.get_attribute("id"), "|", el.get_attribute("placeholder"), "|", el.get_attribute("type"))
            except Exception:  # noqa: BLE001
                pass
    print("modal buttons:", [t.strip() for t in pg.locator("button:visible").all_text_contents() if t.strip()][-6:])
    if MODE == "add":
        name, sku, price = sys.argv[2], sys.argv[3], sys.argv[4]
        pg.fill("#pm-name", name)
        pg.fill("#pm-sku", sku)
        pg.fill("#pm-sell", price)
        pg.fill("#pm-cost", "0.5")
        pg.fill("#pm-stock", "10")
        pg.locator("button:visible").filter(has_text="Add Product").last.click()
        pg.wait_for_timeout(2500)
        pg.screenshot(path=SHOTS + r"\laptop-products-after-add.png")
        text = pg.locator("#subsystem-shell").inner_text()
        print("row present:", sku in text, "|", [l.strip() for l in text.splitlines() if sku in l][:2])
    b.close()
