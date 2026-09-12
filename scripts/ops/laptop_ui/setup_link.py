"""Open an employee setup link in a fresh browser (the invited person's own
device) and set the password through the real page.
usage: laptop_setup_link.py <setup-url> <password>"""
import sys
from playwright.sync_api import sync_playwright

URL, PASSWORD = sys.argv[1], sys.argv[2]
SHOTS = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\join-door-shots\rehearsal"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(URL)
    pg.wait_for_timeout(3000)
    pg.screenshot(path=SHOTS + r"\laptop-setup-link-page.png")
    text = pg.locator("body").inner_text()
    print("page says:", [l.strip() for l in text.splitlines() if l.strip()][:8])
    pw = pg.locator("input[type=password]:visible")
    print("password fields:", pw.count())
    for i in range(pw.count()):
        pw.nth(i).fill(PASSWORD)
    names = [x.strip() for x in pg.locator("button:visible").all_text_contents()]
    print("buttons:", names)
    target = None
    for cand in ("Set password", "Set Password", "Save password", "Finish setup", "Create password", "Activate", "Continue", "Save"):
        loc = pg.get_by_role("button", name=cand)
        if loc.count():
            target = loc.first
            break
    if target is None:
        target = pg.locator("button[type=submit]:visible, .auth-submit:visible").first
    target.click()
    pg.wait_for_timeout(3000)
    pg.screenshot(path=SHOTS + r"\laptop-setup-link-done.png")
    after = pg.locator("body").inner_text()
    print("after:", [l.strip() for l in after.splitlines() if l.strip()][:8])
    b.close()
