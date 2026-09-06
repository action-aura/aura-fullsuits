"""Laptop Employees screen as the owner: what the invite form looks like, then
(with 'invite') actually invite a cashier through it and print the setup link."""
import sys
from playwright.sync_api import sync_playwright

MODE = sys.argv[1] if len(sys.argv) > 1 else "look"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "ui-invited-cashier@rehearsal.local"
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
    pg.evaluate("(s) => SubsystemApp._navigate(s)", "employees")
    pg.wait_for_timeout(2500)
    pg.screenshot(path=SHOTS + r"\laptop-employees-screen.png")
    if MODE in ("modal", "invite"):
        pg.get_by_role("button", name="+ Add Employee").click()
        pg.wait_for_timeout(1200)
        pg.screenshot(path=SHOTS + r"\laptop-employees-add-modal.png")
        modal_text = pg.locator("body").inner_text()
        for sel in ("input", "select", "button", "textarea"):
            for el in pg.locator(sel).all():
                try:
                    if not el.is_visible():
                        continue
                    label = el.get_attribute("type") if sel == "input" else (el.inner_text() or "").strip()[:40]
                    print(sel, "|", el.get_attribute("id"), "|", el.get_attribute("placeholder"), "|", label)
                except Exception:  # noqa: BLE001
                    pass
        if MODE == "invite":
            pg.fill("#emp-inv-email", EMAIL)
            pg.select_option("#emp-inv-role", label="Cashier")
            pg.click("#emp-inv-btn")
            pg.wait_for_timeout(2500)
            pg.screenshot(path=SHOTS + r"\laptop-employees-after-invite.png")
            after = pg.locator("body").inner_text()
            links = [l.strip() for l in after.splitlines() if "#setup/" in l or "setup link" in l.lower() or "invite" in l.lower()]
            print("after invite, setup-ish lines:", links[:8])
            link_el = pg.locator("input[value*='#setup/'], a[href*='#setup/'], code, .setup-link, [id*=setup]").first
            try:
                val = link_el.get_attribute("value") or link_el.get_attribute("href") or link_el.inner_text()
                print("setup link element:", (val or "")[:160])
            except Exception as exc:  # noqa: BLE001
                print("no link element found:", str(exc)[:80])
    if MODE == "look":
        for sel in ("input", "select", "button"):
            for el in pg.locator(f"#subsystem-shell {sel}").all()[:25]:
                try:
                    if not el.is_visible():
                        continue
                    print(sel, "|", el.get_attribute("id"), "|", el.get_attribute("placeholder"),
                          "|", (el.inner_text() or "").strip()[:40] if sel != "input" else el.get_attribute("type"))
                except Exception:  # noqa: BLE001
                    pass
        text = pg.locator("#subsystem-shell").inner_text()
        print("---- headings ----")
        for l in [l.strip() for l in text.splitlines() if l.strip()][:30]:
            print("  ", l[:90])
    b.close()
