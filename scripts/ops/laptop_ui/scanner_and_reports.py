"""Laptop as the owner: the Barcode Scanner screen ('scanner') with a typed
barcode + Enter, then the Reports screen ('reports'). Prints what each shows."""
import sys
from playwright.sync_api import sync_playwright

BARCODE = sys.argv[1] if len(sys.argv) > 1 else "6281000000011"
SHOTS = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\join-door-shots\rehearsal"


def lines(pg, sel="#subsystem-shell"):
    return [l.strip() for l in pg.locator(sel).inner_text().splitlines() if l.strip()]


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

    pg.evaluate("(s) => SubsystemApp._navigate(s)", "scanner")
    pg.wait_for_timeout(2500)
    print("scanner screen:", [l[:80] for l in lines(pg) if l not in ("Retail & POS", "ActionAura")][18:40])
    # A HID scanner is a keyboard that types the code in a burst and presses
    # Enter, with no field focused. Do exactly that.
    # The arm button is #sc-test-btn ("Start Test"); "Test Scanner" is the
    # section title. Arming calls captureNextScan(), then the burst is read.
    pg.click("#sc-test-btn")
    pg.wait_for_timeout(600)
    print("arm button now reads:", pg.locator("#sc-test-btn").inner_text().strip())
    pg.keyboard.type(BARCODE, delay=8)
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(2000)
    pg.screenshot(path=SHOTS + r"\laptop-scanner-after-scan.png")
    print("scanner test value:", pg.locator("#sc-test-value").input_value())
    print("scanner status lines:", [l[:90] for l in lines(pg) if "Scanner" in l or "detected" in l.lower() or "Waiting" in l][:5])

    pg.evaluate("(s) => SubsystemApp._navigate(s)", "pos")
    pg.wait_for_timeout(2500)
    pg.locator("body").click(position={"x": 700, "y": 400})
    pg.keyboard.type(BARCODE, delay=8)
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(2500)
    pg.screenshot(path=SHOTS + r"\laptop-pos-after-scan.png")
    after = lines(pg)
    print("POS after the burst, lines mentioning the product:", [l[:100] for l in after if "Pepsi" in l or BARCODE in l][:6])

    pg.evaluate("(s) => SubsystemApp._navigate(s)", "reports")
    pg.wait_for_timeout(3500)
    pg.screenshot(path=SHOTS + r"\laptop-reports.png")
    print("reports screen:", [l[:80] for l in lines(pg) if l not in ("Retail & POS", "ActionAura")][18:48])
    b.close()
