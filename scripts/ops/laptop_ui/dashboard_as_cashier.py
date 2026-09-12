"""What the laptop's dashboard shows a CASHIER (who has no reports capability)."""
from playwright.sync_api import sync_playwright

OUT = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\join-door-shots\rehearsal\laptop-dashboard-as-cashier.png"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:5010/")
    pg.wait_for_selector("#rl-pass, #sub-header-section", timeout=30000)
    if pg.locator("#rl-pass").count():
        pg.fill("#rl-email", "synced-cashier-1788568313@rehearsal.local")
        pg.fill("#rl-pass", "Cashier2026!Pass")
        pg.click("#rl-btn")
        pg.wait_for_selector("#sub-header-section", timeout=30000)
    pg.wait_for_timeout(3000)
    pg.screenshot(path=OUT)
    body = pg.locator("#subsystem-shell, body").first.inner_text()
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    print("visible text (first 40 lines):")
    for l in lines[:40]:
        print("  ", l[:100])
    b.close()
print(OUT)
