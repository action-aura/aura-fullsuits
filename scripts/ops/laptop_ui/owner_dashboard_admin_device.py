"""Owner's dashboard on the demo laptop: how recent sales name the cashier, and
the admin-device banner -- claim it through the real button if it is shown."""
from playwright.sync_api import sync_playwright

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
    pg.wait_for_timeout(3500)
    pg.screenshot(path=SHOTS + r"\laptop-owner-dashboard-before.png")
    text = pg.locator("#subsystem-shell").inner_text()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    print("recent-sales-ish lines:")
    for l in lines:
        if "SALE-" in l or "Recent" in l or "cashier" in l.lower() or "d5580208" in l:
            print("  ", l[:140])
    r = pg.request.get("http://127.0.0.1:5010/api/devices/me")
    print("devices/me before:", r.status, r.text()[:200])
    btn = pg.get_by_role("button", name="Make this the admin device")
    if btn.count():
        print("admin-device banner shown; claiming through the button")
        btn.first.click()
        pg.wait_for_timeout(3000)
        r = pg.request.get("http://127.0.0.1:5010/api/devices/me")
        print("devices/me after:", r.status, r.text()[:200])
        pg.screenshot(path=SHOTS + r"\laptop-owner-dashboard-after-claim.png")
        nav = pg.locator("#subsystem-shell .sub-nav-item").all_text_contents()
        print("nav now:", [n.strip() for n in nav])
    else:
        print("no admin-device banner")
    b.close()
