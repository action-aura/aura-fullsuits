"""Characterize the scanner wedge on the laptop's Barcode Scanner test page:
arm, type a burst at a given inter-key delay, read the captured value.
Prints one line per (barcode, delay)."""
from playwright.sync_api import sync_playwright

CODES = ["6281000000035", "6281000000011", "ABC12345"]
DELAYS = [5, 15, 30, 60, 120]
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
    pg.wait_for_timeout(2000)
    cfg = {k: pg.locator(f"#{k}").input_value() for k in ("sc-timeout", "sc-minlen", "sc-prefix", "sc-suffix")}
    print("scanner settings:", cfg)
    for code in CODES:
        for delay in DELAYS:
            pg.click("#sc-test-btn")
            pg.wait_for_timeout(400)
            # Blur the button so the burst is not "typed into" it.
            pg.locator("body").click(position={"x": 700, "y": 300})
            pg.wait_for_timeout(200)
            pg.keyboard.type(code, delay=delay)
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(700)
            got = pg.locator("#sc-test-value").input_value()
            print(f"{code} @ {delay}ms -> {got!r}  {'OK' if got == code else 'LOST ' + str(len(code) - len(got)) + ' char(s)'}")
    b.close()
